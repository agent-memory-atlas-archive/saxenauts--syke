from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from syke.daemon.ask_worker_child import run_child
from syke.daemon.ask_workers import (
    DaemonAskCapacityExceeded,
    DaemonAskWorkerError,
    DaemonAskWorkerSupervisor,
)


def test_daemon_ask_worker_supervisor_streams_events_and_result(tmp_path) -> None:
    script = """
import json
import os
import sys

request = json.loads(sys.stdin.read())
print(json.dumps({"type": "event", "event": {"type": "thinking", "content": "looking"}}), flush=True)
print(json.dumps({
    "type": "result",
    "answer": "worker answer",
    "metadata": {
        "backend": "pi",
        "transport": "daemon_worker",
        "worker_pid": os.getpid(),
        "question_seen": request["question"],
    },
}), flush=True)
"""
    seen: list[str] = []
    supervisor = DaemonAskWorkerSupervisor(
        max_workers=1,
        command=[sys.executable, "-c", script],
    )

    answer, metadata = supervisor.ask(
        user_id="test",
        syke_db_path=str(tmp_path / "syke.db"),
        question="what changed",
        on_event=lambda event: seen.append(f"{event.type}:{event.content}"),
        transport_details={"daemon_pid": 123, "routing_reason": "warm_runtime_busy"},
    )

    assert answer == "worker answer"
    assert seen == ["thinking:looking"]
    assert metadata["transport"] == "daemon_worker"
    assert metadata["question_seen"] == "what changed"
    assert metadata["routing_reason"] == "warm_runtime_busy"
    assert metadata["daemon_pid"] == 123
    assert isinstance(metadata["worker_pid"], int)
    assert isinstance(metadata["worker_roundtrip_ms"], int)
    assert isinstance(metadata["worker_slot_wait_ms"], int)


def test_daemon_ask_worker_supervisor_enforces_capacity() -> None:
    supervisor = DaemonAskWorkerSupervisor(max_workers=1, capacity_wait_s=0.0)
    assert supervisor._semaphore is not None
    assert supervisor._semaphore.acquire(blocking=False)

    try:
        with pytest.raises(DaemonAskCapacityExceeded, match="capacity exceeded"):
            supervisor.ask(
                user_id="test",
                syke_db_path="/tmp/syke.db",
                question="what changed",
                on_event=None,
                transport_details={},
            )
    finally:
        supervisor._semaphore.release()


def test_daemon_ask_worker_env_is_bounded_and_normalizes_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    claude_tmp = tmp_path / "claude-501"
    child_tmp = tmp_path / "syke-child"
    runtime_tmp = tmp_path / "runtime-tmp"
    claude_tmp.mkdir()
    child_tmp.mkdir()
    runtime_tmp.mkdir()
    pi_agent_dir = tmp_path / "pi-agent"

    monkeypatch.setenv("TMPDIR", str(claude_tmp))
    monkeypatch.setenv("SYKE_PI_TMPDIR", str(child_tmp))
    monkeypatch.setenv("SYKE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai")
    monkeypatch.setenv("UNSAFE_SECRET", "must-not-leak")
    monkeypatch.setattr(
        "syke.pi_state.build_pi_agent_env",
        lambda: {
            "PI_CODING_AGENT_DIR": str(pi_agent_dir),
            "TMPDIR": str(runtime_tmp),
        },
    )

    env = DaemonAskWorkerSupervisor()._worker_env()

    assert env["PI_CODING_AGENT_DIR"] == str(pi_agent_dir)
    assert env["OPENAI_API_KEY"] == "host-openai"
    assert "UNSAFE_SECRET" not in env
    assert env["TMPDIR"] == str(child_tmp)
    assert env["TMP"] == str(child_tmp)
    assert env["TEMP"] == str(child_tmp)


def test_ask_worker_opens_the_user_database(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "syke.db"
    opened: list[str] = []

    class FakeDB:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(
        "syke.cli_support.context.get_db",
        lambda user: opened.append(user) or FakeDB(),
    )
    monkeypatch.setattr(
        "syke.llm.backends.pi_ask.pi_ask",
        lambda _db, _user, _question, **_kwargs: (
            "answer",
            {"backend": "pi", "transport": "daemon_worker"},
        ),
    )

    assert (
        run_child(
            {
                "user_id": "person",
                "syke_db_path": str(db_path),
                "question": "what changed",
                "transport_details": {},
            }
        )
        == 0
    )
    assert opened == ["person"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "result"
    assert payload["answer"] == "answer"


def test_ask_worker_timeout_uses_wall_clock(monkeypatch, tmp_path) -> None:
    """Worker timeout must expire on wall time, not frozen monotonic time.

    Simulates system sleep during an ask: the wall clock advances in large
    steps (each selector wakeup = 10 minutes of wall time) while a hung
    child keeps its pipes open. The old monotonic deadline would never
    fire while the machine slept; the wall deadline must fire within a
    few wakeups.
    """
    script = "import sys, time; sys.stdin.read(); time.sleep(300)"
    supervisor = DaemonAskWorkerSupervisor(
        max_workers=1,
        command=[sys.executable, "-c", script],
    )

    t = {"now": 1_000_000.0}

    def fake_time() -> float:
        t["now"] += 600.0  # every selector wakeup = 10 minutes of wall time
        return t["now"]

    monkeypatch.setattr(
        "syke.daemon.ask_workers.time",
        SimpleNamespace(time=fake_time, monotonic=time.monotonic),
    )

    with pytest.raises(DaemonAskWorkerError, match="timed out"):
        supervisor.ask(
            user_id="test",
            syke_db_path=str(tmp_path / "syke.db"),
            question="hang",
            on_event=None,
            transport_details={},
        )
