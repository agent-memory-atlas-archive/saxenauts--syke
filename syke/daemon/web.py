"""Local read-only HTTP server for the Syke timeline UI.

Runs inside the daemon, bound strictly to 127.0.0.1. Graph queries open the one
SQLite database read-only; operation evidence comes from protected receipt and
native-session files.

Threat floor: personal-machine.
- Loopback bind only.
- Host header validated (defends against DNS rebinding).
- No CORS, strict CSP, no third-party fetches.
- No write endpoints. Period.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from syke.config import user_control_dir, user_syke_db_path
from syke.control import get_receipt, list_receipts
from syke.db import DatabaseMaintenanceError
from syke.db_access import acquire_database_lease, maintenance_marker_path
from syke.memory.memex_history import load_accepted_memex_versions
from syke.runtime.pi_sessions import (
    find_session_by_id,
    find_session_by_name,
    list_syke_sessions_between,
)

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
# Keep this high enough for multi-month historical timelines.
TIMELINE_MAX = 5000
LOG_LINES_MAX = 500
DAEMON_LOG_PATH = Path(os.path.expanduser("~/.config/syke/daemon.log"))
RESIDENT_SERVICE_MANAGERS = {"launchd", "systemd"}


@contextmanager
def _open_ro(db_path: str) -> Iterator[sqlite3.Connection]:
    """Open the free graph read-only."""
    lease = acquire_database_lease(db_path)
    try:
        marker = maintenance_marker_path(db_path)
        if marker.exists():
            raise DatabaseMaintenanceError(
                f"Syke database is unavailable during maintenance: {marker}"
            )

        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        lease.release()


def _iso_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    # Query strings can come through as `2026-05-12T22:00:00 00:00` when
    # callers forgot to URL-escape the `+` in timezone offsets. Normalizing
    # this form preserves compatibility with existing callers.
    s = s.strip()
    if " " in s:
        s = re.sub(r"\s(\d{2}:\d{2})$", r"+\1", s)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_to_utc_dt(s: str | None) -> datetime | None:
    dt = _iso_to_dt(s)
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _session_dir(user_id: str) -> Path:
    return user_control_dir(user_id) / "sessions"


def _operation_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    """Expose operation facts without projecting ordinary graph history."""
    summary = {
        key: receipt.get(key)
        for key in (
            "id",
            "started_at",
            "completed_at",
            "status",
            "session_id",
            "acknowledged_record_ids",
            "error",
            "reason",
        )
        if key in receipt
    }
    memex_version = receipt.get("memex_version")
    if isinstance(memex_version, dict):
        summary["memex_version"] = {
            key: memex_version.get(key) for key in ("path", "sha256") if key in memex_version
        }
    summary["memex_updated"] = bool(receipt.get("memex_updated") or memex_version)

    recovery = receipt.get("recovery")
    if isinstance(recovery, dict):
        summary["recovery"] = {
            key: recovery.get(key)
            for key in ("restored", "recovery_point", "status", "id")
            if key in recovery
        }
    elif recovery is not None:
        summary["recovery"] = recovery
    else:
        # Old receipts remain readable for their final rollback verdict, but
        # their graph deltas never cross the API boundary.
        state_change = receipt.get("state_change")
        if isinstance(state_change, dict) and state_change.get("graph_outcome") == "restored":
            summary["recovery"] = {"restored": True}
    return summary


def _session_trace(session: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    transcript = session.get("transcript") if include_content else []
    if not isinstance(transcript, list):
        transcript = []
    thinking = [
        block.get("thinking")
        for turn in transcript
        if isinstance(turn, dict)
        for block in turn.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "thinking"
    ]
    return {
        "session_id": session.get("id"),
        "session_path": session.get("path"),
        "transcript": transcript,
        "thinking": thinking,
        "tool_calls": session.get("tool_calls", []) if include_content else [],
        "tool_name_counts": session.get("tool_name_counts") or {},
        "tool_calls_count": int(session.get("tool_calls_count") or 0),
        "num_turns": int(session.get("num_turns") or 0),
        "output_text": session.get("output_text", "") if include_content else "",
        "error": session.get("error") or "",
        "input_tokens": int(session.get("input_tokens") or 0),
        "output_tokens": int(session.get("output_tokens") or 0),
        "cache_read_tokens": int(session.get("cache_read_tokens") or 0),
        "duration_ms": int(session.get("duration_ms") or 0),
        "cost_usd": float(session.get("cost_usd") or 0),
        "provider": session.get("provider"),
        "model": session.get("model"),
        "status": session.get("status"),
    }


def _accepted_memex_states(
    control_dir: Path, receipts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return validated accepted MEMEX states oldest first."""
    return load_accepted_memex_versions(control_dir, receipts)


def _latest_memex_state(
    states: list[dict[str, Any]], boundary: object
) -> tuple[int, dict[str, Any] | None]:
    """Select the latest validated MEMEX state at or before a receipt boundary."""
    boundary_dt = _iso_to_utc_dt(str(boundary or ""))
    if boundary_dt is None:
        return -1, None
    latest_index = -1
    for index, state in enumerate(states):
        state_dt = _iso_to_utc_dt(str(state.get("completed_at") or ""))
        if state_dt is None or state_dt > boundary_dt:
            break
        latest_index = index
    return latest_index, states[latest_index] if latest_index >= 0 else None


# ─── Query layer ─────────────────────────────────────────────────────────────


def query_current_graph(db_path: str, user_id: str) -> dict[str, Any]:
    """Return the current ordinary graph with no timeline boundary."""
    result: dict[str, Any] = {
        "kind": "current_graph",
        "user_id": user_id,
        "as_of": datetime.now(UTC).isoformat(),
        "db_present": Path(db_path).exists(),
        "memory_count": 0,
        "link_count": 0,
        "memories": [],
        "links": [],
    }
    if not result["db_present"]:
        return result

    try:
        with _open_ro(db_path) as conn:
            # Keep both reads on one SQLite snapshot. The current graph contains
            # current ordinary rows only, so no lifecycle or time filter belongs here.
            conn.execute("BEGIN")
            memory_rows = conn.execute(
                """SELECT id, content, created_at, updated_at
                   FROM memories
                   WHERE user_id = ?
                   ORDER BY datetime(created_at) DESC, id DESC""",
                (user_id,),
            ).fetchall()
            link_rows = conn.execute(
                """SELECT id, source_id, target_id, reason, created_at
                   FROM links
                   WHERE user_id = ?
                   ORDER BY datetime(created_at) DESC, id DESC""",
                (user_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result

    memories = [dict(row) for row in memory_rows]
    links = [dict(row) for row in link_rows]
    result.update(
        {
            "memory_count": len(memories),
            "link_count": len(links),
            "memories": memories,
            "links": links,
        }
    )
    return result


def query_timeline(db_path: str, user_id: str, end_iso: str, *, minutes: int) -> dict[str, Any]:
    """Return cycles + asks within (end - minutes, end], newest first.

    Lightweight rows only — detail is fetched per-event on click. The window
    is expressed in minutes so the scrubber can zoom from 1 hour through
    multi-week ranges with one parameter.
    """
    end_dt = _iso_to_dt(end_iso) or datetime.now(UTC)
    end_dt_utc = end_dt.astimezone(UTC) if end_dt.tzinfo is not None else end_dt.replace(tzinfo=UTC)
    start_dt = end_dt_utc - timedelta(minutes=minutes)
    start_iso = start_dt.isoformat()
    end_iso_norm = end_dt_utc.isoformat()

    _ = db_path
    events: list[dict[str, Any]] = []
    control_dir = user_control_dir(user_id)
    receipts = list_receipts(control_dir)
    memex_states = _accepted_memex_states(control_dir, receipts)
    versions_by_cycle = {
        str(state["cycle_id"]): state for state in memex_states if state.get("cycle_id") is not None
    }
    rows = []
    for receipt in receipts:
        display_dt = _iso_to_utc_dt(
            str(receipt.get("completed_at") or receipt.get("started_at") or "")
        )
        if display_dt is None or not (start_dt < display_dt <= end_dt_utc):
            continue
        row = dict(receipt)
        row["display_at"] = receipt.get("completed_at") or receipt.get("started_at")
        rows.append(row)
        if len(rows) >= TIMELINE_MAX:
            break
    session_start = start_dt
    for row in rows:
        cycle_started_at = _iso_to_utc_dt(str(row.get("started_at") or ""))
        if cycle_started_at is not None and cycle_started_at < session_start:
            session_start = cycle_started_at
    native_sessions = list_syke_sessions_between(
        _session_dir(user_id),
        start_at=session_start,
        end_at=end_dt_utc,
        limit=TIMELINE_MAX,
    )
    synthesis_by_cycle: dict[str, dict[str, Any]] = {}
    synthesis_by_session: dict[str, dict[str, Any]] = {}
    for session in native_sessions:
        if session.get("kind") == "synthesis":
            synthesis_by_cycle.setdefault(str(session.get("operation_id") or ""), session)
            synthesis_by_session.setdefault(str(session.get("id") or ""), session)

    for row in rows:
        _, memex_state = _latest_memex_state(memex_states, row["display_at"])
        exact_version = versions_by_cycle.get(str(row.get("id") or ""))
        session = synthesis_by_session.get(str(row.get("session_id") or ""))
        if session is None:
            session = synthesis_by_cycle.get(str(row.get("id") or ""))
        events.append(
            {
                "kind": "cycle",
                "id": row.get("id"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "display_at": row["display_at"],
                "status": row.get("status"),
                "memex_id": memex_state.get("cycle_id") if memex_state else None,
                "memex_created_at": (
                    memex_state.get("completed_at") or memex_state.get("captured_at")
                    if memex_state
                    else None
                ),
                "memex_updated": bool(exact_version),
                "memex_moved": bool(exact_version),
                "memex_content_moved": bool(exact_version),
                "duration_ms": int(session.get("duration_ms") or 0) if session else 0,
                "cost_usd": float(session.get("cost_usd") or 0) if session else 0,
                "model": session.get("model") if session else None,
                "num_turns": int(session.get("num_turns") or 0) if session else 0,
                "tool_calls_count": int(session.get("tool_calls_count") or 0) if session else 0,
                "session_id": session.get("id") if session else None,
                "session_path": session.get("path") if session else None,
            }
        )

    for session in native_sessions:
        if session.get("kind") != "ask":
            continue
        started = _iso_to_utc_dt(session.get("started_at"))
        if started is None or not (start_dt < started <= end_dt_utc):
            continue
        preview = str(session.get("output_text") or "").strip().split("\n", 1)[0][:120]
        events.append(
            {
                "kind": "ask",
                "id": session.get("operation_id"),
                "session_id": session.get("id"),
                "session_path": session.get("path"),
                "started_at": session.get("started_at"),
                "completed_at": session.get("completed_at"),
                "display_at": session.get("completed_at") or session.get("started_at"),
                "status": session.get("status"),
                "duration_ms": int(session.get("duration_ms") or 0),
                "cost_usd": float(session.get("cost_usd") or 0),
                "model": session.get("model"),
                "num_turns": int(session.get("num_turns") or 0),
                "tool_calls_count": int(session.get("tool_calls_count") or 0),
                "preview": preview,
            }
        )

    def _event_sort_dt(event: dict[str, Any]) -> datetime:
        for key in ("display_at", "completed_at", "started_at"):
            value = event.get(key)
            if isinstance(value, str):
                parsed = _iso_to_utc_dt(value)
                if parsed is not None:
                    return parsed
        return datetime.min.replace(tzinfo=UTC)

    events.sort(key=_event_sort_dt, reverse=True)
    return {
        "user_id": user_id,
        "window": {
            "start": start_iso,
            "end": end_iso_norm,
            "minutes": minutes,
            "days": round(minutes / 1440, 4),
        },
        "count": len(events),
        "events": events,
    }


def query_cycle(
    db_path: str,
    user_id: str,
    cycle_id: str,
    *,
    summary: bool | str = False,
) -> dict[str, Any] | None:
    """Return one host receipt with accepted MEMEX history and its native session."""
    _ = db_path
    summary_mode = "memex" if summary else "full"
    control_dir = user_control_dir(user_id)
    cycle = get_receipt(control_dir, cycle_id)
    if cycle is None:
        return None
    completed_at = cycle.get("completed_at") or cycle["started_at"]
    receipts = list_receipts(control_dir)
    session_id = cycle.get("session_id")
    session = (
        find_session_by_id(
            _session_dir(user_id),
            str(session_id),
            include_transcript=summary_mode == "full",
        )
        if isinstance(session_id, str) and session_id
        else None
    )
    if session is None:
        session = find_session_by_name(
            _session_dir(user_id),
            f"syke:synthesis:{cycle_id}",
            include_transcript=summary_mode == "full",
        )
    cycle_info = _operation_summary(cycle)
    if session:
        cycle_info.update(
            {
                "duration_ms": int(session.get("duration_ms") or 0),
                "cost_usd": float(session.get("cost_usd") or 0),
                "input_tokens": int(session.get("input_tokens") or 0),
                "output_tokens": int(session.get("output_tokens") or 0),
                "cache_read_tokens": int(session.get("cache_read_tokens") or 0),
                "model": session.get("model"),
            }
        )
    memex_states = _accepted_memex_states(control_dir, receipts)
    current_index, current_state = _latest_memex_state(memex_states, completed_at)
    exact_version = next(
        (state for state in memex_states if state.get("cycle_id") == cycle_id),
        None,
    )
    memex_moved = exact_version is not None
    if memex_moved:
        current_state = exact_version
        current_index = memex_states.index(exact_version)
        previous_state = memex_states[current_index - 1] if current_index > 0 else None
    else:
        previous_state = current_state
    memex_content = str(current_state.get("content") or "") if current_state else ""
    memex_created_at = current_state.get("completed_at") if current_state else None
    previous_memex_content = str(previous_state.get("content") or "") if previous_state else ""
    cycle_info["memex_id"] = current_state.get("cycle_id") if current_state else None
    cycle_info["memex_created_at"] = memex_created_at
    cycle_info["memex_moved"] = memex_moved
    cycle_info["memex_content_moved"] = memex_moved
    trace = _session_trace(session, include_content=summary_mode == "full") if session else None
    return {
        "kind": "cycle",
        "summary": bool(summary),
        "cycle": cycle_info,
        "memex": {"content": memex_content, "created_at": memex_created_at},
        "prev_memex": {"content": previous_memex_content},
        "trace": trace,
    }


def query_ask(
    db_path: str,
    user_id: str,
    ask_id: str,
    *,
    summary: bool | str = False,
) -> dict[str, Any] | None:
    """Return full detail for a single native ask session.

    Ordinary graph state is intentionally absent; the graph has its own
    boundary-free current-state endpoint.
    """
    _ = db_path
    summary_mode = "memex" if summary else "full"
    sessions = _session_dir(user_id)
    ask = find_session_by_name(
        sessions,
        f"syke:ask:{ask_id}",
        include_transcript=summary_mode == "full",
    )
    if ask is None:
        ask = find_session_by_id(
            sessions,
            ask_id,
            include_transcript=summary_mode == "full",
        )
    if ask is None or ask.get("kind") != "ask":
        return None

    transcript = ask.get("transcript") if summary_mode == "full" else []
    if not isinstance(transcript, list):
        transcript = []
    trace = _session_trace(ask, include_content=summary_mode == "full")
    return {
        "kind": "ask",
        "summary": bool(summary),
        "ask": {
            "id": ask.get("operation_id"),
            "session_id": ask.get("id"),
            "session_path": ask.get("path"),
            "started_at": ask.get("started_at"),
            "completed_at": ask.get("completed_at"),
            "status": ask.get("status"),
            "input_text": str(ask.get("input_text") or ""),
            "output_text": str(ask.get("output_text") or "") if summary_mode == "full" else "",
            "model": ask.get("model"),
            "num_turns": int(ask.get("num_turns") or 0),
            "duration_ms": int(ask.get("duration_ms") or 0),
            "cost_usd": float(ask.get("cost_usd") or 0),
            "input_tokens": int(ask.get("input_tokens") or 0),
            "output_tokens": int(ask.get("output_tokens") or 0),
        },
        "transcript": trace["transcript"],
        "thinking": trace["thinking"],
        "tool_calls": trace["tool_calls"],
    }


def query_log_tail(lines: int) -> dict[str, Any]:
    """Tail the daemon log. Bounded, no full-file load."""
    n = min(max(lines, 1), LOG_LINES_MAX)
    if not DAEMON_LOG_PATH.exists():
        return {"path": str(DAEMON_LOG_PATH), "lines": [], "exists": False}
    try:
        with DAEMON_LOG_PATH.open("rb") as fh:
            buf: deque[bytes] = deque(maxlen=n)
            for raw in fh:
                buf.append(raw.rstrip(b"\n"))
        return {
            "path": str(DAEMON_LOG_PATH),
            "lines": [b.decode("utf-8", errors="replace") for b in buf],
            "exists": True,
        }
    except OSError as exc:
        return {"path": str(DAEMON_LOG_PATH), "lines": [], "exists": True, "error": str(exc)}


def _provider_setup_blocker() -> dict[str, Any] | None:
    provider_id = os.getenv("SYKE_PROVIDER", "").strip()
    if not provider_id:
        try:
            from syke.pi_state import get_pi_settings_path

            settings_path = get_pi_settings_path()
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                raw_provider = (
                    settings.get("defaultProvider") if isinstance(settings, dict) else None
                )
                if isinstance(raw_provider, str):
                    provider_id = raw_provider.strip()
        except (OSError, json.JSONDecodeError):
            provider_id = ""

    if provider_id:
        return None

    return {
        "kind": "provider",
        "reason": (
            "No provider configured. Run `syke setup`, `syke auth use <provider>`, "
            "or `syke auth set <provider> ... --use`."
        ),
        "next_steps": [
            "syke auth status",
            "syke auth set <provider> --api-key <KEY> --model <model> --use",
            "syke auth login <provider> --use",
            "syke setup --agent",
            "syke sync",
        ],
    }


def _onboarding_with_current_persistence(user_id: str) -> dict[str, Any] | None:
    from syke.onboarding import read_onboarding_state

    onboarding = read_onboarding_state(user_id)
    if not onboarding:
        return onboarding

    stored_persistence = onboarding.get("persistence")
    if not isinstance(stored_persistence, dict):
        stored_persistence = {}
    stored_manager = str(stored_persistence.get("manager") or "")
    stored_is_resident = (
        stored_manager in RESIDENT_SERVICE_MANAGERS
        and stored_persistence.get("keeps_daemon_alive") is True
        and stored_persistence.get("serves_timeline_while_idle") is not False
    )
    if stored_is_resident:
        return onboarding

    try:
        from syke.cli_support.daemon_state import daemon_payload

        daemon = daemon_payload()
    except Exception:
        return onboarding

    service = daemon.get("service")
    if not isinstance(service, dict):
        return onboarding
    live_persistence = daemon.get("persistence")
    if not isinstance(live_persistence, dict):
        return onboarding
    live_manager = str(service.get("manager") or live_persistence.get("manager") or "")
    live_is_resident = (
        live_manager in RESIDENT_SERVICE_MANAGERS
        and not service.get("scheduled_only")
        and (service.get("registered") or service.get("running") or daemon.get("running"))
    )
    if not live_is_resident or live_persistence == stored_persistence:
        return onboarding

    updated = dict(onboarding)
    updated["stored_persistence"] = dict(stored_persistence)
    updated["persistence"] = dict(live_persistence)
    updated["persistence_source"] = "daemon_status"
    return updated


def query_health(db_path: str, user_id: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "user_id": user_id,
        "db_path": db_path,
        "db_present": Path(db_path).exists(),
        "log_path": str(DAEMON_LOG_PATH),
        "now": datetime.now(UTC).isoformat(),
        "last_cycle": None,
        "last_completed_cycle": None,
        "memex_updated_at": None,
        "onboarding": _onboarding_with_current_persistence(user_id),
        "setup_blocker": _provider_setup_blocker(),
    }
    receipts = list_receipts(user_control_dir(user_id))
    if receipts:
        info["last_cycle"] = _operation_summary(receipts[0])
    completed = next(
        (receipt for receipt in receipts if receipt.get("status") == "completed"),
        None,
    )
    if completed:
        info["last_completed_cycle"] = _operation_summary(completed)
    if not info["db_present"]:
        return info
    try:
        with _open_ro(db_path) as conn:
            r3 = conn.execute(
                "SELECT created_at, updated_at FROM current_memex "
                "WHERE singleton = 1 AND user_id = ?",
                (user_id,),
            ).fetchone()
            if r3:
                info["memex_updated_at"] = r3["updated_at"] or r3["created_at"]
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    return info


# ─── HTTP handler ────────────────────────────────────────────────────────────


_HOST_RE = re.compile(r"^([^:]+|\[[^\]]+\])(:\d+)?$")


def _extract_host(host_header: str | None) -> str:
    if not host_header:
        return ""
    m = _HOST_RE.match(host_header.strip())
    if not m:
        return ""
    return m.group(1).lower()


def _security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    )


def make_handler(user_id: str, html_path: Path) -> type[BaseHTTPRequestHandler]:
    db_path_factory = lambda: str(user_syke_db_path(user_id))  # noqa: E731

    class WebHandler(BaseHTTPRequestHandler):
        # Suppress default access logging (daemon log already captures lifecycle).
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)

        def _send_text(
            self, status: int, body: str, ctype: str = "text/plain; charset=utf-8"
        ) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(data)

        def _send_empty(self, status: int, ctype: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", "0")
            _security_headers(self)
            self.end_headers()

        def _check_host(self) -> bool:
            host = _extract_host(self.headers.get("Host"))
            if host in ALLOWED_HOSTS:
                return True
            self._send_json(403, {"error": "host not allowed"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._check_host():
                return
            try:
                self._route()
            except Exception as exc:
                logger.error("web: handler error: %s", exc, extra={"tag": "WEB"})
                try:
                    self._send_json(500, {"error": "internal error"})
                except Exception:
                    pass

        def _route(self) -> None:
            from urllib.parse import parse_qs, urlsplit

            parts = urlsplit(self.path)
            path = parts.path
            qs = parse_qs(parts.query)

            if path == "/" or path == "/index.html":
                if not html_path.exists():
                    self._send_text(500, "UI bundle missing")
                    return
                self._send_text(
                    200, html_path.read_text(encoding="utf-8"), ctype="text/html; charset=utf-8"
                )
                return

            if path == "/favicon.ico":
                self._send_empty(204, "image/x-icon")
                return

            if path == "/api/health":
                self._send_json(200, query_health(db_path_factory(), user_id))
                return

            if path == "/api/current-graph":
                self._send_json(200, query_current_graph(db_path_factory(), user_id))
                return

            if path == "/api/timeline":
                end_iso = (qs.get("end") or [datetime.now(UTC).isoformat()])[0]
                # `minutes` is the canonical window parameter; `days` stays as a
                # convenience alias so old links keep working.
                minutes_param = qs.get("minutes")
                if minutes_param:
                    try:
                        minutes = int(minutes_param[0])
                    except ValueError:
                        minutes = 60 * 24 * 7
                else:
                    try:
                        minutes = int(float((qs.get("days") or ["7"])[0]) * 1440)
                    except ValueError:
                        minutes = 60 * 24 * 7
                # Clamp: 5 minutes up to 2 years for long-horizon timelines.
                minutes = max(5, min(60 * 24 * 730, minutes))
                self._send_json(
                    200,
                    query_timeline(db_path_factory(), user_id, end_iso, minutes=minutes),
                )
                return

            m = re.match(r"^/api/cycle/([A-Za-z0-9_.:-]+)$", path)
            if m:
                summary_param = ((qs.get("summary") or ["0"])[0]).lower()
                summary = summary_param in {"1", "true", "yes", "memory"}
                detail = query_cycle(db_path_factory(), user_id, m.group(1), summary=summary)
                if detail is None:
                    self._send_json(404, {"error": "cycle not found"})
                else:
                    self._send_json(200, detail)
                return

            m = re.match(r"^/api/ask/([0-9a-fA-F\-]{8,})$", path)
            if m:
                summary_param = ((qs.get("summary") or ["0"])[0]).lower()
                summary = summary_param in {"1", "true", "yes", "memory"}
                detail = query_ask(db_path_factory(), user_id, m.group(1), summary=summary)
                if detail is None:
                    self._send_json(404, {"error": "ask not found"})
                else:
                    self._send_json(200, detail)
                return

            if path == "/api/log/tail":
                try:
                    lines = max(1, min(LOG_LINES_MAX, int((qs.get("lines") or ["200"])[0])))
                except ValueError:
                    lines = 200
                self._send_json(200, query_log_tail(lines))
                return

            self._send_json(404, {"error": "not found"})

    return WebHandler


# ─── Server lifecycle ────────────────────────────────────────────────────────


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class SykeWebServer:
    """Local read-only timeline server. Runs in a daemon thread."""

    def __init__(self, user_id: str, port: int, html_path: Path):
        self.user_id = user_id
        self.port = port
        self.html_path = html_path
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> bool:
        try:
            handler = make_handler(self.user_id, self.html_path)
            # Explicit loopback bind. Refuse to bind to anything else even if env is wrong.
            self._server = _Server(("127.0.0.1", self.port), handler)
        except OSError as exc:
            logger.info("Web server disabled: bind failed on 127.0.0.1:%s (%s)", self.port, exc)
            self._server = None
            return False

        def _serve() -> None:
            try:
                assert self._server is not None
                self._server.serve_forever(poll_interval=0.05)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("web server crashed: %s", exc, extra={"tag": "WEB"})

        self._thread = threading.Thread(target=_serve, name="syke-web", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as exc:
                logger.debug("web server shutdown: %s", exc, exc_info=True)
            self._server = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None


def web_server_status(port: int, *, timeout: float = 0.25) -> dict[str, Any]:
    """Probe whether the local web server is reachable on 127.0.0.1:port."""
    info: dict[str, Any] = {
        "ok": False,
        "url": f"http://127.0.0.1:{port}/",
        "reachable": False,
        "detail": None,
    }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(("127.0.0.1", port))
        info["reachable"] = True
        info["ok"] = True
        info["detail"] = f"web server reachable at {info['url']}"
    except OSError as exc:
        info["detail"] = str(exc)
    return info
