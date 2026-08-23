from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from syke.runtime.pi_sessions import (
    find_session_by_id,
    find_session_by_name,
    list_sessions,
    list_sessions_between,
    list_syke_sessions_between,
    read_session,
    session_history_status,
)


def _write_session(
    path: Path,
    *,
    session_id: str,
    name: str | None = None,
    failed: bool = False,
    stop_reason: str | None = None,
) -> None:
    entries: list[dict[str, object]] = [
        {
            "type": "session",
            "version": 3,
            "id": session_id,
            "timestamp": "2026-07-30T20:00:00.000Z",
            "cwd": "/Users/example/.syke/workspace",
        },
        {
            "type": "model_change",
            "id": "model-change",
            "timestamp": "2026-07-30T20:00:00.010Z",
            "provider": "openai-codex",
            "modelId": "gpt-5.5",
        },
    ]
    if name:
        entries.append(
            {
                "type": "session_info",
                "id": "session-info",
                "timestamp": "2026-07-30T20:00:00.020Z",
                "name": name,
            }
        )
    entries.extend(
        [
            {
                "type": "message",
                "id": "user-message",
                "timestamp": "2026-07-30T20:00:01.000Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Inspect current state."}],
                },
            },
            {
                "type": "message",
                "id": "assistant-message",
                "timestamp": "2026-07-30T20:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "provider": "openai-codex",
                    "model": "gpt-5.5",
                    "content": [
                        {"type": "thinking", "thinking": "Check the graph."},
                        {
                            "type": "toolCall",
                            "id": "call-1",
                            "name": "bash",
                            "arguments": {"command": "sqlite3 syke.db '.tables'"},
                        },
                        {"type": "text", "text": "State checked."},
                    ],
                    "usage": {
                        "input": 100,
                        "output": 20,
                        "cacheRead": 10,
                        "cacheWrite": 0,
                        "cost": {"total": 0.02},
                    },
                    "stopReason": "error" if failed else stop_reason or "stop",
                    "errorMessage": "provider failed" if failed else None,
                },
            },
            {
                "type": "message",
                "id": "tool-result",
                "timestamp": "2026-07-30T20:00:03.000Z",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call-1",
                    "toolName": "bash",
                    "content": [{"type": "text", "text": "memories links"}],
                    "isError": False,
                },
            },
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )


def test_read_session_projects_native_pi_history_without_copying_it(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-30T20-00-00-000Z_session-1.jsonl"
    _write_session(path, session_id="session-1", name="syke:ask:ask-1")

    session = read_session(path, include_transcript=True)

    assert session["id"] == "session-1"
    assert session["name"] == "syke:ask:ask-1"
    assert session["kind"] == "ask"
    assert session["operation_id"] == "ask-1"
    assert session["status"] == "completed"
    assert session["provider"] == "openai-codex"
    assert session["model"] == "gpt-5.5"
    assert session["input_text"] == "Inspect current state."
    assert session["output_text"] == "State checked."
    assert session["tool_name_counts"] == {"bash": 1}
    assert session["input_tokens"] == 100
    assert session["output_tokens"] == 20
    assert session["cache_read_tokens"] == 10
    assert session["cost_usd"] == 0.02
    assert session["duration_ms"] == 3000
    assert len(session["transcript"]) == 3


def test_read_session_preserves_native_failure(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-30T20-00-00-000Z_session-2.jsonl"
    _write_session(
        path,
        session_id="session-2",
        name="syke:synthesis:cycle-2",
        failed=True,
    )

    session = read_session(path)

    assert session["kind"] == "synthesis"
    assert session["operation_id"] == "cycle-2"
    assert session["status"] == "failed"
    assert session["error"] == "provider failed"
    assert "transcript" not in session


def test_read_session_marks_unfinished_tool_round_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-30T20-00-00-000Z_session-3.jsonl"
    _write_session(
        path,
        session_id="session-3",
        name="syke:synthesis:cycle-3",
        stop_reason="toolUse",
    )

    session = read_session(path)

    assert session["status"] == "incomplete"
    assert session["stop_reason"] == "toolUse"


def test_native_session_lookup_uses_id_and_embedded_name(tmp_path: Path) -> None:
    older = tmp_path / "2026-07-30T20-00-00-000Z_session-old.jsonl"
    newer = tmp_path / "2026-07-30T21-00-00-000Z_session-new.jsonl"
    _write_session(older, session_id="session-old", name="syke:ask:ask-old")
    _write_session(newer, session_id="session-new", name="syke:synthesis:cycle-new")

    by_id = find_session_by_id(tmp_path, "session-old")
    by_name = find_session_by_name(tmp_path, "syke:synthesis:cycle-new")
    recent = list_sessions(tmp_path, limit=1)

    assert by_id and by_id["name"] == "syke:ask:ask-old"
    assert by_name and by_name["id"] == "session-new"
    assert [item["id"] for item in recent] == ["session-new"]


def test_list_syke_sessions_between_opens_only_named_files_in_window(tmp_path: Path) -> None:
    before = tmp_path / "2026-07-30T19-59-59-000Z_session-before.jsonl"
    inside = tmp_path / "2026-07-30T20-10-00-000Z_session-inside.jsonl"
    unnamed = tmp_path / "2026-07-30T20-11-00-000Z_session-unnamed.jsonl"
    after = tmp_path / "2026-07-30T20-20-01-000Z_session-after.jsonl"
    _write_session(before, session_id="session-before", name="syke:ask:ask-before")
    _write_session(inside, session_id="session-inside", name="syke:synthesis:cycle-inside")
    _write_session(unnamed, session_id="session-unnamed")
    _write_session(after, session_id="session-after", name="syke:ask:ask-after")

    sessions = list_syke_sessions_between(
        tmp_path,
        start_at=datetime(2026, 7, 30, 20, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 30, 20, 20, tzinfo=UTC),
    )
    all_sessions = list_sessions_between(
        tmp_path,
        start_at=datetime(2026, 7, 30, 20, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 30, 20, 20, tzinfo=UTC),
    )

    assert [session["id"] for session in sessions] == ["session-inside"]
    assert {session["id"] for session in all_sessions} == {
        "session-inside",
        "session-unnamed",
    }


def test_session_history_status_is_read_only_availability_check(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    status = session_history_status(sessions)
    assert status == {
        "ok": False,
        "path": str(sessions.resolve()),
        "detail": f"Native Pi session history is not available at {sessions.resolve()}",
    }

    sessions.mkdir()
    status = session_history_status(sessions)
    assert status["ok"] is True
    assert "readable" in str(status["detail"])
