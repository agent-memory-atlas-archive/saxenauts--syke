"""Read-only projections over Pi's native JSONL operation history."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _transcript_turn(message: dict[str, Any]) -> dict[str, Any] | None:
    role = message.get("role")
    content = message.get("content")
    if role == "assistant" and isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"thinking", "reasoning"}:
                text = block.get("thinking") or block.get("text")
                if isinstance(text, str) and text:
                    blocks.append({"type": "thinking", "thinking": text})
            elif block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    blocks.append({"type": "text", "text": text})
            elif block_type == "toolCall":
                raw_input = block.get("arguments") or block.get("input") or {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "name": str(block.get("name") or block.get("toolName") or "tool"),
                        "input": raw_input if isinstance(raw_input, dict) else {},
                    }
                )
        return {"role": "assistant", "blocks": blocks} if blocks else None
    if role == "toolResult":
        return {
            "role": "user",
            "blocks": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.get("toolCallId"),
                    "tool_name": message.get("toolName"),
                    "content": _text_content(content),
                    "is_error": bool(message.get("isError", False)),
                }
            ],
        }
    if role == "user":
        text = _text_content(content)
        return {"role": "user", "blocks": [{"type": "text", "text": text}]} if text else None
    return None


def _operation_identity(name: str | None, session_id: str) -> tuple[str, str]:
    if name:
        for kind in ("ask", "synthesis"):
            prefix = f"syke:{kind}:"
            if name.startswith(prefix) and name.removeprefix(prefix):
                return kind, name.removeprefix(prefix)
    return "session", session_id


def read_session(path: str | Path, *, include_transcript: bool = False) -> dict[str, Any]:
    """Project one native Pi JSONL session into a bounded Python value."""
    session_path = Path(path).expanduser().resolve()
    session_id = session_path.stem.rsplit("_", 1)[-1]
    name: str | None = None
    cwd: str | None = None
    provider: str | None = None
    model: str | None = None
    response_id: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    input_text = ""
    output_text = ""
    started: datetime | None = None
    completed: datetime | None = None
    assistant_messages = 0
    event_count = 0
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    cost_usd = 0.0
    tool_calls: list[dict[str, Any]] = []
    transcript: list[dict[str, Any]] = []

    with session_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(entry, dict):
                continue
            event_count += 1
            recorded = _timestamp(entry.get("timestamp"))
            if recorded is not None:
                started = recorded if started is None or recorded < started else started
                completed = recorded if completed is None or recorded > completed else completed

            entry_type = entry.get("type")
            if entry_type == "session":
                raw_id = entry.get("id")
                if isinstance(raw_id, str) and raw_id:
                    session_id = raw_id
                raw_cwd = entry.get("cwd")
                if isinstance(raw_cwd, str):
                    cwd = raw_cwd
                continue
            if entry_type == "session_info":
                raw_name = entry.get("name")
                if isinstance(raw_name, str) and raw_name:
                    name = raw_name
                continue
            if entry_type == "model_change":
                raw_provider = entry.get("provider")
                raw_model = entry.get("modelId")
                if isinstance(raw_provider, str):
                    provider = raw_provider
                if isinstance(raw_model, str):
                    model = raw_model
                continue
            if entry_type != "message":
                continue

            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            if include_transcript:
                turn = _transcript_turn(message)
                if turn is not None:
                    transcript.append(turn)
            if message.get("role") == "user" and not input_text:
                input_text = _text_content(message.get("content"))
            if message.get("role") != "assistant":
                continue

            assistant_messages += 1
            raw_provider = message.get("provider")
            raw_model = message.get("model")
            if isinstance(raw_provider, str):
                provider = raw_provider
            if isinstance(raw_model, str):
                model = raw_model
            raw_response_id = message.get("responseId")
            if isinstance(raw_response_id, str):
                response_id = raw_response_id
            raw_stop_reason = message.get("stopReason")
            if isinstance(raw_stop_reason, str):
                stop_reason = raw_stop_reason
            raw_error = message.get("errorMessage")
            if isinstance(raw_error, str) and raw_error:
                error = raw_error

            content = message.get("content")
            text = _text_content(content)
            if text:
                output_text = text
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "toolCall":
                        continue
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "name": str(block.get("name") or block.get("toolName") or "tool"),
                            "input": block.get("arguments") or block.get("input") or {},
                        }
                    )

            usage = message.get("usage")
            if isinstance(usage, dict):
                input_tokens += int(usage.get("input") or 0)
                output_tokens += int(usage.get("output") or 0)
                cache_read_tokens += int(usage.get("cacheRead") or 0)
                cache_write_tokens += int(usage.get("cacheWrite") or 0)
                cost = usage.get("cost")
                if isinstance(cost, dict):
                    cost_usd += float(cost.get("total") or 0.0)

    kind, operation_id = _operation_identity(name, session_id)
    status = "failed" if error or stop_reason == "error" else "completed"
    if assistant_messages == 0 or stop_reason in {None, "toolUse"}:
        status = "incomplete"
    duration_ms = 0
    if started is not None and completed is not None:
        duration_ms = max(0, int((completed - started).total_seconds() * 1000))
    tool_name_counts = Counter(call["name"] for call in tool_calls)

    result: dict[str, Any] = {
        "id": session_id,
        "name": name,
        "kind": kind,
        "operation_id": operation_id,
        "path": str(session_path),
        "cwd": cwd,
        "started_at": started.isoformat() if started else None,
        "completed_at": completed.isoformat() if completed else None,
        "status": status,
        "error": error,
        "input_text": input_text,
        "output_text": output_text,
        "provider": provider,
        "model": model,
        "response_id": response_id,
        "stop_reason": stop_reason,
        "num_turns": assistant_messages,
        "event_count": event_count,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "tool_calls": tool_calls,
        "tool_calls_count": len(tool_calls),
        "tool_name_counts": dict(tool_name_counts),
    }
    if include_transcript:
        result["transcript"] = transcript
    return result


def _session_paths(session_dir: str | Path) -> list[Path]:
    root = Path(session_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    paths = list(root.glob("*.jsonl"))
    return sorted(
        paths,
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def _session_path_started_at(path: Path) -> datetime | None:
    stamp = path.name.split("_", 1)[0]
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S-%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def list_syke_sessions_between(
    session_dir: str | Path,
    *,
    start_at: datetime,
    end_at: datetime,
    include_transcript: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read Syke ask/synthesis sessions whose native filenames fall in a time window."""
    start_utc = (
        start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at.astimezone(UTC)
    )
    end_utc = end_at.replace(tzinfo=UTC) if end_at.tzinfo is None else end_at.astimezone(UTC)
    if start_utc > end_utc:
        return []

    sessions: list[dict[str, Any]] = []
    for path in _session_paths(session_dir):
        path_started_at = _session_path_started_at(path)
        if path_started_at is not None and not (start_utc <= path_started_at <= end_utc):
            continue
        try:
            session = read_session(path, include_transcript=include_transcript)
        except OSError:
            continue
        if path_started_at is None:
            session_started_at = _timestamp(session.get("started_at"))
            if session_started_at is None or not (start_utc <= session_started_at <= end_utc):
                continue
        if session["kind"] not in {"ask", "synthesis"}:
            continue
        sessions.append(session)
        if limit is not None and len(sessions) >= limit:
            break
    return sessions


def list_sessions(
    session_dir: str | Path,
    *,
    limit: int | None = 50,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent native sessions, newest first."""
    sessions: list[dict[str, Any]] = []
    for path in _session_paths(session_dir):
        try:
            session = read_session(path)
        except OSError:
            continue
        if kind and session["kind"] != kind:
            continue
        sessions.append(session)
        if limit is not None and len(sessions) >= limit:
            break
    return sessions


def find_session_by_id(
    session_dir: str | Path,
    session_id: str,
    *,
    include_transcript: bool = False,
) -> dict[str, Any] | None:
    """Find a native session using its Pi session ID."""
    root = Path(session_dir).expanduser().resolve()
    for path in root.glob(f"*_{session_id}.jsonl"):
        try:
            return read_session(path, include_transcript=include_transcript)
        except OSError:
            return None
    return None


def find_session_by_name(
    session_dir: str | Path,
    name: str,
    *,
    include_transcript: bool = False,
) -> dict[str, Any] | None:
    """Find a Syke-named Pi session without maintaining a second index."""
    for path in _session_paths(session_dir):
        try:
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= 16:
                        break
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if (
                        isinstance(entry, dict)
                        and entry.get("type") == "session_info"
                        and entry.get("name") == name
                    ):
                        return read_session(path, include_transcript=include_transcript)
        except OSError:
            continue
    return None


def session_history_status(session_dir: str | Path) -> dict[str, object]:
    """Report whether native Pi history is available for read-only inspection."""
    path = Path(session_dir).expanduser().resolve()
    readable = path.is_dir() and os.access(path, os.R_OK)
    detail = (
        f"Native Pi session history is readable at {path}"
        if readable
        else f"Native Pi session history is not available at {path}"
    )
    return {"ok": readable, "path": str(path), "detail": detail}


def list_sessions_between(
    session_dir: str | Path,
    *,
    start_at: datetime,
    end_at: datetime,
    include_transcript: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read native sessions whose filenames fall in a time window."""
    start_utc = (
        start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at.astimezone(UTC)
    )
    end_utc = end_at.replace(tzinfo=UTC) if end_at.tzinfo is None else end_at.astimezone(UTC)
    if start_utc > end_utc:
        return []

    sessions: list[dict[str, Any]] = []
    for path in _session_paths(session_dir):
        path_started_at = _session_path_started_at(path)
        if path_started_at is not None and not (start_utc <= path_started_at <= end_utc):
            continue
        try:
            session = read_session(path, include_transcript=include_transcript)
        except OSError:
            continue
        if path_started_at is None:
            session_started_at = _timestamp(session.get("started_at"))
            if session_started_at is None or not (start_utc <= session_started_at <= end_utc):
                continue
        sessions.append(session)
        if limit is not None and len(sessions) >= limit:
            break
    return sessions
