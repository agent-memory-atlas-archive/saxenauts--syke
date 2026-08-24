from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import syke.config as config
import syke.onboarding as onboarding
from syke.cli_support import daemon_state
from syke.daemon.daemon import SykeDaemon
from syke.entrypoint import cli


@pytest.mark.parametrize(
    ("waiter", "snapshot"),
    [
        (
            daemon_state.wait_for_daemon_startup,
            {"running": False, "registered": True, "ipc": {"ok": False}},
        ),
        (
            daemon_state.wait_for_daemon_shutdown,
            {"running": True, "registered": False, "ipc": {"ok": True}},
        ),
    ],
)
def test_daemon_lifecycle_wait_deadlines_use_wall_clock(monkeypatch, waiter, snapshot) -> None:
    wall_times = iter([100.0, 121.0])
    snapshots: list[dict[str, object]] = []

    def readiness(_user_id: str) -> dict[str, object]:
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(daemon_state, "daemon_readiness_snapshot", readiness)
    monkeypatch.setattr(daemon_state.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(
        daemon_state.time,
        "monotonic",
        lambda: (_ for _ in ()).throw(AssertionError("monotonic deadline used")),
    )
    monkeypatch.setattr(daemon_state.time, "sleep", lambda _delay: None)

    assert waiter("test", timeout_seconds=20.0) is snapshot
    assert snapshots == [snapshot]


def test_daemon_start_reports_registered_service_without_live_process(cli_runner) -> None:
    with (
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": False, "pid": None, "source": "none"},
        ),
        patch("syke.cli_commands.daemon.sys.platform", "linux"),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_startup",
            return_value={
                "running": False,
                "registered": True,
                "platform": "Darwin",
                "pid": None,
                "ipc": {"ok": False, "detail": "daemon IPC socket missing"},
            },
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "start"])

    assert result.exit_code == 4
    assert (
        "Daemon service is registered, but no live background process is running." in result.output
    )


@pytest.mark.parametrize(
    ("filesystem_access", "expected_output"),
    [
        ({"ok": True}, "Protected-folder access verified."),
        (
            {"ok": False, "detail": "blocked: Documents"},
            "Protected-folder access incomplete: blocked: Documents",
        ),
    ],
)
def test_daemon_start_verifies_macos_protected_folders(
    cli_runner,
    filesystem_access: dict[str, object],
    expected_output: str,
) -> None:
    with (
        patch("syke.cli_commands.daemon.sys.platform", "darwin"),
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": False, "pid": None, "source": "none"},
        ),
        patch("syke.daemon.daemon.install_and_start") as install,
        patch(
            "syke.runtime.macos_filesystem_access.run_macos_filesystem_access_check",
            return_value=filesystem_access,
        ) as verify_access,
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_startup",
            return_value={
                "running": True,
                "registered": True,
                "platform": "Darwin",
                "pid": 123,
                "ipc": {"ok": True, "detail": "ready"},
            },
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "start"])

    assert result.exit_code == 0
    verify_access.assert_called_once_with("test")
    install.assert_called_once_with("test", 900)
    assert expected_output in result.output


def test_daemon_stop_reports_incomplete_when_process_survives(cli_runner) -> None:
    with (
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 123, "source": "pidfile"},
        ),
        patch("syke.daemon.daemon.launchd_metadata", return_value={"registered": True}),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_shutdown",
            return_value={"running": True, "registered": False, "pid": 123},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "stop"])

    assert result.exit_code == 4
    assert "Daemon stop is incomplete." in result.output


def test_daemon_ipc_ask_rejects_noncanonical_db_path(monkeypatch) -> None:
    daemon = SykeDaemon("test")
    monkeypatch.setattr("syke.config.user_syke_db_path", lambda _user: "/tmp/expected.db")

    with pytest.raises(ValueError, match="outside user scope"):
        daemon._handle_ipc_ask(
            syke_db_path="/tmp/unexpected.db",
            question="what changed",
            on_event=None,
        )


def test_daemon_ipc_ask_warm_path_marks_routing_reason(monkeypatch, tmp_path) -> None:
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")
    daemon = SykeDaemon("test")
    captured: dict[str, object] = {}

    def fake_pi_ask(db, user_id, question, **kwargs):
        captured.update(kwargs)
        assert user_id == "test"
        assert question == "what changed"
        assert db.db_path == str(syke_db_path)
        return "warm answer", {"backend": "pi", "transport": kwargs["transport"]}

    monkeypatch.setattr("syke.config.user_syke_db_path", lambda _user: syke_db_path)
    monkeypatch.setattr(
        "syke.cli_support.context.get_db",
        lambda _user: SimpleNamespace(db_path=str(syke_db_path), close=lambda: None),
    )
    monkeypatch.setattr("syke.llm.backends.pi_ask.pi_ask", fake_pi_ask)

    answer, metadata = daemon._handle_ipc_ask(
        syke_db_path=str(syke_db_path),
        question="what changed",
        on_event=None,
    )

    assert answer == "warm answer"
    assert metadata["transport"] == "daemon_ipc"
    transport_details = captured["transport_details"]
    assert isinstance(transport_details, dict)
    assert transport_details["routing_reason"] == "warm_runtime"
    assert "daemon_pid" in transport_details
    assert "ipc_socket_path" in transport_details


def test_daemon_distributes_only_completed_synthesis_with_actual_memex_state() -> None:
    daemon = SykeDaemon("test")
    db = SimpleNamespace()

    with patch("syke.distribution.refresh_distribution") as refresh:
        daemon._distribute(SimpleNamespace(), {"status": "blocked", "error": "No Pi model"})
        refresh.assert_not_called()
        refresh.return_value = SimpleNamespace(memex_path=None, skill_paths=[], warnings=[])

        daemon._distribute(db, {"status": "completed", "memex_updated": True})
        assert refresh.call_args.kwargs["memex_updated"] is True

        refresh.reset_mock()
        daemon._distribute(db, {"status": "completed", "memex_updated": False})
        assert refresh.call_args.kwargs["memex_updated"] is False

        refresh.reset_mock()
        daemon._distribute(db, {"status": "completed"})
        assert refresh.call_args.kwargs["memex_updated"] is False


@pytest.mark.parametrize(
    ("synthesis_status", "expected_onboarding_status"),
    [
        ("completed", "first_synthesis_completed"),
        ("blocked", "waiting_first_synthesis"),
    ],
)
def test_daemon_cycle_completes_onboarding_only_after_accepted_synthesis(
    synthesis_status: str,
    expected_onboarding_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SYKE_HOME", tmp_path)
    _ = onboarding.write_onboarding_state(
        "test",
        selected_sources=("codex",),
        total_files=1,
        estimated_minutes=1,
        estimate_method="test",
        mode="daemon",
    )
    daemon = SykeDaemon("test")
    distributed: list[str] = []

    def synthesize(_db: object) -> dict[str, object]:
        return {"status": synthesis_status}

    def distribute(_db: object, result: dict[str, object]) -> None:
        distributed.append(str(result["status"]))

    monkeypatch.setattr(daemon, "_health_check", lambda: {"healthy": True})
    monkeypatch.setattr(daemon, "_synthesize", synthesize)
    monkeypatch.setattr(daemon, "_distribute", distribute)

    daemon._daemon_cycle(SimpleNamespace())

    state = onboarding.read_onboarding_state("test")
    assert state is not None
    assert state["status"] == expected_onboarding_status
    assert distributed == [synthesis_status]


def test_daemon_ensure_process_markers_rewrites_pid_and_rebinds_ipc(tmp_path, monkeypatch) -> None:
    daemon = SykeDaemon("test")
    pid_path = tmp_path / "daemon.pid"
    socket_path = tmp_path / "daemon.sock"
    socket_path.write_text("stale", encoding="utf-8")
    stop_ipc = Mock()

    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", pid_path)
    daemon._pi_runtime = SimpleNamespace(status=lambda: {"alive": True})
    daemon._ipc_server = SimpleNamespace(
        socket_path=socket_path,
        stop=stop_ipc,
    )

    with (
        patch(
            "syke.daemon.ipc.daemon_ipc_status",
            return_value={"reachable": False},
        ),
        patch.object(daemon, "_start_ipc_server") as start_ipc,
    ):
        daemon._ensure_process_markers()

    assert pid_path.exists()
    assert pid_path.read_text(encoding="utf-8").strip().isdigit()
    stop_ipc.assert_called_once()
    start_ipc.assert_called_once()


def test_synthesis_timeout_returns_failure() -> None:
    daemon = SykeDaemon("test")
    daemon._runtime_lock = SimpleNamespace(
        acquire=lambda timeout=None: False,
        release=lambda: None,
    )

    with patch("syke.llm.backends.pi_synthesis.pi_synthesize") as synthesize:
        result = daemon._synthesize(SimpleNamespace())

    synthesize.assert_not_called()
    assert result["status"] == "failed"
    assert "timeout" in result["error"]
