from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from syke.runtime import macos_filesystem_access as access


def _identity(name: str) -> dict[str, dict[str, str]]:
    return {
        "python": {"resolved_path": f"/{name}/python", "sha256": name},
        "node": {"resolved_path": f"/{name}/node", "sha256": name},
    }


def _granted_folders(home: Path) -> dict[str, dict[str, object]]:
    return {
        name: {
            "ok": True,
            "status": "granted",
            "path": str(home / name),
            "error_code": None,
            "error": None,
        }
        for name in access.PROTECTED_FOLDER_NAMES
    }


def test_launchd_probe_timeout_uses_wall_clock(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "probe-run"
    run_dir.mkdir()
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    wall_times = iter([100.0, 106.0])
    monkeypatch.setattr(access.subprocess, "run", run)
    monkeypatch.setattr(access.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(
        access.time,
        "monotonic",
        lambda: (_ for _ in ()).throw(AssertionError("monotonic deadline used")),
    )
    monkeypatch.setattr(access.time, "sleep", lambda _delay: None)

    with pytest.raises(TimeoutError, match="within 5 seconds"):
        access._run_launchd_probe(
            launcher=tmp_path / "syke",
            user_id="test",
            run_dir=run_dir,
            timeout=5.0,
        )

    assert commands[0][:2] == ["launchctl", "bootstrap"]
    assert commands[-1][:2] == ["launchctl", "bootout"]


def test_status_grant_requires_current_runtime_identity(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "filesystem-access.json"
    monkeypatch.setattr(access.sys, "platform", "darwin")
    monkeypatch.setattr(access, "ACCESS_STATE_PATH", state_path)
    for stored, current, expected_ok, expected_status in (
        ("same", "same", True, "granted"),
        ("old", "new", False, "stale"),
    ):
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checked_at": "2026-08-15T00:00:00+00:00",
                    "runtime_identity": _identity(stored),
                    "folders": _granted_folders(tmp_path),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            access, "_current_runtime_identity", lambda value=current: _identity(value)
        )

        status = access.macos_filesystem_access_status()

        assert status["ok"] is expected_ok
        assert status["status"] == expected_status
        assert status["identity_matches"] is expected_ok


def test_run_check_persists_denied_folder_without_file_names(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    run_root = tmp_path / "runs"
    home = tmp_path / "home"
    monkeypatch.setattr(access.sys, "platform", "darwin")
    monkeypatch.setattr(access, "ACCESS_STATE_PATH", state_path)
    monkeypatch.setattr(access, "ACCESS_RUN_ROOT", run_root)
    monkeypatch.setattr(
        "syke.runtime.locator.resolve_background_syke_runtime",
        lambda: SimpleNamespace(mode="external_cli"),
    )
    monkeypatch.setattr(
        "syke.runtime.locator.ensure_syke_launcher", lambda _runtime: tmp_path / "bin" / "syke"
    )
    monkeypatch.setattr(
        access,
        "_run_launchd_probe",
        lambda **_kwargs: {
            "schema_version": 1,
            "checked_at": "2026-08-15T00:00:00+00:00",
            "runtime_identity": _identity("current"),
            "folders": {
                **_granted_folders(home),
                "Documents": {
                    "ok": False,
                    "status": "denied",
                    "path": str(home / "Documents"),
                    "error_code": "EPERM",
                    "error": "operation not permitted",
                },
            },
        },
    )

    result = access.run_macos_filesystem_access_check("test")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "Documents" in result["detail"]
    stored = state_path.read_text(encoding="utf-8")
    assert "operation not permitted" in stored
    assert "private-file-name" not in stored
    assert not any(run_root.iterdir())


def test_run_check_reports_background_launcher_failure(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(access.sys, "platform", "darwin")
    monkeypatch.setattr(access, "ACCESS_STATE_PATH", state_path)
    monkeypatch.setattr(access, "ACCESS_RUN_ROOT", tmp_path / "runs")

    def unavailable_runtime() -> None:
        raise RuntimeError("background runtime unavailable")

    monkeypatch.setattr("syke.runtime.locator.resolve_background_syke_runtime", unavailable_runtime)

    result = access.run_macos_filesystem_access_check("test")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["error"] == "background runtime unavailable"
    assert json.loads(state_path.read_text(encoding="utf-8"))["error"] == result["error"]


def test_probe_worker_uses_seatbelt_and_records_only_folder_results(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    result_path = tmp_path / "result.json"
    node = tmp_path / "node"
    python = tmp_path / "python"
    node.write_bytes(b"node")
    python.write_bytes(b"python")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(access.sys, "executable", str(python))
    monkeypatch.setattr("syke.llm.pi_client.ensure_node_binary", lambda: node)
    monkeypatch.setattr("syke.runtime.sandbox.generate_seatbelt_profile", lambda *a, **k: "profile")
    monkeypatch.setattr(
        access.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"folders": _granted_folders(home)}),
            stderr="",
        ),
    )

    returncode = access.run_probe_worker(result_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))

    assert returncode == 0
    assert set(payload["folders"]) == set(access.PROTECTED_FOLDER_NAMES)
    assert "private-file-name" not in result_path.read_text(encoding="utf-8")
