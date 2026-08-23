"""Pi and Node installation owned by Syke."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("syke.llm.pi_client")

PI_PACKAGE = "@earendil-works/pi-coding-agent"

PI_PACKAGE_VERSION = "0.84.1"

PI_PACKAGE_SPEC = f"{PI_PACKAGE}@{PI_PACKAGE_VERSION}"

PI_SCHEMA_PACKAGE = "typebox"

PI_SCHEMA_VERSION = "1.3.7"

PI_SCHEMA_SPEC = f"{PI_SCHEMA_PACKAGE}@{PI_SCHEMA_VERSION}"

PI_LOCAL_PREFIX = Path.home() / ".syke" / "pi"

PI_BIN = Path.home() / ".syke" / "bin" / "pi"

PI_NODE_BIN = Path.home() / ".syke" / "bin" / "node"

PI_PACKAGE_ROOT = PI_LOCAL_PREFIX / "node_modules" / "@earendil-works" / "pi-coding-agent"

PI_CLI_JS = PI_PACKAGE_ROOT / "dist" / "cli.js"

PI_TOOL_EXTENSION = PI_LOCAL_PREFIX / "syke-tools.mjs"

PI_TOOL_EXTENSION_SOURCE = Path(__file__).resolve().parents[1] / "runtime" / "pi_tools.mjs"

_NODE_CANDIDATES = [
    Path("/opt/homebrew/bin/node"),
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
]

_NPM_CANDIDATES = [
    Path("/opt/homebrew/bin/npm"),
    Path("/usr/local/bin/npm"),
    Path("/usr/bin/npm"),
]

_MINIMUM_NODE_VERSION = (22, 19, 0)

_NODE_REQUIREMENT = "Node.js 22.19+ with Zstandard support"


def _find_executable(name: str, candidates: list[Path]) -> Path | None:
    resolved = shutil.which(name)
    if resolved:
        path = Path(resolved).expanduser().resolve()
        if path.exists() and os.access(path, os.X_OK):
            return path

    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _ensure_symlink(link_path: Path, target_path: Path) -> Path:
    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.is_symlink():
        try:
            if link_path.resolve() == target_path.resolve() and os.access(link_path, os.X_OK):
                return link_path
        except OSError:
            pass
        link_path.unlink()
    elif link_path.exists():
        if link_path.resolve() == target_path.resolve() and os.access(link_path, os.X_OK):
            return link_path
        link_path.unlink()

    link_path.symlink_to(target_path)
    return link_path


def _node_version_text(node: Path) -> str:
    try:
        result = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "unknown version"
    version = (result.stdout or result.stderr).strip()
    return version or "unknown version"


def _node_supports_pi_runtime(node: Path) -> tuple[bool, str]:
    version_text = _node_version_text(node)
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version_text)
    if match is None:
        return False, version_text
    version = tuple(int(part) for part in match.groups())
    if version < _MINIMUM_NODE_VERSION:
        return False, version_text

    try:
        result = subprocess.run(
            [
                str(node),
                "-e",
                "new RegExp('', 'v');"
                "const zlib = require('node:zlib');"
                "if (typeof zlib.createZstdDecompress !== 'function') {"
                "throw new Error('Node.js Zstandard support is unavailable');"
                "}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, version_text
    detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return False, f"{version_text}: {detail[:300]}"


def ensure_node_binary() -> Path:
    """Return a stable absolute Node path Syke can use outside shell-managed PATH."""
    if PI_NODE_BIN.exists() and os.access(PI_NODE_BIN, os.X_OK):
        supported, detail = _node_supports_pi_runtime(PI_NODE_BIN)
        if supported:
            return PI_NODE_BIN
        if PI_NODE_BIN.is_symlink():
            PI_NODE_BIN.unlink()
        else:
            raise RuntimeError(f"Syke's Pi runtime requires {_NODE_REQUIREMENT}. Found {detail}")

    candidates: list[Path] = []
    resolved = shutil.which("node")
    if resolved:
        candidates.append(Path(resolved).expanduser().resolve())
    candidates.extend(candidate.resolve() for candidate in _NODE_CANDIDATES if candidate.exists())

    seen: set[Path] = set()
    unsupported: list[str] = []
    for node in candidates:
        if node in seen or not os.access(node, os.X_OK):
            continue
        seen.add(node)
        supported, detail = _node_supports_pi_runtime(node)
        if supported:
            return _ensure_symlink(PI_NODE_BIN, node)
        unsupported.append(detail)

    if unsupported:
        raise RuntimeError(
            f"Syke's Pi runtime requires {_NODE_REQUIREMENT}. Found {', '.join(unsupported)}"
        )
    raise RuntimeError(
        f"Syke's Pi runtime requires {_NODE_REQUIREMENT}. Install from https://nodejs.org"
    )


def _resolve_npm_binary() -> str:
    npm = _find_executable("npm", _NPM_CANDIDATES)
    if npm is None:
        raise RuntimeError(
            "Syke's Pi runtime requires npm to install Pi locally. Install Node.js from "
            "https://nodejs.org"
        )
    return str(npm)


def _write_pi_launcher(node_bin: Path) -> Path:
    """Write the stable Pi launcher Syke uses for shell and daemon paths."""
    if not PI_CLI_JS.exists():
        raise RuntimeError(f"Pi CLI entrypoint not found at {PI_CLI_JS}")

    PI_BIN.parent.mkdir(parents=True, exist_ok=True)
    if PI_BIN.is_symlink():
        PI_BIN.unlink()
    elif PI_BIN.exists() and not PI_BIN.is_file():
        PI_BIN.unlink()
    launcher = f'#!/bin/sh\nexec "{node_bin}" "{PI_CLI_JS}" "$@"\n'
    PI_BIN.write_text(launcher, encoding="utf-8")
    PI_BIN.chmod(PI_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return PI_BIN


def _package_path(prefix: Path, package: str) -> Path:
    return prefix / "node_modules" / Path(*package.split("/"))


def _read_package_manifest(package_root: Path) -> dict[str, Any]:
    manifest_path = package_root / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid package metadata at {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Invalid package metadata at {manifest_path}")
    return manifest


def _validate_pi_install(prefix: Path) -> None:
    root_manifest = _read_package_manifest(prefix)
    dependencies = root_manifest.get("dependencies")
    if not isinstance(dependencies, dict):
        raise RuntimeError(f"Pi runtime dependencies are missing at {prefix / 'package.json'}")
    expected_dependencies = {
        PI_PACKAGE: PI_PACKAGE_VERSION,
        PI_SCHEMA_PACKAGE: PI_SCHEMA_VERSION,
    }
    for package, expected_version in expected_dependencies.items():
        if dependencies.get(package) != expected_version:
            raise RuntimeError(
                f"Pi runtime must pin {package} at {expected_version}; "
                f"found {dependencies.get(package)!r}"
            )

        manifest = _read_package_manifest(_package_path(prefix, package))
        if manifest.get("name") != package or manifest.get("version") != expected_version:
            raise RuntimeError(
                f"Pi runtime package {package} must be {expected_version}; "
                f"found {manifest.get('name')!r} {manifest.get('version')!r}"
            )

    cli = _package_path(prefix, PI_PACKAGE) / "dist" / "cli.js"
    if not cli.is_file():
        raise RuntimeError(f"Pi CLI entrypoint not found at {cli}")


def _installed_pi_version(prefix: Path) -> str | None:
    try:
        manifest = _read_package_manifest(_package_path(prefix, PI_PACKAGE))
    except RuntimeError:
        return None
    version = manifest.get("version")
    return version if manifest.get("name") == PI_PACKAGE and isinstance(version, str) else None


def _version_tuple(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:$|[-+])", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _remove_install_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _install_pi_runtime(node_bin: Path) -> None:
    installed_version = _installed_pi_version(PI_LOCAL_PREFIX)
    installed_tuple = _version_tuple(installed_version) if installed_version else None
    pinned_tuple = _version_tuple(PI_PACKAGE_VERSION)
    if installed_tuple and pinned_tuple and installed_tuple > pinned_tuple:
        raise RuntimeError(
            f"Pi {installed_version} is newer than Syke's tested version {PI_PACKAGE_VERSION}. "
            "Syke will not replace it with an older version automatically."
        )

    npm = _resolve_npm_binary()
    PI_LOCAL_PREFIX.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{PI_LOCAL_PREFIX.name}.staging-",
            dir=PI_LOCAL_PREFIX.parent,
        )
    )
    backup = PI_LOCAL_PREFIX.with_name(
        f".{PI_LOCAL_PREFIX.name}.backup-{os.getpid()}-{time.time_ns()}"
    )
    install_env = dict(os.environ)
    install_env["PATH"] = os.pathsep.join(
        part for part in (str(node_bin.parent), install_env.get("PATH", "")) if part
    )

    try:
        result = subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(staging),
                "--save-exact",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                PI_PACKAGE_SPEC,
                PI_SCHEMA_SPEC,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env=install_env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to install Pi runtime: {result.stderr.strip()[:500]}")
        _validate_pi_install(staging)

        if PI_LOCAL_PREFIX.exists() or PI_LOCAL_PREFIX.is_symlink():
            PI_LOCAL_PREFIX.rename(backup)
        try:
            staging.rename(PI_LOCAL_PREFIX)
        except Exception:
            if backup.exists() or backup.is_symlink():
                backup.rename(PI_LOCAL_PREFIX)
            raise
        _remove_install_path(backup)
    finally:
        _remove_install_path(staging)


def ensure_pi_binary() -> str:
    """Install Pi locally under ~/.syke/ and return a stable launcher path."""
    node_bin = ensure_node_binary()

    try:
        _validate_pi_install(PI_LOCAL_PREFIX)
    except RuntimeError:
        logger.info("Installing Pi %s to %s", PI_PACKAGE_VERSION, PI_LOCAL_PREFIX)
        _install_pi_runtime(node_bin)
        _validate_pi_install(PI_LOCAL_PREFIX)

    if PI_CLI_JS.exists():
        _write_pi_launcher(node_bin)
        return str(PI_BIN)
    raise RuntimeError(f"Pi CLI entrypoint not found after install at {PI_CLI_JS}")


def _install_pi_tool_extension() -> Path:
    """Install Syke's trusted tool broker beside Pi for package resolution."""
    if not PI_TOOL_EXTENSION_SOURCE.is_file():
        raise RuntimeError(f"Syke Pi tool extension not found at {PI_TOOL_EXTENSION_SOURCE}")
    PI_LOCAL_PREFIX.mkdir(parents=True, exist_ok=True)
    source = PI_TOOL_EXTENSION_SOURCE.read_bytes()
    if not PI_TOOL_EXTENSION.exists() or PI_TOOL_EXTENSION.read_bytes() != source:
        PI_TOOL_EXTENSION.write_bytes(source)
    return PI_TOOL_EXTENSION


def resolve_pi_binary() -> str:
    """Find or install the Pi binary at ~/.syke/bin/pi."""
    return ensure_pi_binary()


def get_pi_version(*, install: bool = False, minimal_env: bool = False, timeout: int = 10) -> str:
    """Return Pi version through Syke's stable launcher.

    When ``minimal_env`` is true, simulate a launchd-style cold environment with
    a stripped PATH to catch shell-dependent runtime failures.
    """
    launcher = Path(ensure_pi_binary() if install else PI_BIN)
    _validate_pi_install(PI_LOCAL_PREFIX)
    if not launcher.exists():
        raise FileNotFoundError(f"Pi launcher not found at {launcher}")

    env: dict[str, str] | None = None
    if minimal_env:
        env = {
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }

    result = subprocess.run(
        [str(launcher), "--version"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(detail[:500])
    return result.stdout.strip() or result.stderr.strip() or "unknown"
