from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from syke.daemon.daemon import SykeDaemon
from syke.daemon.ipc import (
    DaemonIpcServer,
    DaemonIpcUnavailable,
    ask_via_daemon,
    daemon_ipc_status,
    socket_path_for_user,
)
from syke.llm.backends import AskEvent


def _unix_socket_bind_is_available(path: Path) -> bool:
    if not hasattr(socket, "AF_UNIX"):
        return False

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.bind(str(path))
    except OSError:
        return False
    finally:
        probe.close()
        path.unlink(missing_ok=True)

    return True


def _require_unix_socket_bind(tmp_path: Path) -> None:
    _ = tmp_path
    if not _unix_socket_bind_is_available(socket_path_for_user("bind-probe")):
        pytest.skip("Unix socket bind not permitted in this environment")


def _start_server_or_skip(server: DaemonIpcServer) -> None:
    if not server.start():
        pytest.skip("Unix domain socket bind unavailable in this environment")


def test_daemon_ipc_round_trip_streams_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    _require_unix_socket_bind(tmp_path)
    seen: list[AskEvent] = []

    def handler(
        syke_db_path: str,
        question: str,
        on_event,
    ) -> tuple[str, dict[str, object]]:
        assert syke_db_path == "/tmp/replay-syke.db"
        assert question == "What changed?"
        if on_event is not None:
            on_event(AskEvent(type="thinking", content="Looking"))
            on_event(AskEvent(type="text", content="Warm answer"))
        return "Warm answer", {"backend": "pi", "duration_ms": 12}

    server = DaemonIpcServer("test_user", handler)
    _start_server_or_skip(server)
    try:
        answer, metadata = ask_via_daemon(
            user_id="test_user",
            syke_db_path="/tmp/replay-syke.db",
            question="What changed?",
            on_event=seen.append,
        )
    finally:
        server.stop()

    assert answer == "Warm answer"
    assert metadata["transport"] == "daemon_ipc"
    assert isinstance(metadata["ipc_roundtrip_ms"], int)
    assert str(metadata["ipc_socket_path"]).endswith(".sock")
    assert [event.type for event in seen] == ["thinking", "text"]
    assert [event.content for event in seen] == ["Looking", "Warm answer"]


def test_daemon_ipc_errors_surface_as_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    _require_unix_socket_bind(tmp_path)

    def handler(
        syke_db_path: str,
        question: str,
        on_event,
    ) -> tuple[str, dict[str, object]]:
        del syke_db_path, question, on_event
        raise RuntimeError("boom")

    server = DaemonIpcServer("test_user", handler)
    _start_server_or_skip(server)
    try:
        with pytest.raises(DaemonIpcUnavailable, match="boom"):
            ask_via_daemon(
                user_id="test_user",
                syke_db_path="/tmp/replay-syke.db",
                question="What changed?",
            )
    finally:
        server.stop()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="requires Unix sockets")
def test_daemon_ipc_rejects_asks_beyond_handler_cap(monkeypatch, tmp_path: Path) -> None:
    """A saturated IPC server rejects immediately instead of growing handlers."""
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    _require_unix_socket_bind(tmp_path)

    handler_started = threading.Event()
    handler_release = threading.Event()

    def slow_handler(
        syke_db_path: str,
        question: str,
        on_event,
    ) -> tuple[str, dict[str, object]]:
        del syke_db_path, on_event
        handler_started.set()
        handler_release.wait(timeout=10)
        return question, {"backend": "pi", "duration_ms": 12}

    server = DaemonIpcServer("test_user", slow_handler, max_handlers=1)
    _start_server_or_skip(server)

    first_result: dict[str, str] = {}

    def first_ask() -> None:
        answer, _metadata = ask_via_daemon(
            user_id="test_user",
            syke_db_path="/tmp/replay-syke.db",
            question="slow",
        )
        first_result["answer"] = answer

    occupant = threading.Thread(target=first_ask, daemon=True)
    try:
        occupant.start()
        assert handler_started.wait(timeout=5), "first handler never started"

        with pytest.raises(DaemonIpcUnavailable, match="saturated"):
            ask_via_daemon(
                user_id="test_user",
                syke_db_path="/tmp/replay-syke.db",
                question="rejected",
            )

        handler_release.set()
        occupant.join(timeout=10)
        assert first_result.get("answer") == "slow"

        # Once the in-flight handler drains, the server accepts asks again.
        assert server._handlers_done.wait(timeout=5), "handler never drained"
        answer, _metadata = ask_via_daemon(
            user_id="test_user",
            syke_db_path="/tmp/replay-syke.db",
            question="after-drain",
        )
        assert answer == "after-drain"
    finally:
        handler_release.set()
        server.stop()


def test_daemon_ipc_busy_runtime_round_trips_daemon_worker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    _require_unix_socket_bind(tmp_path)
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")
    daemon = SykeDaemon("test_user")
    captured: dict[str, object] = {}

    class FakeWorkers:
        def ask(self, **kwargs):
            captured.update(kwargs)
            return "worker answer", {
                "backend": "pi",
                "transport": "daemon_worker",
                "worker_pid": 4242,
                "routing_reason": "warm_runtime_busy",
            }

    monkeypatch.setattr("syke.config.user_syke_db_path", lambda _user: syke_db_path)
    daemon._ask_workers = cast(Any, FakeWorkers())
    daemon._runtime_lock.acquire()
    server = DaemonIpcServer(
        "test_user",
        daemon._handle_ipc_ask,
        daemon._handle_ipc_runtime_status,
    )
    _start_server_or_skip(server)
    try:
        answer, metadata = ask_via_daemon(
            user_id="test_user",
            syke_db_path=str(syke_db_path),
            question="What changed?",
        )
    finally:
        server.stop()
        daemon._runtime_lock.release()

    assert answer == "worker answer"
    assert metadata["transport"] == "daemon_worker"
    assert metadata["routing_reason"] == "warm_runtime_busy"
    assert metadata["worker_pid"] == 4242
    assert isinstance(metadata["ipc_roundtrip_ms"], int)
    assert captured["question"] == "What changed?"


def test_daemon_ipc_status_unreachable_socket_is_not_ok(monkeypatch, tmp_path: Path) -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix sockets unavailable on this platform")

    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    socket_path = socket_path_for_user("test_user")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_text("not-a-socket", encoding="utf-8")

    payload = daemon_ipc_status("test_user")

    assert payload["socket_present"] is True
    assert payload["reachable"] is False
    assert payload["ok"] is False
    assert "unreachable" in str(payload["detail"])


def test_daemon_ipc_start_returns_false_when_socket_bind_is_denied(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)

    def handler(
        syke_db_path: str,
        question: str,
        on_event,
    ) -> tuple[str, dict[str, object]]:
        del syke_db_path, question, on_event
        return "Warm answer", {"backend": "pi", "duration_ms": 12}

    server = DaemonIpcServer("test_user", handler)

    with patch(
        "syke.daemon.ipc._ThreadingUnixStreamServer",
        side_effect=PermissionError(1, "Operation not permitted"),
    ):
        assert server.start() is False

    assert not server.socket_path.exists()


def test_daemon_ipc_start_refuses_to_clobber_live_socket(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    server = DaemonIpcServer("test_user", lambda *_args, **_kwargs: ("ok", {}))
    server.socket_path.write_text("", encoding="utf-8")

    with (
        patch("syke.daemon.ipc._socket_is_reachable", return_value=(True, None)),
        patch("syke.daemon.ipc._unlink_socket") as unlink_socket,
    ):
        assert server.start() is False

    unlink_socket.assert_not_called()


def test_daemon_ipc_bind_failure_does_not_unlink_socket_won_by_other_owner(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    server = DaemonIpcServer("test_user", lambda *_args, **_kwargs: ("ok", {}))
    server.socket_path.write_text("stale", encoding="utf-8")

    def bind_loses_race(*_args, **_kwargs):
        server.socket_path.write_text("owned-by-other", encoding="utf-8")
        raise OSError("address already in use")

    with (
        patch("syke.daemon.ipc._socket_is_reachable", return_value=(False, "stale")),
        patch("syke.daemon.ipc._ThreadingUnixStreamServer", side_effect=bind_loses_race),
    ):
        assert server.start() is False

    assert server.socket_path.read_text(encoding="utf-8") == "owned-by-other"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="requires Unix sockets")
def test_daemon_ipc_stop_does_not_unlink_replaced_socket_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    server = DaemonIpcServer("test_user", lambda *_args, **_kwargs: ("ok", {}))
    _require_unix_socket_bind(server.socket_path.parent)
    _start_server_or_skip(server)

    server.socket_path.unlink()
    server.socket_path.write_text("owned-by-other", encoding="utf-8")
    server.stop()

    assert server.socket_path.read_text(encoding="utf-8") == "owned-by-other"
