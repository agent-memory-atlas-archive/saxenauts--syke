import plistlib
import signal
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from syke.daemon.daemon import (
    DaemonInstanceLocked,
    SykeDaemon,
    _acquire_daemon_lock,
    _pid_is_safe_daemon_target,
    _pid_looks_like_syke,
    _release_daemon_lock,
    daemon_process_state,
    generate_plist,
    is_running,
    stop_and_unload,
)
from syke.runtime.locator import SykeRuntimeDescriptor


def test_daemon_pid_permission_denied_treated_as_running(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    pid_path.write_text("91919", encoding="utf-8")

    with patch("os.kill", side_effect=PermissionError):
        running, pid = is_running()

    assert running is True
    assert pid == 91919
    assert pid_path.exists()


def test_stale_pid_is_removed(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    pid_path.write_text("91919", encoding="utf-8")
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", pid_path)

    with patch("os.kill", side_effect=ProcessLookupError):
        assert is_running() == (False, None)

    assert not pid_path.exists()


def test_non_syke_pid_is_never_trusted(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", pid_path)

    for kill_effect in (None, PermissionError()):
        pid_path.write_text("91919", encoding="utf-8")
        with (
            patch("os.kill", side_effect=kill_effect),
            patch("syke.daemon.daemon._pid_looks_like_syke", return_value=False),
        ):
            assert is_running() == (False, None)
        assert not pid_path.exists()


def test_pid_identity_requires_daemon_run_signature() -> None:
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["ps"],
            0,
            stdout="/usr/local/bin/syke --user test daemon run --interval 900\n",
            stderr="",
        ),
    ):
        assert _pid_looks_like_syke(1234) is True

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["ps"], 0, stdout="/usr/local/bin/syke ask what changed\n", stderr=""
        ),
    ):
        assert _pid_looks_like_syke(1234) is False


def test_pid_safety_accepts_launchd_attestation_when_ps_identity_is_uncertain(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")

    with (
        patch("syke.daemon.daemon._pid_looks_like_syke", return_value=None),
        patch(
            "syke.daemon.daemon.launchd_metadata",
            return_value={"registered": True, "state": "running", "pid": 4242},
        ),
    ):
        assert _pid_is_safe_daemon_target(4242) is True


def test_daemon_process_state_requires_current_service_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", tmp_path / "syke.pid")
    monkeypatch.setattr("sys.platform", "darwin")
    cases = (
        (
            {"registered": True, "state": "running", "pid": 4242},
            {"running": True, "pid": 4242, "source": "launchd"},
        ),
        (
            {
                "registered": True,
                "state": "running",
                "pid": 4242,
                "stale": True,
                "stale_reasons": ["plist missing"],
            },
            {"running": False, "pid": None, "source": "none"},
        ),
    )

    for metadata, expected in cases:
        with (
            patch("syke.daemon.daemon.launchd_metadata", return_value=metadata),
            patch("os.kill", return_value=None),
        ):
            assert daemon_process_state() == expected


def test_daemon_lock_blocks_second_instance(monkeypatch, tmp_path):
    lock_path = tmp_path / "daemon.lock"
    monkeypatch.setattr("syke.daemon.daemon.LOCKFILE", lock_path)

    handle = _acquire_daemon_lock()
    try:
        with pytest.raises(DaemonInstanceLocked):
            _acquire_daemon_lock()
    finally:
        _release_daemon_lock(handle)


# --- Plist generation ---


def test_generate_plist_uses_stable_syke_launcher(monkeypatch):
    secret = "sk_test_should_not_appear"
    monkeypatch.setenv("SYKE_API_KEY", secret)
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
    ):
        plist = generate_plist("testuser", interval=900)

    assert "/Users/me/.syke/bin/syke" in plist
    assert "/usr/local/bin/syke" not in plist
    assert "900" in plist
    assert secret not in plist
    payload = plistlib.loads(plist.encode())
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert payload["ThrottleInterval"] == 30


def test_generate_plist_rejects_tcc_path_when_no_alternative():
    with patch(
        "syke.runtime.locator.resolve_background_syke_runtime",
        side_effect=RuntimeError("macOS-protected directory"),
    ):
        with pytest.raises(RuntimeError, match="macOS-protected directory"):
            generate_plist("testuser", interval=900)


def test_install_launchd_writes_generated_service(tmp_path, monkeypatch):
    from syke.daemon.daemon import install_launchd

    plist_path = tmp_path / "com.syke.daemon.plist"
    log_path = tmp_path / "daemon.log"
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)
    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", log_path)

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
        patch("subprocess.run", return_value=subprocess.CompletedProcess(["launchctl"], 0)),
    ):
        install_launchd("testuser", interval=1234)

    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["ProgramArguments"][-2:] == ["--interval", "1234"]
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True


def test_stop_and_unload_stops_running_process_before_unloading(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls: list[str] = []

    def _unload() -> None:
        calls.append("unload")

    def _kill(pid: int, sig: int) -> None:
        _ = (pid, sig)
        calls.append("kill")

    with (
        patch(
            "syke.daemon.daemon.is_running",
            side_effect=[(True, 123), (False, None), (False, None)],
        ),
        patch("syke.daemon.daemon.uninstall_launchd", side_effect=_unload),
        patch("syke.daemon.daemon._pid_is_safe_daemon_target", return_value=True),
        patch("os.kill", side_effect=_kill),
        patch("time.monotonic", return_value=0.0),
        patch("time.sleep"),
    ):
        stop_and_unload()

    assert calls == ["unload", "kill"]


def test_stop_and_unload_deadlines_use_wall_clock(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    wall_times = iter([100.0, 106.0, 106.0, 109.0])

    with (
        patch(
            "syke.daemon.daemon.is_running",
            side_effect=[(True, 123), (True, 123)],
        ),
        patch("syke.daemon.daemon.uninstall_launchd"),
        patch("syke.daemon.daemon._pid_is_safe_daemon_target", return_value=True),
        patch("os.kill") as kill_mock,
        patch("time.time", side_effect=lambda: next(wall_times)),
        patch(
            "time.monotonic",
            side_effect=AssertionError("monotonic deadline used"),
        ),
        patch("time.sleep"),
    ):
        stop_and_unload()

    assert kill_mock.call_args_list == [
        call(123, signal.SIGTERM),
        call(123, signal.SIGKILL),
    ]


def test_stop_and_unload_refuses_to_kill_unverified_pid(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls: list[str] = []

    def _unload() -> None:
        calls.append("unload")

    with (
        patch("syke.daemon.daemon.is_running", return_value=(True, 123)),
        patch("syke.daemon.daemon.uninstall_launchd", side_effect=_unload),
        patch("syke.daemon.daemon._pid_is_safe_daemon_target", return_value=False),
        patch("os.kill") as kill_mock,
    ):
        stop_and_unload()

    kill_mock.assert_not_called()
    assert calls == ["unload"]


def test_daemon_run_contains_cycle_failure_and_continues(monkeypatch):
    daemon = SykeDaemon("testuser", interval=1)
    cycle_calls = {"count": 0}

    class _FakeDB:
        db_path = "/tmp/syke.db"

        def initialize(self) -> None:
            return

        def close(self) -> None:
            return

    def _cycle(_db) -> None:
        cycle_calls["count"] += 1
        if cycle_calls["count"] == 1:
            raise RuntimeError("boom")
        daemon.stop()

    monkeypatch.setattr("syke.config.user_data_dir", lambda _user: Path("/tmp"))
    monkeypatch.setattr("syke.cli_support.context.get_db", lambda _user: _FakeDB())

    with (
        patch("signal.signal"),
        patch("syke.daemon.daemon._acquire_daemon_lock", return_value=None),
        patch("syke.daemon.daemon._release_daemon_lock"),
        patch("syke.daemon.daemon._write_pid"),
        patch("syke.daemon.daemon._unlink_pidfile"),
        patch.object(daemon, "_start_pi_runtime"),
        patch.object(daemon, "_stop_pi_runtime"),
        patch.object(daemon, "_start_ipc_server"),
        patch.object(daemon, "_stop_ipc_server"),
        patch.object(daemon, "_start_web_server"),
        patch.object(daemon, "_stop_web_server"),
        patch.object(daemon._stop_event, "wait", return_value=False),
        patch.object(daemon, "_daemon_cycle", side_effect=_cycle),
    ):
        daemon.run()

    assert cycle_calls["count"] == 2
