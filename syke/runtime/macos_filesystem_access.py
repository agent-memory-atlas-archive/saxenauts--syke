"""macOS protected-folder consent checks for background Syke."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.runtime.workspace import CONTROL_ROOT, RUNTIME_DIR, WORKSPACE_ROOT

PROTECTED_FOLDER_NAMES = ("Desktop", "Documents", "Downloads")
ACCESS_STATE_PATH = CONTROL_ROOT / "filesystem-access.json"
ACCESS_RUN_ROOT = CONTROL_ROOT / "filesystem-access-runs"
LAUNCHD_LABEL_PREFIX = "com.syke.filesystem-access"

_NODE_PROBE_SCRIPT = r"""
import { readdir } from "node:fs/promises";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const folders = {};

for (const folder of request.folders) {
  try {
    await readdir(folder.path);
    folders[folder.name] = {
      ok: true,
      status: "granted",
      path: folder.path,
      error_code: null,
      error: null,
    };
  } catch (error) {
    const code = error && error.code ? String(error.code) : null;
    let status = "error";
    if (code === "EPERM" || code === "EACCES") status = "denied";
    if (code === "ENOENT") status = "missing";
    folders[folder.name] = {
      ok: status === "missing",
      status,
      path: folder.path,
      error_code: code,
      error: String(error && error.message ? error.message : error),
    };
  }
}

process.stdout.write(JSON.stringify({ folders }));
""".strip()


def protected_home_folders(*, home: Path | None = None) -> tuple[Path, ...]:
    resolved_home = (home or Path.home()).expanduser().resolve()
    return tuple((resolved_home / name).resolve() for name in PROTECTED_FOLDER_NAMES)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _executable_identity(path: Path) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(path.expanduser()),
        "resolved_path": str(resolved),
        "sha256": _sha256(resolved),
    }


def _python_from_runtime() -> Path:
    from syke.runtime.locator import resolve_background_syke_runtime

    runtime = resolve_background_syke_runtime()
    if runtime.mode == "python_module":
        return Path(runtime.syke_command[0]).expanduser().resolve()

    target = runtime.target_path or Path(runtime.syke_command[0])
    first_line = target.open("rb").readline().decode("utf-8", errors="replace").strip()
    if not first_line.startswith("#!"):
        raise RuntimeError(f"Syke console script has no interpreter shebang: {target}")
    parts = shlex.split(first_line[2:])
    if not parts:
        raise RuntimeError(f"Syke console script has an empty interpreter shebang: {target}")
    if Path(parts[0]).name == "env":
        if len(parts) < 2:
            raise RuntimeError(f"Unsupported Syke interpreter shebang: {first_line}")
        resolved = shutil.which(parts[1])
        if resolved is None:
            raise RuntimeError(f"Could not resolve Syke interpreter: {parts[1]}")
        return Path(resolved).resolve()
    return Path(parts[0]).expanduser().resolve()


def _current_runtime_identity() -> dict[str, dict[str, str]]:
    from syke.llm.pi_client import PI_NODE_BIN

    if not PI_NODE_BIN.is_file():
        raise RuntimeError(f"Syke Node runtime is missing: {PI_NODE_BIN}")
    return {
        "python": _executable_identity(_python_from_runtime()),
        "node": _executable_identity(PI_NODE_BIN),
    }


def _worker_runtime_identity(node: Path) -> dict[str, dict[str, str]]:
    return {
        "python": _executable_identity(Path(sys.executable)),
        "node": _executable_identity(node),
    }


def _runtime_identities_match(left: object, right: object) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for runtime_name in ("python", "node"):
        left_runtime = left.get(runtime_name)
        right_runtime = right.get(runtime_name)
        if not isinstance(left_runtime, dict) or not isinstance(right_runtime, dict):
            return False
        for key in ("resolved_path", "sha256"):
            if left_runtime.get(key) != right_runtime.get(key):
                return False
    return True


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _folder_summary(folders: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    granted: list[str] = []
    blocked: list[str] = []
    for name in PROTECTED_FOLDER_NAMES:
        raw = folders.get(name)
        item = raw if isinstance(raw, dict) else {}
        status = item.get("status")
        if status in {"granted", "missing"}:
            granted.append(name)
        else:
            blocked.append(name)
    return not blocked, granted, blocked


def _detail_for_state(payload: dict[str, Any], *, identity_matches: bool = True) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error:
        return f"protected-folder check failed: {error}"
    if not identity_matches:
        return "Syke's background runtime changed; run `syke doctor` in a terminal to verify access"
    folders = payload.get("folders")
    folder_payload = folders if isinstance(folders, dict) else {}
    ok, _granted, blocked = _folder_summary(folder_payload)
    if ok:
        return "Desktop, Documents, and Downloads verified for background Syke"
    if blocked:
        names = ", ".join(blocked)
        return (
            f"blocked: {names}. Open System Settings > Privacy & Security > Files & Folders, "
            "enable access for Python or Syke, then run `syke doctor`"
        )
    return "protected folders have not been checked"


def macos_filesystem_access_status() -> dict[str, Any]:
    """Return the last background verification without causing a macOS prompt."""
    if sys.platform != "darwin":
        return {
            "applicable": False,
            "ok": True,
            "status": "not_applicable",
            "detail": "macOS protected-folder consent is not required on this platform",
            "folders": {},
        }

    payload = _read_json(ACCESS_STATE_PATH)
    if payload is None:
        return {
            "applicable": True,
            "ok": False,
            "status": "not_checked",
            "detail": "protected folders have not been checked; `syke setup` requests access",
            "folders": {},
        }

    try:
        identity_matches = _runtime_identities_match(
            payload.get("runtime_identity"),
            _current_runtime_identity(),
        )
    except (OSError, RuntimeError):
        identity_matches = False
    folders = payload.get("folders")
    folder_payload = folders if isinstance(folders, dict) else {}
    folders_ok, _granted, _blocked = _folder_summary(folder_payload)
    ok = folders_ok and identity_matches
    return {
        **payload,
        "applicable": True,
        "ok": ok,
        "status": "granted" if ok else "stale" if not identity_matches else "blocked",
        "identity_matches": identity_matches,
        "detail": _detail_for_state(payload, identity_matches=identity_matches),
    }


def _validated_request_folders(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_folders = payload.get("folders")
    if not isinstance(raw_folders, list):
        raise ValueError("macOS filesystem probe request has no folder list")
    expected = {path.name: path for path in protected_home_folders()}
    folders: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_folders:
        if not isinstance(raw, dict):
            raise ValueError("macOS filesystem probe folder is not an object")
        name = raw.get("name")
        path_text = raw.get("path")
        if not isinstance(name, str) or name not in expected or name in seen:
            raise ValueError(f"invalid macOS protected-folder category: {name!r}")
        if (
            not isinstance(path_text, str)
            or Path(path_text).expanduser().resolve() != expected[name]
        ):
            raise ValueError(f"invalid macOS protected-folder path for {name}")
        seen.add(name)
        folders.append({"name": name, "path": str(expected[name])})
    return folders


def run_probe_worker(request_path: Path, result_path: Path) -> int:
    """Run the sandboxed Node folder check inside the one-shot LaunchAgent."""
    from syke.llm.pi_client import ensure_node_binary
    from syke.runtime.sandbox import generate_seatbelt_profile

    request = _read_json(request_path)
    if request is None:
        raise ValueError(f"Could not read macOS filesystem probe request: {request_path}")
    folders = _validated_request_folders(request)
    node = ensure_node_binary()
    profile_path = result_path.with_suffix(".sb")
    profile_path.write_text(
        generate_seatbelt_profile(
            WORKSPACE_ROOT,
            control_root=CONTROL_ROOT,
            runtime_root=RUNTIME_DIR,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            str(profile_path),
            str(node),
            "--input-type=module",
            "-e",
            _NODE_PROBE_SCRIPT,
        ],
        input=json.dumps({"folders": folders}),
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        node_result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        node_result = {
            "folders": {},
            "error": completed.stderr.strip() or "filesystem probe returned invalid JSON",
        }
    result = {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "runtime_identity": _worker_runtime_identity(node),
        "folders": node_result.get("folders") if isinstance(node_result, dict) else {},
        "worker_returncode": completed.returncode,
    }
    if isinstance(node_result, dict) and node_result.get("error"):
        result["error"] = node_result["error"]
    _write_json(result_path, result)
    return 0 if completed.returncode == 0 else 1


def _job_payload(
    *,
    label: str,
    launcher: Path,
    user_id: str,
    request_path: Path,
    result_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    return {
        "Label": label,
        "ProgramArguments": [
            str(launcher),
            "--user",
            user_id,
            "_macos-filesystem-probe",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ],
        "RunAtLoad": True,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def _run_launchd_probe(
    *,
    launcher: Path,
    user_id: str,
    run_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    run_id = run_dir.name
    label = f"{LAUNCHD_LABEL_PREFIX}.{run_id}"
    request_path = run_dir / "request.json"
    result_path = run_dir / "result.json"
    log_path = run_dir / "probe.log"
    plist_path = run_dir / "probe.plist"
    _write_json(
        request_path,
        {
            "schema_version": 1,
            "folders": [
                {"name": path.name, "path": str(path)} for path in protected_home_folders()
            ],
        },
    )
    with plist_path.open("wb") as handle:
        plistlib.dump(
            _job_payload(
                label=label,
                launcher=launcher,
                user_id=user_id,
                request_path=request_path,
                result_path=result_path,
                log_path=log_path,
            ),
            handle,
            sort_keys=False,
        )

    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{label}"
    bootstrap = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if bootstrap.returncode != 0:
        detail = bootstrap.stderr.strip() or bootstrap.stdout.strip()
        raise RuntimeError(f"Could not start the macOS folder check: {detail}")

    started = time.time()
    next_status_check = started + 0.5
    try:
        while time.time() - started < timeout:
            result = _read_json(result_path)
            if result is not None:
                return result
            now = time.time()
            if now >= next_status_check:
                status = subprocess.run(
                    ["launchctl", "print", service],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                status_text = status.stdout.lower()
                if "state = exited" in status_text:
                    try:
                        detail = log_path.read_text(encoding="utf-8").strip()
                    except OSError:
                        detail = ""
                    raise RuntimeError(
                        "macOS folder check exited without a result"
                        + (f": {detail[-1000:]}" if detail else "")
                    )
                next_status_check = now + 0.5
            time.sleep(0.1)
        raise TimeoutError(f"macOS folder check did not finish within {timeout:.0f} seconds")
    finally:
        subprocess.run(
            ["launchctl", "bootout", service],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )


def run_macos_filesystem_access_check(
    user_id: str,
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Request and verify protected-folder access through background Syke."""
    if sys.platform != "darwin":
        return macos_filesystem_access_status()
    if timeout <= 0:
        raise ValueError("macOS filesystem access timeout must be positive")

    from syke.runtime.locator import ensure_syke_launcher, resolve_background_syke_runtime

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = ACCESS_RUN_ROOT / run_id

    try:
        runtime = resolve_background_syke_runtime()
        launcher = ensure_syke_launcher(runtime)
        run_dir.mkdir(parents=True, exist_ok=False)
        payload = _run_launchd_probe(
            launcher=launcher,
            user_id=user_id,
            run_dir=run_dir,
            timeout=timeout,
        )
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "checked_at": datetime.now(UTC).isoformat(),
            "runtime_identity": None,
            "folders": {},
            "error": str(exc),
        }
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    folders = payload.get("folders")
    folder_payload = folders if isinstance(folders, dict) else {}
    ok, _granted, _blocked = _folder_summary(folder_payload)
    ok = ok and not bool(payload.get("error"))
    result = {
        **payload,
        "applicable": True,
        "ok": ok,
        "status": "granted" if ok else "blocked",
        "detail": _detail_for_state(payload),
    }
    _write_json(ACCESS_STATE_PATH, result)
    return result
