"""Observe — the system watching itself.

Reads protected host receipts, native Pi sessions, and runtime state. Returns
structured data with raw numbers and qualitative assessments for both humans
and agents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from syke.config import user_control_dir
from syke.control import list_receipts, receipt_rollup


def _hours_ago(iso_timestamp: str | None) -> float | None:
    if not iso_timestamp:
        return None
    try:
        then = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return round((now - then).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None


def _human_ago(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        mins = int(hours * 60)
        return f"{mins}m ago" if mins > 0 else "just now"
    if hours < 24:
        return f"{hours:.0f}h ago"
    days = hours / 24
    if days < 7:
        return f"{days:.0f}d ago"
    weeks = days / 7
    return f"{weeks:.0f}w ago"


def _assess_staleness(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 2:
        return "fresh"
    if hours < 12:
        return "healthy"
    if hours < 24:
        return "ok"
    if hours < 48:
        return "stale"
    return "dead"


def memory_health(db, user_id: str) -> dict:
    stats = db.get_graph_stats(user_id)
    return {
        **stats,
        "unlinked_pct": round(stats["unlinked_rate"] * 100, 1),
        "assessment": "available" if stats["memories"] else "empty",
    }


def _parse_iso_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _recent_receipts(user_id: str, *, limit: int = 20) -> list[dict]:
    try:
        return list_receipts(user_control_dir(user_id), limit=limit)
    except Exception:
        return []


def _cycle_rollup(user_id: str) -> dict[str, float | int]:
    empty = {
        "total_runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
        "incomplete_runs": 0,
        "total_cost_usd": 0.0,
    }
    try:
        counts = receipt_rollup(user_control_dir(user_id))
        synthesis_sessions = [
            entry for entry in _load_session_entries() if entry.get("kind") == "synthesis"
        ]
    except Exception:
        return empty
    return {
        "total_runs": counts["total"],
        "completed_runs": counts["completed"],
        "failed_runs": counts["failed"],
        "incomplete_runs": counts["incomplete"],
        "total_cost_usd": round(
            sum(float(entry.get("cost_usd") or 0) for entry in synthesis_sessions),
            4,
        ),
    }


def _receipt_memex_moved(receipt: dict) -> bool:
    """Read current version evidence while tolerating old MEMEX update flags."""
    if receipt.get("status") != "completed":
        return False
    return isinstance(receipt.get("memex_version"), dict) or bool(receipt.get("memex_updated"))


def _receipt_recovered(receipt: dict) -> bool:
    recovery = receipt.get("recovery")
    if isinstance(recovery, dict):
        return recovery.get("restored") is True
    # Legacy compatibility is limited to the operational rollback verdict.
    state_change = receipt.get("state_change")
    return isinstance(state_change, dict) and state_change.get("graph_outcome") == "restored"


def synthesis_health(db, user_id: str, metrics_dir: Path | None = None) -> dict:
    _ = db, metrics_dir
    cycles = _recent_receipts(user_id, limit=5)
    cycle_rollup = _cycle_rollup(user_id)
    native_by_cycle = {
        str(entry.get("operation_id") or ""): entry
        for entry in _load_session_entries()
        if entry.get("kind") == "synthesis"
    }

    if cycles:
        last_run = cycles[0]
        last_ts = last_run.get("completed_at") or last_run.get("started_at")
        recent_sessions = [native_by_cycle.get(str(cycle.get("id") or "")) for cycle in cycles]
        recent_costs = [
            float(session.get("cost_usd") or 0)
            for session in recent_sessions
            if isinstance(session, dict)
        ]
        avg_cost = round(sum(recent_costs) / len(recent_costs), 4) if recent_costs else 0
        total_cost = float(cycle_rollup["total_cost_usd"])
        memex_moved = _receipt_memex_moved(last_run)
        recovered = _receipt_recovered(last_run)
        last_session = native_by_cycle.get(str(last_run.get("id") or ""))
        duration_ms = int(last_session.get("duration_ms") or 0) if last_session else 0
        cost_usd = round(float(last_session.get("cost_usd") or 0), 4) if last_session else 0
        recent_runs = len(cycles)
        last_status = str(last_run.get("status") or "unknown")
    else:
        last_run = {}
        last_ts = None
        avg_cost = 0
        total_cost = float(cycle_rollup["total_cost_usd"])
        memex_moved = False
        recovered = False
        duration_ms = None
        cost_usd = 0
        recent_runs = 0
        last_status = "unknown"

    hours = _hours_ago(last_ts if isinstance(last_ts, str) else None)
    if hours is None:
        assessment = "never_run"
    elif last_status in {"failed", "blocked", "incomplete"} and hours < 24:
        assessment = "degraded"
    elif hours < 1:
        assessment = "active"
    elif hours < 6:
        assessment = "recent"
    elif hours < 24:
        assessment = "idle"
    else:
        assessment = "stale"

    return {
        "last_run_iso": last_ts,
        "last_run_ago": _human_ago(hours),
        "last_run_hours": hours,
        "last_status": last_status,
        "memex_moved": memex_moved,
        "recovered": recovered,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "avg_cost_usd": avg_cost,
        "total_cost_usd": round(float(total_cost), 4),
        "recent_runs": recent_runs,
        "completed_runs": int(cycle_rollup["completed_runs"]),
        "failed_runs": int(cycle_rollup["failed_runs"]),
        "incomplete_runs": int(cycle_rollup["incomplete_runs"]),
        "assessment": assessment,
    }


def evolution_trends(db, user_id: str, days: int = 7) -> dict:
    """Return operation outcomes without inventing ordinary graph history."""
    _ = db
    cutoff = datetime.now(UTC).timestamp() - (days * 86400)
    receipts = []
    for receipt in list_receipts(user_control_dir(user_id)):
        event_time = _parse_iso_timestamp(receipt.get("completed_at") or receipt.get("started_at"))
        if event_time is not None and event_time.timestamp() >= cutoff:
            receipts.append(receipt)
    statuses = [str(receipt.get("status") or "incomplete") for receipt in receipts]
    return {
        "days": days,
        "cycles": len(receipts),
        "completed": statuses.count("completed"),
        "failed": statuses.count("failed"),
        "blocked": statuses.count("blocked"),
        "incomplete": statuses.count("incomplete"),
        "recovered": sum(_receipt_recovered(receipt) for receipt in receipts),
        "memex_movements": sum(_receipt_memex_moved(receipt) for receipt in receipts),
    }


def signals(db, user_id: str) -> list[dict]:
    from syke.daemon.daemon import is_running
    from syke.daemon.ipc import daemon_ipc_status
    from syke.metrics import runtime_metrics_status

    result = []

    memex = db.get_memex(user_id)
    if memex:
        memex_hours = _hours_ago(memex.get("updated_at") or memex.get("created_at"))
        if memex_hours and memex_hours > 24:
            result.append(
                {
                    "type": "stale_memex",
                    "detail": f"memex last updated {_human_ago(memex_hours)}",
                }
            )

    visibility = runtime_metrics_status(user_id)
    file_logging = visibility["file_logging"]
    if not bool(file_logging["ok"]):
        result.append(
            {
                "type": "file_logging_disabled",
                "detail": str(file_logging["detail"]),
            }
        )
    session_history = visibility["session_history"]
    if not bool(session_history["ok"]):
        result.append(
            {
                "type": "session_history_unavailable",
                "detail": str(session_history["detail"]),
            }
        )

    daemon_running, _ = is_running()
    daemon_ipc = daemon_ipc_status(user_id)
    if daemon_running and not bool(daemon_ipc["ok"]):
        result.append(
            {
                "type": "daemon_ipc_unavailable",
                "detail": f"daemon IPC unavailable: {daemon_ipc['detail']}",
            }
        )

    return result


def memex_health(db, user_id: str) -> dict:
    memex = db.get_memex(user_id)
    if not memex:
        return {
            "exists": False,
            "lines": 0,
            "updated_ago": "never",
            "assessment": "missing",
        }

    content = memex.get("content", "")
    lines = len(content.strip().split("\n")) if content else 0
    hours = _hours_ago(memex.get("updated_at") or memex.get("created_at"))
    memory_count = db.get_graph_stats(user_id)["memories"]

    return {
        "exists": True,
        "lines": lines,
        "chars": len(content),
        "updated_ago": _human_ago(hours),
        "updated_hours": hours,
        "memories": memory_count,
        "assessment": _assess_staleness(hours),
    }


def full_observe(db, user_id: str, days: int = 7) -> dict:
    return {
        "user_id": user_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "memory": memory_health(db, user_id),
        "synthesis": synthesis_health(db, user_id),
        "runtime": runtime_health(db, user_id),
        "memex": memex_health(db, user_id),
        "evolution": evolution_trends(db, user_id, days=days),
        "signals": signals(db, user_id),
    }


def _load_session_entries() -> list[dict]:
    from syke.runtime import workspace as workspace_module
    from syke.runtime.pi_sessions import list_sessions

    try:
        return list_sessions(workspace_module.SESSIONS_DIR, limit=1000)
    except Exception:
        return []


def runtime_health(db, user_id: str, metrics_dir: Path | None = None) -> dict:
    from syke.daemon.daemon import is_running
    from syke.daemon.ipc import daemon_ipc_status
    from syke.metrics import runtime_metrics_status

    _ = metrics_dir
    runtime_entries = _load_session_entries()
    ask_entries = [entry for entry in runtime_entries if entry.get("kind") == "ask"]
    synthesis_entries = [entry for entry in runtime_entries if entry.get("kind") == "synthesis"]
    cycle_rollup = _cycle_rollup(user_id)
    cycle_runs = _recent_receipts(user_id, limit=20)
    cycle_failed_runs = int(cycle_rollup["failed_runs"]) + int(cycle_rollup["incomplete_runs"])

    total_tool_calls = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    failures = 0
    tool_name_counts: dict[str, int] = {}

    for entry in runtime_entries:
        total_tool_calls += len(entry.get("tool_calls") or [])
        cache_read_tokens += int(entry.get("cache_read_tokens", 0) or 0)
        cache_write_tokens += int(entry.get("cache_write_tokens", 0) or 0)
        if entry.get("status") == "failed":
            failures += 1

        for tool_call in entry.get("tool_calls") or []:
            if isinstance(tool_call, dict):
                name = tool_call.get("name") or tool_call.get("tool") or "tool"
                if isinstance(name, str):
                    tool_name_counts[name] = tool_name_counts.get(name, 0) + 1

    def _avg_duration_ms(rows: list[dict]) -> int | None:
        durations = [int(row.get("duration_ms") or 0) for row in rows if row.get("duration_ms")]
        if not durations:
            return None
        return int(sum(durations) / len(durations))

    last_entry = runtime_entries[0] if runtime_entries else None
    metric_ts = None
    if isinstance(last_entry, dict):
        metric_ts = last_entry.get("completed_at") or last_entry.get("started_at")
    cycle_last = cycle_runs[0] if cycle_runs else None
    cycle_ts = (
        cycle_last.get("completed_at") or cycle_last.get("started_at") if cycle_last else None
    )
    metric_dt = _parse_iso_timestamp(metric_ts)
    cycle_dt = _parse_iso_timestamp(cycle_ts)
    use_cycle_as_last = bool(cycle_dt and (metric_dt is None or cycle_dt > metric_dt))
    last_ts = cycle_ts if use_cycle_as_last else metric_ts
    hours = _hours_ago(last_ts if isinstance(last_ts, str) else None)

    visibility = runtime_metrics_status(user_id)
    daemon_running, _ = is_running()
    daemon_ipc = daemon_ipc_status(user_id)
    top_tools = sorted(tool_name_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    synthesis_runs = int(cycle_rollup["total_runs"]) or len(synthesis_entries)

    if not runtime_entries and synthesis_runs == 0:
        assessment = "no_telemetry"
    elif failures > 0 or cycle_failed_runs > 0:
        assessment = "degraded"
    elif not runtime_entries:
        assessment = "cycle_only"
    else:
        assessment = "observed"

    return {
        "recent_runs": len(runtime_entries),
        "ask_runs": len(ask_entries),
        "synthesis_runs": synthesis_runs,
        "cycle_completed_runs": int(cycle_rollup["completed_runs"]),
        "cycle_failed_runs": int(cycle_rollup["failed_runs"]),
        "cycle_incomplete_runs": int(cycle_rollup["incomplete_runs"]),
        "cycle_total_cost_usd": float(cycle_rollup["total_cost_usd"]),
        "last_synthesis_status": cycle_last.get("status") if cycle_last else None,
        "last_run_ago": _human_ago(hours),
        "last_run_hours": hours,
        "last_operation": (
            "synthesis_cycle"
            if use_cycle_as_last
            else last_entry.get("kind")
            if isinstance(last_entry, dict)
            else None
        ),
        "last_provider": last_entry.get("provider") if isinstance(last_entry, dict) else None,
        "last_model": last_entry.get("model") if isinstance(last_entry, dict) else None,
        "last_response_id": last_entry.get("response_id") if isinstance(last_entry, dict) else None,
        "avg_ask_ms": _avg_duration_ms(ask_entries),
        "avg_synthesis_ms": _avg_duration_ms(synthesis_entries),
        "total_tool_calls": total_tool_calls,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "failures": failures + cycle_failed_runs,
        "top_tools": top_tools,
        "file_logging_enabled": bool(visibility["file_logging"]["ok"]),
        "file_logging_error": visibility["file_logging"]["detail"],
        "session_history_available": bool(visibility["session_history"]["ok"]),
        "session_history_detail": visibility["session_history"]["detail"],
        "daemon_running": daemon_running,
        "daemon_ipc_available": bool(daemon_ipc["ok"]),
        "daemon_ipc_detail": daemon_ipc["detail"],
        "assessment": assessment,
    }


def format_observe(data: dict) -> str:
    lines: list[str] = []
    mem = data["memory"]
    syn = data["synthesis"]
    rt = data["runtime"]
    mx = data["memex"]
    evo = data["evolution"]
    sigs = data["signals"]

    lines.append(f"Syke \u2014 {data['user_id']}")
    lines.append("")

    lines.append("## Memory")
    lines.append(
        f"{mem['memories']} current memories and {mem['links']} current links "
        f"({mem['links_per_memory']} links/memory)."
    )
    if mem["hubs"]:
        hub_strs = [f'"{h["preview"]}" ({h["links"]})' for h in mem["hubs"][:3]]
        lines.append(f"Densest hubs: {', '.join(hub_strs)}.")
    if mem["unlinked"] > 0:
        lines.append(
            f"{mem['unlinked']} current memories have no current links "
            f"({mem['unlinked_pct']}%); this is graph shape, not a health verdict."
        )
    lines.append("")

    lines.append("## Synthesis")
    if syn["assessment"] == "never_run":
        lines.append("Synthesis has never run.")
    else:
        parts = [f"Last run {syn['last_run_ago']}", f"status {syn['last_status']}"]
        if syn["duration_ms"]:
            parts.append(f"{syn['duration_ms'] / 1000:.0f}s")
        if syn["cost_usd"]:
            parts.append(f"${syn['cost_usd']:.2f}")
        if syn["memex_moved"]:
            parts.append("memex moved")
        if syn["recovered"]:
            parts.append("recovery restored")
        lines.append(". ".join(parts) + ".")
    if syn["total_cost_usd"] > 0:
        lines.append(f"Lifetime cost: ${syn['total_cost_usd']:.2f}.")
    lines.append("")

    lines.append("## Runtime")
    if rt["recent_runs"] == 0:
        lines.append("No Pi runtime telemetry yet.")
    else:
        parts = [f"Last run {rt['last_run_ago']}"]
        if rt["last_operation"]:
            parts.append(str(rt["last_operation"]))
        if rt["last_provider"] and rt["last_model"]:
            parts.append(f"{rt['last_provider']} / {rt['last_model']}")
        if rt["avg_ask_ms"]:
            parts.append(f"ask avg {rt['avg_ask_ms'] / 1000:.1f}s")
        if rt["avg_synthesis_ms"]:
            parts.append(f"synthesis avg {rt['avg_synthesis_ms'] / 1000:.1f}s")
        lines.append(". ".join(parts) + ".")
        lines.append(
            f"{rt['total_tool_calls']} tool calls. Cache read {rt['cache_read_tokens']}, "
            f"cache write {rt['cache_write_tokens']}."
        )
        if rt["top_tools"]:
            tools = ", ".join(f"{name} ({count})" for name, count in rt["top_tools"])
            lines.append(f"Top tools: {tools}.")
    lines.append("")

    lines.append("## The Map")
    if not mx["exists"]:
        lines.append("No memex yet.")
    else:
        lines.append(f"{mx['lines']} lines, {mx['chars']} chars. Last updated {mx['updated_ago']}.")
        if mx["memories"]:
            lines.append(f"{mx['memories']} current memories backing the map.")
    lines.append("")

    lines.append(f"## Operations ({evo['days']}d)")
    if evo["cycles"]:
        lines.append(
            f"{evo['cycles']} cycles: {evo['completed']} completed, {evo['failed']} failed, "
            f"{evo['blocked']} blocked, {evo['incomplete']} incomplete; "
            f"MEMEX moved {evo['memex_movements']} times."
        )
    else:
        lines.append("No cycle receipts in this window.")
    lines.append("")

    if sigs:
        lines.append("## Signals")
        for s in sigs:
            lines.append(f"  {s['detail']}")
        lines.append("")

    return "\n".join(lines)
