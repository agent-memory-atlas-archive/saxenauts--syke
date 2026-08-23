"""Protected host receipts and admitted external records."""

from __future__ import annotations

import errno
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uuid_extensions import uuid7

FINAL_RECEIPT_STATUSES = {"completed", "failed", "blocked", "incomplete"}


def receipts_dir(control_dir: str | Path) -> Path:
    return Path(control_dir).expanduser().resolve() / "receipts"


def records_dir(control_dir: str | Path) -> Path:
    return Path(control_dir).expanduser().resolve() / "records"


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    """Persist a directory entry when the platform supports directory fsync."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EINVAL, errno.EISDIR, errno.ENOTSUP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def _write_json_once(path: Path, value: dict[str, Any]) -> Path:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != value:
            raise RuntimeError(f"Protected control fact conflicts with existing file: {path}")
        return path

    _private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return path


def _safe_id(value: object, *, label: str) -> str:
    identifier = str(value or "").strip()
    if not identifier or Path(identifier).name != identifier:
        raise ValueError(f"Invalid {label} id: {value!r}")
    return identifier


def receipt_path(control_dir: str | Path, cycle_id: str) -> Path:
    return receipts_dir(control_dir) / f"{_safe_id(cycle_id, label='cycle')}.json"


def write_receipt(control_dir: str | Path, receipt: dict[str, Any]) -> Path:
    """Atomically write the host's final verdict for one synthesis."""
    cycle_id = _safe_id(receipt.get("id"), label="cycle")
    status = receipt.get("status")
    if status not in FINAL_RECEIPT_STATUSES:
        raise ValueError(f"Receipt status must be final, got {status!r}")
    for field in ("started_at", "completed_at"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(f"Receipt requires {field}")
    acknowledged = receipt.get("acknowledged_record_ids", [])
    if not isinstance(acknowledged, list) or any(
        not isinstance(record_id, str) or not record_id for record_id in acknowledged
    ):
        raise ValueError("Receipt acknowledged_record_ids must be a list of record IDs")
    return _write_json_once(receipt_path(control_dir, cycle_id), receipt)


def get_receipt(control_dir: str | Path, cycle_id: str) -> dict[str, Any] | None:
    path = receipt_path(control_dir, cycle_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _utc_time(raw: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _time_key(receipt: dict[str, Any]) -> tuple[datetime, str]:
    raw = receipt.get("completed_at") or receipt.get("started_at")
    parsed = _utc_time(raw) or datetime.min.replace(tzinfo=UTC)
    return parsed, str(receipt.get("id") or "")


def list_receipts(
    control_dir: str | Path,
    *,
    limit: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Read final receipts newest first."""
    root = receipts_dir(control_dir)
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or (status is not None and value.get("status") != status):
            continue
        found.append(value)
    found.sort(key=_time_key, reverse=True)
    return found[:limit] if limit is not None else found


def receipt_rollup(control_dir: str | Path) -> dict[str, int]:
    counts = {"total": 0, "completed": 0, "failed": 0, "blocked": 0, "incomplete": 0}
    for receipt in list_receipts(control_dir):
        status = str(receipt.get("status") or "")
        counts["total"] += 1
        if status in counts:
            counts[status] += 1
    return counts


def _new_record_id() -> str:
    return str(uuid7())


def _write_record(
    control_dir: str | Path,
    *,
    record_id: str,
    received_at: str,
    payload: str,
) -> str:
    identifier = _safe_id(record_id, label="record")
    _write_json_once(
        records_dir(control_dir) / f"{identifier}.json",
        {"id": identifier, "received_at": received_at, "payload": payload},
    )
    return identifier


def admit_record(
    control_dir: str | Path,
    payload: str,
    *,
    received_at_override: str | None = None,
) -> str:
    """Preserve one exact external record inside the protected inbox."""
    return _write_record(
        control_dir,
        record_id=_new_record_id(),
        received_at=received_at_override or datetime.now(UTC).isoformat(),
        payload=payload,
    )


def list_records(
    control_dir: str | Path,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    root = records_dir(control_dir)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    rows.sort(key=lambda row: (str(row.get("received_at") or ""), str(row.get("id") or "")))
    return rows[:limit] if limit is not None else rows


def acknowledged_record_ids(receipt: dict[str, Any]) -> list[str]:
    """Read record IDs acknowledged by a completed receipt."""
    values = receipt.get("acknowledged_record_ids")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def pending_records(
    control_dir: str | Path,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return admitted records not acknowledged by an accepted synthesis."""
    acknowledged: set[str] = set()
    for receipt in list_receipts(control_dir, status="completed"):
        acknowledged.update(acknowledged_record_ids(receipt))
    pending = [
        record
        for record in list_records(control_dir)
        if str(record.get("id") or "") not in acknowledged
    ]
    return pending[:limit] if limit is not None else pending
