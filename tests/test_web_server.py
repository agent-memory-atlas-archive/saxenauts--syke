"""Tests for the local read-only timeline web server."""

from __future__ import annotations

import json
import socket
import sqlite3
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from uuid_extensions import uuid7

from syke.control import write_receipt
from syke.daemon.web import (
    SykeWebServer,
    query_ask,
    query_current_graph,
    query_cycle,
    query_health,
    query_timeline,
)
from syke.db import SykeDB
from syke.memory.memex_history import write_memex_version


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_session(
    db_path: Path,
    *,
    operation_id: str,
    kind: str,
    started_at: datetime,
    completed_at: datetime,
    input_text: str = "",
    output_text: str = "",
    tool_calls: list[dict[str, object]] | None = None,
    model: str = "gpt-5.4",
    status: str = "completed",
    error: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> tuple[str, Path]:
    session_id = str(uuid7())
    from syke.runtime import workspace

    sessions = workspace.CONTROL_ROOT / "sessions"
    path = sessions / f"{started_at.isoformat().replace(':', '-')}_{session_id}.jsonl"
    assistant_content: list[dict[str, object]] = [
        {
            "type": "toolCall",
            "id": f"call-{index}",
            "name": str(call.get("name") or "tool"),
            "arguments": call.get("input") or {},
        }
        for index, call in enumerate(tool_calls or [])
    ]
    if output_text:
        assistant_content.append({"type": "text", "text": output_text})
    entries: list[dict[str, object]] = [
        {
            "type": "session",
            "version": 3,
            "id": session_id,
            "timestamp": started_at.isoformat(),
            "cwd": str(db_path.parent),
        },
        {
            "type": "model_change",
            "id": f"model-{session_id}",
            "timestamp": started_at.isoformat(),
            "provider": "openai-codex",
            "modelId": model,
        },
        {
            "type": "session_info",
            "id": f"name-{session_id}",
            "timestamp": started_at.isoformat(),
            "name": f"syke:{kind}:{operation_id}",
        },
    ]
    if input_text:
        entries.append(
            {
                "type": "message",
                "id": f"user-{session_id}",
                "timestamp": started_at.isoformat(),
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": input_text}],
                },
            }
        )
    entries.append(
        {
            "type": "message",
            "id": f"assistant-{session_id}",
            "timestamp": completed_at.isoformat(),
            "message": {
                "role": "assistant",
                "provider": "openai-codex",
                "model": model,
                "content": assistant_content,
                "usage": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "cost": {"total": 0.001},
                },
                "stopReason": "error" if status == "failed" else "stop",
                "errorMessage": error,
            },
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )
    return session_id, path


def _write_cycle_receipt(
    cycle_id: str,
    *,
    started_at: str,
    completed_at: str | None = None,
    status: str = "completed",
    memex_updated: bool = False,
    memex_content: str | None = None,
    previous_memex_content: str | None = None,
    state_change: dict[str, object] | None = None,
    session_id: str | None = None,
) -> None:
    from syke.runtime import workspace

    receipt_session_id = session_id
    version_ref = None
    if memex_content is not None:
        receipt_session_id = receipt_session_id or f"session-{cycle_id}"
        version_ref = write_memex_version(
            workspace.CONTROL_ROOT,
            cycle_id=cycle_id,
            session_id=receipt_session_id,
            completed_at=completed_at or started_at,
            content=memex_content,
            previous_content=previous_memex_content,
        )
    payload: dict[str, object] = {
        "id": cycle_id,
        "started_at": started_at,
        "completed_at": completed_at or started_at,
        "status": status,
        "session_id": receipt_session_id,
        "acknowledged_record_ids": [],
        "memex_updated": bool(memex_updated or version_ref),
    }
    if version_ref is not None:
        payload["memex_version"] = version_ref
    if state_change is not None:
        payload["state_change"] = state_change
    write_receipt(
        workspace.CONTROL_ROOT,
        payload,
    )


def _seed_db(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        # One accepted MEMEX version followed by an updated accepted version.
        from syke.memory.memex import update_memex

        update_memex(db, user_id, "# MEMEX\n\n## Active Routes\n\n- baseline\n")
        update_memex(db, user_id, "# MEMEX\n\n## Active Routes\n\n- baseline\n- new route\n")

        # One completed host receipt inside the timeline window.
        cid = str(uuid7())
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        baseline = "# MEMEX\n\n## Active Routes\n\n- baseline\n"
        current = "# MEMEX\n\n## Active Routes\n\n- baseline\n- new route\n"
        baseline_at = now - timedelta(seconds=1)
        _write_cycle_receipt(
            str(uuid7()),
            started_at=baseline_at.isoformat(),
            completed_at=baseline_at.isoformat(),
            memex_content=baseline,
        )
        session_id, _ = _write_session(
            db_path,
            operation_id=cid,
            kind="synthesis",
            started_at=now,
            completed_at=now,
            output_text="cycle complete",
            model="gpt-5.4",
            input_tokens=1000,
            output_tokens=200,
        )
        _write_cycle_receipt(
            cid,
            started_at=now_iso,
            session_id=session_id,
            memex_content=current,
            previous_memex_content=baseline,
        )

        ask_id = str(uuid7())
        now = datetime.now(UTC)
        _write_session(
            db_path,
            operation_id=ask_id,
            kind="ask",
            started_at=now,
            completed_at=now,
            status="completed",
            input_text="What is syke?",
            output_text="A memory system.",
            model="gpt-5.4",
        )
    return db_path, user_id


@contextmanager
def _running_server(tmp_path: Path, monkeypatch, *, html: str = "<!doctype html><h1>ok</h1>"):
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))
    html_path = tmp_path / "index.html"
    html_path.write_text(html)
    port = _free_port()
    srv = SykeWebServer(user_id, port, html_path)
    assert srv.start()
    try:
        yield db_path, user_id, port
    finally:
        srv.stop()


# ─── Host validation (DNS rebinding defense) ────────────────────────────────


def test_server_enforces_localhost_and_security_headers(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch) as (_, _, port):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Host": "evil.com"})
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=2)
        assert excinfo.value.code == 403
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            headers = {k.lower(): v for k, v in r.headers.items()}
            assert "no-store" in headers["cache-control"]
            assert headers["x-content-type-options"] == "nosniff"
            assert "default-src 'self'" in headers["content-security-policy"]


# ─── Query layer ─────────────────────────────────────────────────────────────


def test_query_health_updates_provider_blocker_without_model_resolution(tmp_path, monkeypatch):
    from syke.llm import pi_client

    def _fail_model_resolution(_model_override=None):
        raise AssertionError("/api/health must not invoke Pi model resolution")

    monkeypatch.delenv("SYKE_PROVIDER", raising=False)
    pi_agent = tmp_path / "pi-agent"
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent))
    monkeypatch.setattr(pi_client, "resolve_pi_model", _fail_model_resolution)

    h = query_health(str(tmp_path / "missing.db"), "fresh")

    assert h["db_present"] is False
    assert h["last_cycle"] is None
    assert h["setup_blocker"]["kind"] == "provider"
    assert "No provider configured" in h["setup_blocker"]["reason"]
    assert "syke auth status" in h["setup_blocker"]["next_steps"]
    assert (
        "syke auth set <provider> --api-key <KEY> --model <model> --use"
        in h["setup_blocker"]["next_steps"]
    )
    assert "syke setup --agent" in h["setup_blocker"]["next_steps"]
    pi_agent.mkdir()
    (pi_agent / "settings.json").write_text('{"defaultProvider": "openai-codex"}\n')

    configured = query_health(str(tmp_path / "missing.db"), "fresh")

    assert configured["db_present"] is False
    assert configured["setup_blocker"] is None


def test_query_health_replaces_legacy_onboarding_persistence_with_live_service(
    tmp_path, monkeypatch
):
    from syke.cli_support import daemon_state
    from syke.onboarding import write_onboarding_state

    write_onboarding_state(
        "test_user",
        selected_sources=("codex",),
        total_files=1,
        estimated_minutes=1,
        estimate_method="test",
        mode="daemon",
        persistence={"manager": "cron", "keeps_daemon_alive": False},
    )
    live_persistence = {
        "manager": "systemd",
        "keeps_daemon_alive": True,
        "serves_timeline_while_idle": True,
    }
    monkeypatch.setattr(
        daemon_state,
        "daemon_payload",
        lambda: {
            "running": True,
            "registered": True,
            "persistence": live_persistence,
            "service": {"manager": "systemd", "running": True, "scheduled_only": False},
        },
    )

    health = query_health(str(tmp_path / "missing.db"), "test_user")

    assert health["onboarding"]["persistence"] == live_persistence
    assert health["onboarding"]["stored_persistence"]["manager"] == "cron"
    assert health["onboarding"]["persistence_source"] == "daemon_status"


def test_query_current_graph_reads_current_rows_without_a_timeline_boundary(tmp_path):
    db_path = tmp_path / "syke-current.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE links (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO memories VALUES
                ('mem-a', 'test_user', 'A', '2026-01-01T00:00:00+00:00', NULL),
                ('mem-b', 'test_user', 'B', '2026-01-02T00:00:00+00:00',
                 '2026-01-03T00:00:00+00:00');
            INSERT INTO links VALUES
                ('link-a-b', 'test_user', 'mem-a', 'mem-b', 'connected',
                 '2026-01-03T00:00:00+00:00');
            """
        )

    graph = query_current_graph(str(db_path), "test_user")

    assert graph["kind"] == "current_graph"
    assert graph["memory_count"] == 2
    assert graph["link_count"] == 1
    assert [memory["id"] for memory in graph["memories"]] == ["mem-b", "mem-a"]
    assert graph["links"] == [
        {
            "id": "link-a-b",
            "source_id": "mem-a",
            "target_id": "mem-b",
            "reason": "connected",
            "created_at": "2026-01-03T00:00:00+00:00",
        }
    ]


def test_cycle_and_ask_details_do_not_return_ordinary_graph_playback(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    timeline = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    cycle_event = next(event for event in timeline["events"] if event["kind"] == "cycle")
    ask_event = next(event for event in timeline["events"] if event["kind"] == "ask")

    cycle = query_cycle(str(db_path), user_id, cycle_event["id"])
    ask = query_ask(str(db_path), user_id, ask_event["id"])

    assert cycle is not None
    assert ask is not None
    assert "memories" not in cycle
    assert "links" not in cycle
    assert "memories" not in ask
    assert "links" not in ask


def test_query_timeline_ignores_orphan_memex_version_files(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path):
        baseline = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        artifact_at = datetime(2026, 4, 8, 7, 45, tzinfo=UTC)
        cycle_at = datetime(2026, 4, 8, 8, 0, tzinfo=UTC)
        accepted_cycle = str(uuid7())
        _write_cycle_receipt(
            accepted_cycle,
            started_at=(baseline - timedelta(minutes=1)).isoformat(),
            completed_at=baseline.isoformat(),
            memex_content="# MEMEX\n\nreal baseline\n",
        )
        from syke.runtime import workspace

        write_memex_version(
            workspace.CONTROL_ROOT,
            cycle_id="orphan-version",
            session_id="orphan-session",
            completed_at=artifact_at.isoformat(),
            content="# MEMEX\n\nsynthetic artifact\n",
            previous_content="# MEMEX\n\nreal baseline\n",
        )
        cycle_id = str(uuid7())
        _write_cycle_receipt(
            cycle_id,
            started_at=(cycle_at - timedelta(minutes=1)).isoformat(),
            completed_at=cycle_at.isoformat(),
        )

    t = query_timeline(
        str(db_path),
        user_id,
        (cycle_at + timedelta(minutes=10)).isoformat(),
        minutes=60,
    )
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")

    assert cycle["id"] == cycle_id
    assert cycle["memex_created_at"] == baseline.isoformat()
    assert cycle["memex_id"] == accepted_cycle
    assert cycle["memex_moved"] is False


def test_operation_summaries_bound_heavy_payloads_and_keep_routing_context(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    cycle_output = "x" * 50_000
    ask_output = "y" * 50_000
    with SykeDB(db_path):
        baseline = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        completed = datetime(2026, 4, 8, 7, 45, tzinfo=UTC)
        _write_cycle_receipt(
            str(uuid7()),
            started_at=(baseline - timedelta(minutes=1)).isoformat(),
            completed_at=baseline.isoformat(),
            memex_content="# MEMEX\n\nbaseline\n",
        )
        cycle_id = str(uuid7())
        session_id, _ = _write_session(
            db_path,
            operation_id=cycle_id,
            kind="synthesis",
            started_at=completed - timedelta(minutes=1),
            completed_at=completed,
            status="completed",
            tool_calls=[
                {
                    "name": "write",
                    "input": {"path": "MEMEX.md", "content": "# MEMEX\n\ncurrent route\n"},
                }
            ],
            output_text=cycle_output,
            model="gpt-5.4",
            input_tokens=10,
            output_tokens=5,
        )
        _write_cycle_receipt(
            cycle_id,
            started_at=(completed - timedelta(minutes=1)).isoformat(),
            completed_at=completed.isoformat(),
            session_id=session_id,
            memex_content="# MEMEX\n\ncurrent route\n",
            previous_memex_content="# MEMEX\n\nbaseline\n",
        )
        ask_id = str(uuid7())
        _write_session(
            db_path,
            operation_id=ask_id,
            kind="ask",
            started_at=completed,
            completed_at=completed,
            input_text="What is Syke?",
            output_text=ask_output,
        )

    summary = query_cycle(str(db_path), user_id, cycle_id, summary=True)
    full = query_cycle(str(db_path), user_id, cycle_id)
    ask_summary = query_ask(str(db_path), user_id, ask_id, summary=True)
    ask_full = query_ask(str(db_path), user_id, ask_id)

    assert summary is not None
    assert full is not None
    assert ask_summary is not None
    assert ask_full is not None
    assert summary["summary"] is True
    assert full["summary"] is False
    assert summary["memex"]["content"] == "# MEMEX\n\ncurrent route"
    assert summary["prev_memex"]["content"] == "# MEMEX\n\nbaseline"
    assert summary["trace"]["transcript"] == []
    assert summary["trace"]["tool_calls"] == []
    assert summary["trace"]["output_text"] == ""
    assert summary["trace"]["tool_calls_count"] == full["trace"]["tool_calls_count"]
    assert full["trace"]["transcript"]
    assert full["trace"]["output_text"] == cycle_output
    assert ask_summary["ask"]["input_text"] == "What is Syke?"
    assert ask_summary["ask"]["output_text"] == ""
    assert ask_summary["transcript"] == []
    assert ask_full["ask"]["output_text"] == ask_output


def test_query_timeline_sorts_mixed_offsets_by_instant(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        earlier, later = str(uuid7()), str(uuid7())
        _write_cycle_receipt(
            earlier,
            started_at="2026-05-12T10:00:00+01:00",
            completed_at="2026-05-12T10:00:00+01:00",
        )
        _write_cycle_receipt(
            later,
            started_at="2026-05-12T09:30:00+00:00",
            completed_at="2026-05-12T09:30:00+00:00",
        )
        db.conn.commit()

    t = query_timeline(str(db_path), user_id, "2026-05-12T11:00:00+00:00", minutes=180)
    events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert len(events) >= 2
    assert events[0]["id"] == later
    assert events[1]["id"] == earlier


def test_cycle_detail_uses_exact_native_session_name_for_same_second_cycles(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat()

    with SykeDB(db_path):
        first_cycle, second_cycle = str(uuid7()), str(uuid7())

        now_dt = datetime.fromisoformat(now_iso)
        first_session_id, _ = _write_session(
            db_path,
            operation_id=first_cycle,
            kind="synthesis",
            started_at=now_dt,
            completed_at=now_dt,
            status="completed",
            output_text="a",
            model="model-A",
        )
        second_session_id, _ = _write_session(
            db_path,
            operation_id=second_cycle,
            kind="synthesis",
            started_at=now_dt,
            completed_at=now_dt,
            status="completed",
            output_text="b",
            model="model-B",
        )
        _write_cycle_receipt(
            first_cycle,
            started_at=now_iso,
            completed_at=now_iso,
            session_id=first_session_id,
        )
        _write_cycle_receipt(
            second_cycle,
            started_at=now_iso,
            completed_at=now_iso,
            session_id=second_session_id,
        )

    end_iso = (datetime.fromisoformat(now_iso) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=60)
    cycle_events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert {e["model"] for e in cycle_events} == {"model-A", "model-B"}
    for event in cycle_events:
        detail = query_cycle(str(db_path), user_id, event["id"])
        assert detail is not None
        assert detail["trace"] is not None
        assert detail["trace"]["model"] == event["model"]


def test_cycle_detail_distinguishes_moved_and_held_memex(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path):
        baseline = datetime(2026, 4, 10, 10, 0, tzinfo=UTC)
        cycle_start = datetime(2026, 4, 10, 10, 4, tzinfo=UTC)
        cycle_end = datetime(2026, 4, 10, 10, 6, tzinfo=UTC)
        _write_cycle_receipt(
            str(uuid7()),
            started_at=(baseline - timedelta(minutes=1)).isoformat(),
            completed_at=baseline.isoformat(),
            memex_content="# MEMEX\n\n- baseline\n",
        )
        moved_cycle_id = str(uuid7())
        _write_cycle_receipt(
            moved_cycle_id,
            started_at=cycle_start.isoformat(),
            completed_at=cycle_end.isoformat(),
            memex_content="# MEMEX\n\n- baseline\n- new route\n",
            previous_memex_content="# MEMEX\n\n- baseline\n",
        )
        held_cycle_id = str(uuid7())
        _write_cycle_receipt(
            held_cycle_id,
            started_at=(cycle_end + timedelta(minutes=3)).isoformat(),
            completed_at=(cycle_end + timedelta(minutes=5)).isoformat(),
        )

    moved = query_cycle(str(db_path), user_id, moved_cycle_id)
    held = query_cycle(str(db_path), user_id, held_cycle_id)
    assert moved is not None
    assert held is not None
    assert moved["cycle"]["memex_moved"] is True
    assert "new route" in moved["memex"]["content"]
    assert "new route" not in moved["prev_memex"]["content"]
    assert held["cycle"]["memex_content_moved"] is False
    assert held["cycle"]["memex_moved"] is False
    assert held["prev_memex"]["content"] == held["memex"]["content"]
    assert "new route" in held["memex"]["content"]


def test_query_cycle_includes_failed_trace_error(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    cycle_id = str(uuid7())
    completed_dt = datetime.now(UTC) + timedelta(seconds=1)
    error = "Pi runtime failed: No Pi model is configured"
    session_id, _ = _write_session(
        db_path,
        operation_id=cycle_id,
        kind="synthesis",
        started_at=completed_dt,
        completed_at=completed_dt,
        status="failed",
        error=error,
        model="pi",
    )
    _write_cycle_receipt(
        cycle_id,
        started_at=completed_dt.isoformat(),
        status="failed",
        session_id=session_id,
    )

    detail = query_cycle(str(db_path), user_id, cycle_id)
    assert detail is not None
    assert detail["trace"] is not None
    assert detail["trace"]["status"] == "failed"
    assert detail["trace"]["error"] == error
