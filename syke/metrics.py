"""Metrics and logging over native Pi sessions and host receipts."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from syke.config import user_data_dir
from syke.runtime import workspace as workspace_module
from syke.runtime.pi_sessions import session_history_status

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
