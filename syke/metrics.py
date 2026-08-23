"""Metrics and logging over native Pi sessions and host receipts."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from syke.config import user_control_dir, user_data_dir
from syke.control import list_receipts, receipt_rollup
from syke.runtime import workspace as workspace_module
from syke.runtime.pi_sessions import list_sessions, session_history_status

# Structured logger
logger = logging.getLogger("syke")


def _writability_status(path: Path, *, label: str) -> dict[str, object]:
    base_dir = path.parent
    probe_dir = base_dir if base_dir.exists() else base_dir.parent
    writable = probe_dir.exists() and os.access(probe_dir, os.W_OK)
    detail = f"{label} writable at {path}" if writable else f"{label} not writable at {path}"
    return {
        "ok": writable,
        "path": str(path),
        "detail": detail,
    }


def runtime_metrics_status(user_id: str) -> dict[str, dict[str, object]]:
    data_dir = user_data_dir(user_id)
    file_logging = _writability_status(data_dir / "syke.log", label="File logging")
    if _LAST_FILE_LOGGING_ERROR is not None:
        file_logging = {
            **file_logging,
            "ok": False,
            "detail": f"File logging disabled: {_LAST_FILE_LOGGING_ERROR}",
        }

    return {
        "file_logging": file_logging,
        "session_history": session_history_status(workspace_module.SESSIONS_DIR),
    }


_LAST_FILE_LOGGING_ERROR: str | None = None


def _ensure_private_file(path: Path) -> None:
    path.touch(exist_ok=True)
    os.chmod(path, 0o600)


def setup_logging(
    user_id: str,
    verbose: bool = False,
    *,
    file_logging: bool = True,
) -> None:
    """Configure console logging and, when requested, the durable log file."""
    global _LAST_FILE_LOGGING_ERROR
    level = logging.DEBUG if verbose else logging.INFO

    # Console handler — clean output
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(console)
    logger.propagate = False

    if not file_logging:
        _LAST_FILE_LOGGING_ERROR = None
        return

    try:
        log_dir = user_data_dir(user_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "syke.log"
        _ensure_private_file(log_file)
        file_handler = logging.FileHandler(log_file)
    except OSError as exc:
        _LAST_FILE_LOGGING_ERROR = str(exc)
        logger.debug("File logging disabled: %s", exc, exc_info=True)
        return

    _LAST_FILE_LOGGING_ERROR = None

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)


class MetricsTracker:
    """Reads operational summaries from Pi's native protected sessions."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def get_summary(self) -> dict:
        """Load all metrics and produce a summary."""
        runs = self._load_all()
        cycle_summary = self._load_receipt_summary(runs)

        total_cost = sum(r.get("cost_usd", 0) for r in runs)
        total_tokens = sum(
            r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            for r in runs
        )
        by_operation: dict[str, dict] = {}
        for r in runs:
            op = r.get("operation", "unknown")
            if op not in by_operation:
                by_operation[op] = {"count": 0, "cost_usd": 0.0, "total_tokens": 0, "errors": 0}
            by_operation[op]["count"] += 1
            by_operation[op]["cost_usd"] += r.get("cost_usd", 0)
            by_operation[op]["total_tokens"] += (
                r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            )
            if not r.get("success", True):
                by_operation[op]["errors"] += 1

        return {
            "total_runs": len(runs),
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "by_operation": by_operation,
            "last_run": runs[-1] if runs else cycle_summary["last_cycle"],
            "synthesis_cycles_total": cycle_summary["total_cycles"],
            "synthesis_cycles_completed": cycle_summary["completed_cycles"],
            "synthesis_cycles_failed": cycle_summary["failed_cycles"],
            "synthesis_cycles_incomplete": cycle_summary["incomplete_cycles"],
            "synthesis_cycles_cost_usd": cycle_summary["total_cost_usd"],
            "last_cycle": cycle_summary["last_cycle"],
        }

    def _load_all(self) -> list[dict]:
        """Load native Pi session summaries in chronological order."""
        try:
            sessions = list_sessions(workspace_module.SESSIONS_DIR, limit=None)
        except Exception as exc:
            logger.debug("Failed to read native Pi sessions: %s", exc, exc_info=True)
            return []

        runs = []
        for entry in reversed(sessions):
            details = {
                "tool_calls": int(entry.get("tool_calls_count") or 0),
                "num_turns": int(entry.get("num_turns") or 0),
                "tool_name_counts": entry.get("tool_name_counts") or {},
                "status": entry.get("status"),
                "provider": entry.get("provider"),
                "model": entry.get("model"),
                "response_id": entry.get("response_id"),
                "stop_reason": entry.get("stop_reason"),
                "session_id": entry.get("id"),
                "session_path": entry.get("path"),
            }
            runs.append(
                {
                    "operation": entry.get("kind"),
                    "operation_id": entry.get("operation_id"),
                    "user_id": entry.get("user_id"),
                    "started_at": entry.get("started_at"),
                    "completed_at": entry.get("completed_at"),
                    "duration_seconds": float(entry.get("duration_ms") or 0) / 1000.0,
                    "input_tokens": int(entry.get("input_tokens") or 0),
                    "output_tokens": int(entry.get("output_tokens") or 0),
                    "thinking_tokens": 0,
                    "cost_usd": float(entry.get("cost_usd") or 0.0),
                    "success": entry.get("status") == "completed",
                    "error": entry.get("error"),
                    "num_turns": int(entry.get("num_turns") or 0),
                    "duration_api_ms": int(entry.get("duration_ms") or 0),
                    "details": details,
                }
            )
        return runs

    def _load_receipt_summary(self, runs: list[dict]) -> dict:
        summary = {
            "total_cycles": 0,
            "completed_cycles": 0,
            "failed_cycles": 0,
            "incomplete_cycles": 0,
            "total_cost_usd": 0.0,
            "last_cycle": None,
        }
        try:
            control_dir = user_control_dir(self.user_id)
            rollup = receipt_rollup(control_dir)
            synthesis_runs = [run for run in runs if run.get("operation") == "synthesis"]
            summary.update(
                {
                    "total_cycles": rollup["total"],
                    "completed_cycles": rollup["completed"],
                    "failed_cycles": rollup["failed"],
                    "incomplete_cycles": rollup["incomplete"],
                    "total_cost_usd": round(
                        sum(float(run.get("cost_usd") or 0) for run in synthesis_runs),
                        4,
                    ),
                }
            )
            receipts = list_receipts(control_dir, limit=1)
            if receipts:
                last = receipts[0]
                native = next(
                    (
                        run
                        for run in reversed(synthesis_runs)
                        if run.get("operation_id") == last.get("id")
                    ),
                    None,
                )
                summary["last_cycle"] = {
                    "operation": "synthesis_cycle",
                    "status": last.get("status"),
                    "completed_at": last.get("completed_at") or last.get("started_at"),
                    "cost_usd": round(float(native.get("cost_usd") or 0), 4) if native else 0.0,
                    "success": last.get("status") == "completed",
                }
        except Exception:
            return summary
        return summary
