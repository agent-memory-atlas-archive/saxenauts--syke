"""Daemon health checks plus shared metrics facade."""

from __future__ import annotations

from syke.config import user_control_dir, user_data_dir
from syke.control import receipt_rollup
from syke.metrics import MetricsTracker, setup_logging
from syke.runtime import workspace as workspace_module
from syke.runtime.pi_sessions import session_history_status

__all__ = ["MetricsTracker", "run_health_check", "setup_logging"]


def run_health_check(user_id: str) -> dict:
    """Run health checks and return results."""
    checks: dict[str, dict] = {}

    import sys

    checks["python"] = {
        "ok": sys.version_info >= (3, 12),
        "detail": f"Python {sys.version.split()[0]}",
    }

    db = None
    try:
        from syke.cli_support.context import get_db

        db = get_db(user_id)
        memory_count = int(db.get_graph_stats(user_id)["memories"])
        cycle_count = receipt_rollup(user_control_dir(user_id))["total"]
        checks["database"] = {
            "ok": True,
            "detail": f"{memory_count} current memories, {cycle_count} cycles",
        }
    except Exception as e:
        checks["database"] = {"ok": False, "detail": str(e)}

    data_dir = user_data_dir(user_id)
    checks["data_dir"] = {
        "ok": data_dir.exists(),
        "detail": str(data_dir),
    }

    # Memex check — reuse open db connection
    if db is not None:
        try:
            memex = db.get_memex(user_id)
            checks["memex"] = {
                "ok": memex is not None,
                "detail": "Memex exists" if memex is not None else "No memex yet",
            }
        except Exception as e:
            checks["memex"] = {"ok": False, "detail": str(e)}
    else:
        checks["memex"] = {"ok": False, "detail": "Database unavailable"}

    checks["session_history"] = session_history_status(workspace_module.SESSIONS_DIR)

    # Synthesis freshness — reuse health.py
    if db is not None:
        try:
            from syke.health import synthesis_health

            synth = synthesis_health(db, user_id)
            synth_state = synth.get("assessment", "unknown")
            checks["synthesis"] = {
                "ok": synth_state in ("active", "recent", "never_run"),
                "detail": synth_state,
            }
        except Exception as e:
            checks["synthesis"] = {"ok": False, "detail": str(e)}

    # Signals surface concrete runtime and freshness degradation.
    if db is not None:
        try:
            from syke.health import signals

            sigs = signals(db, user_id)
            checks["signals"] = {
                "ok": len(sigs) == 0,
                "detail": [s.get("detail", s.get("type", "unknown")) for s in sigs] if sigs else [],
            }
        except Exception as e:
            checks["signals"] = {"ok": False, "detail": str(e)}

    # Runtime: is Pi alive and reachable via IPC?
    try:
        from syke.daemon.ipc import daemon_runtime_status

        rt = daemon_runtime_status(user_id, timeout=0.5)
        checks["runtime"] = {
            "ok": bool(rt.get("alive")),
            "detail": rt.get("detail", "unknown"),
        }
    except Exception as e:
        checks["runtime"] = {"ok": False, "detail": str(e)}

    if db is not None:
        db.close()

    all_critical_ok = all(checks[k]["ok"] for k in ["python", "database", "runtime"])
    return {"healthy": all_critical_ok, "checks": checks}
