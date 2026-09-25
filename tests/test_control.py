"""Protected host receipts and raw records."""

from __future__ import annotations

from pathlib import Path

import syke.control as control_module
from syke.control import (
    admit_record,
    get_receipt,
    list_receipts,
    list_records,
    pending_records,
    write_receipt,
)


def test_receipts_are_final_host_facts_not_another_database(tmp_path: Path) -> None:
    control = tmp_path / "control"
    receipt = {
        "id": "cycle-1",
        "started_at": "2026-08-02T10:00:00+00:00",
        "completed_at": "2026-08-02T10:00:01+00:00",
        "status": "completed",
        "session_id": "session-1",
        "acknowledged_record_ids": [],
        "memex_updated": False,
    }

    path = write_receipt(control, receipt)

    assert path.parent == control / "receipts"
    assert path.suffix == ".json"
    assert get_receipt(control, "cycle-1") == receipt
    assert list_receipts(control) == [receipt]


def test_completed_receipts_acknowledge_only_the_exact_records_they_saw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    control = tmp_path / "control"
    generated_ids = iter(["record-z", "record-a"])
    monkeypatch.setattr(control_module, "_new_record_id", lambda: next(generated_ids))
    first = admit_record(
        control,
        "plain external record",
        received_at_override="2026-08-02T10:00:00+00:00",
    )
    second_payload = '{"kind":"correction","nested":{"value":2}}'
    second = admit_record(
        control,
        second_payload,
        received_at_override="2026-08-02T10:00:01+00:00",
    )

    assert [row["payload"] for row in list_records(control)] == [
        "plain external record",
        second_payload,
    ]

    write_receipt(
        control,
        {
            "id": "failed-cycle",
            "started_at": "2026-08-02T10:01:00+00:00",
            "completed_at": "2026-08-02T10:01:01+00:00",
            "status": "failed",
            "session_id": "failed-session",
            "acknowledged_record_ids": [first, second],
            "memex_updated": False,
            "state_change": None,
        },
    )
    assert [row["id"] for row in pending_records(control)] == [first, second]

    write_receipt(
        control,
        {
            "id": "accepted-cycle",
            "started_at": "2026-08-02T10:02:00+00:00",
            "completed_at": "2026-08-02T10:02:01+00:00",
            "status": "completed",
            "session_id": "accepted-session",
            "acknowledged_record_ids": [first],
            "memex_updated": False,
            "state_change": None,
        },
    )
    assert pending_records(control) == [
        {
            "id": second,
            "received_at": "2026-08-02T10:00:01+00:00",
            "payload": second_payload,
        }
    ]
