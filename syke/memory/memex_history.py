"""Immutable, receipt-linked history for accepted MEMEX content."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.control import _safe_id, _write_json_once
from syke.memory.memex_budget import memex_body

MEMEX_HISTORY_SCHEMA_VERSION = 1
MEMEX_HISTORY_DIRECTORY = "memex-history"


def memex_content_sha256(content: str) -> str:
    """Hash the canonical headerless MEMEX body."""
    if not isinstance(content, str):
        raise TypeError("MEMEX content must be a string")
    return hashlib.sha256(memex_body(content).encode("utf-8")).hexdigest()


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("MEMEX history time must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid MEMEX history time: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _session_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("MEMEX version requires a session id")
    return value


def _version_relative_path(cycle_id: str) -> str:
    identifier = _safe_id(cycle_id, label="cycle")
    return f"{MEMEX_HISTORY_DIRECTORY}/{identifier}.json"


def _confined_path(control_dir: str | Path, relative_path: str) -> Path:
    root = Path(control_dir).expanduser().resolve()
    candidate = root / relative_path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"MEMEX history path escapes the control directory: {relative_path!r}")
    if candidate.parent.is_symlink() or candidate.is_symlink():
        raise ValueError(f"MEMEX history path cannot use symlinks: {relative_path!r}")
    return candidate


def write_memex_version(
    control_dir: str | Path,
    *,
    cycle_id: str,
    session_id: str,
    completed_at: str,
    content: str,
    previous_content: str | None,
) -> dict[str, str] | None:
    """Write one accepted full version, or suppress an adjacent content no-op."""
    identifier = _safe_id(cycle_id, label="cycle")
    session = _session_id(session_id)
    _parse_time(completed_at)
    if not isinstance(content, str):
        raise TypeError("MEMEX content must be a string")
    if previous_content is not None and not isinstance(previous_content, str):
        raise TypeError("Previous MEMEX content must be a string or None")

    body = memex_body(content)
    if previous_content is not None and body == memex_body(previous_content):
        return None

    digest = memex_content_sha256(body)
    relative_path = _version_relative_path(identifier)
    payload: dict[str, Any] = {
        "schema_version": MEMEX_HISTORY_SCHEMA_VERSION,
        "cycle_id": identifier,
        "session_id": session,
        "completed_at": completed_at,
        "content_sha256": digest,
        "content": body,
    }
    _write_json_once(_confined_path(control_dir, relative_path), payload)
    return {"path": relative_path, "sha256": digest}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_receipt_version(
    control_dir: str | Path,
    receipt: Mapping[str, Any],
) -> tuple[datetime, dict[str, Any]] | None:
    if receipt.get("status") != "completed":
        return None

    try:
        cycle_id = _safe_id(receipt.get("id"), label="cycle")
        session_id = _session_id(receipt.get("session_id"))
        completed_at = receipt.get("completed_at")
        completed_time = _parse_time(completed_at)
        expected_path = _version_relative_path(cycle_id)
    except (TypeError, ValueError):
        return None

    link = receipt.get("memex_version")
    if not isinstance(link, Mapping):
        return None
    relative_path = link.get("path")
    linked_hash = link.get("sha256")
    if (
        not isinstance(relative_path, str)
        or relative_path != expected_path
        or not isinstance(linked_hash, str)
    ):
        return None

    try:
        path = _confined_path(control_dir, relative_path)
    except (TypeError, ValueError):
        return None
    payload = _load_json(path)
    if payload is None:
        return None

    content = payload.get("content")
    if not isinstance(content, str) or content != memex_body(content):
        return None
    digest = memex_content_sha256(content)
    if (
        payload.get("schema_version") != MEMEX_HISTORY_SCHEMA_VERSION
        or payload.get("cycle_id") != cycle_id
        or payload.get("session_id") != session_id
        or payload.get("completed_at") != completed_at
        or payload.get("content_sha256") != digest
        or linked_hash != digest
    ):
        return None
    return completed_time, payload


def load_accepted_memex_versions(
    control_dir: str | Path,
    receipts: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Load only full versions linked by matching completed receipts, oldest first."""
    accepted: list[tuple[datetime, str, dict[str, Any]]] = []
    seen_cycles: set[str] = set()
    for receipt in receipts:
        loaded = _load_receipt_version(control_dir, receipt)
        if loaded is None:
            continue
        completed_time, payload = loaded
        cycle_id = str(payload["cycle_id"])
        if cycle_id in seen_cycles:
            continue
        seen_cycles.add(cycle_id)
        accepted.append((completed_time, cycle_id, payload))
    accepted.sort(key=lambda item: (item[0], item[1]))
    return [payload for _, _, payload in accepted]
