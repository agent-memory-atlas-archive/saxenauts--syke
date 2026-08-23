"""First-run onboarding state shared by setup and the local web UI."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke import config

ONBOARDING_STATE_FILE = "onboarding.json"


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def onboarding_state_path(user_id: str) -> Path:
    _ = user_id
    return config.SYKE_HOME / ONBOARDING_STATE_FILE


def write_onboarding_state(
    user_id: str,
    *,
    selected_sources: list[str] | tuple[str, ...],
    total_files: int,
    estimated_minutes: int,
    estimate_method: str,
    mode: str,
    monitor: str | None = None,
    persistence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "waiting_first_synthesis",
        "created_at": now,
        "updated_at": now,
        "selected_sources": list(selected_sources),
        "total_files": int(total_files),
        "estimated_minutes": int(estimated_minutes),
        "estimate_method": estimate_method,
        "mode": mode,
        "monitor": monitor,
        "persistence": persistence or {},
    }
    _write_state(onboarding_state_path(user_id), payload)
    return payload


def mark_first_synthesis_complete(user_id: str) -> dict[str, Any] | None:
    payload = read_onboarding_state(user_id)
    if not payload:
        return None
    if payload.get("status") != "waiting_first_synthesis":
        return payload

    updated = dict(payload)
    updated["status"] = "first_synthesis_completed"
    updated["updated_at"] = datetime.now(UTC).isoformat()

    _write_state(onboarding_state_path(user_id), updated)
    return updated


def read_onboarding_state(user_id: str) -> dict[str, Any] | None:
    path = onboarding_state_path(user_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload
