"""Immutable receipt-linked MEMEX history."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from syke.memory.memex_history import (
    load_accepted_memex_versions,
    memex_content_sha256,
    write_memex_version,
)


def _receipt(
    *,
    cycle_id: str,
    session_id: str,
    completed_at: str,
    link: dict[str, str],
    status: str = "completed",
) -> dict[str, object]:
    return {
        "id": cycle_id,
        "status": status,
        "session_id": session_id,
        "completed_at": completed_at,
        "memex_version": link,
    }


def test_write_memex_version_is_headerless_immutable_and_receipt_linked(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    completed_at = "2026-08-10T10:00:00+00:00"
    content = "# MEMEX [99 / 2,000 tokens]\n\n# Current\n\nFact.\n"

    link = write_memex_version(
        control,
        cycle_id="cycle-1",
        session_id="session-1",
        completed_at=completed_at,
        content=content,
        previous_content="old body",
    )

    assert link == {
        "path": "memex-history/cycle-1.json",
        "sha256": memex_content_sha256("# Current\n\nFact."),
    }
    assert link is not None
    stored = json.loads((control / link["path"]).read_text(encoding="utf-8"))
    assert stored == {
        "schema_version": 1,
        "cycle_id": "cycle-1",
        "session_id": "session-1",
        "completed_at": completed_at,
        "content_sha256": link["sha256"],
        "content": "# Current\n\nFact.",
    }
    receipt = _receipt(
        cycle_id="cycle-1",
        session_id="session-1",
        completed_at=completed_at,
        link=link,
    )
    assert load_accepted_memex_versions(control, [receipt]) == [stored]

    assert (
        write_memex_version(
            control,
            cycle_id="cycle-1",
            session_id="session-1",
            completed_at=completed_at,
            content=content,
            previous_content="old body",
        )
        == link
    )
    with pytest.raises(RuntimeError, match="conflicts with existing file"):
        write_memex_version(
            control,
            cycle_id="cycle-1",
            session_id="session-1",
            completed_at=completed_at,
            content="different accepted body",
            previous_content="old body",
        )


def test_adjacent_noop_is_suppressed_but_revert_is_a_new_version(tmp_path: Path) -> None:
    control = tmp_path / "control"

    assert (
        write_memex_version(
            control,
            cycle_id="cycle-noop",
            session_id="session-noop",
            completed_at="2026-08-10T10:00:00+00:00",
            content="# MEMEX [1 / 2,000 tokens]\n\nbody",
            previous_content="body\n",
        )
        is None
    )
    assert not (control / "memex-history" / "cycle-noop.json").exists()

    changed = write_memex_version(
        control,
        cycle_id="cycle-change",
        session_id="session-change",
        completed_at="2026-08-10T10:01:00+00:00",
        content="new body",
        previous_content="body",
    )
    reverted = write_memex_version(
        control,
        cycle_id="cycle-revert",
        session_id="session-revert",
        completed_at="2026-08-10T10:02:00+00:00",
        content="body",
        previous_content="new body",
    )

    assert changed is not None
    assert reverted is not None
    assert changed["sha256"] != reverted["sha256"]
    reverted_payload = json.loads((control / reverted["path"]).read_text(encoding="utf-8"))
    assert reverted_payload["content"] == "body"


def test_reader_requires_completed_receipt_identity_and_ignores_orphans(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    completed_at = "2026-08-10T10:00:00+00:00"
    accepted_link = write_memex_version(
        control,
        cycle_id="cycle-accepted",
        session_id="session-accepted",
        completed_at=completed_at,
        content="accepted body",
        previous_content="old body",
    )
    orphan_link = write_memex_version(
        control,
        cycle_id="cycle-orphan",
        session_id="session-orphan",
        completed_at="2026-08-10T10:01:00+00:00",
        content="orphan body",
        previous_content="accepted body",
    )
    assert accepted_link is not None
    assert orphan_link is not None

    valid = _receipt(
        cycle_id="cycle-accepted",
        session_id="session-accepted",
        completed_at=completed_at,
        link=accepted_link,
    )
    wrong_cycle = {**valid, "id": "cycle-other"}
    wrong_session = {**valid, "session_id": "session-other"}
    wrong_time = {**valid, "completed_at": "2026-08-10T10:00:01+00:00"}
    wrong_hash = {
        **valid,
        "memex_version": {**accepted_link, "sha256": "0" * 64},
    }
    escaped_path = {
        **valid,
        "memex_version": {
            **accepted_link,
            "path": "memex-history/../cycle-accepted.json",
        },
    }
    failed = _receipt(
        cycle_id="cycle-orphan",
        session_id="session-orphan",
        completed_at="2026-08-10T10:01:00+00:00",
        link=orphan_link,
        status="failed",
    )

    accepted = load_accepted_memex_versions(
        control,
        [wrong_cycle, wrong_session, wrong_time, wrong_hash, escaped_path, failed, valid],
    )
    assert [version["cycle_id"] for version in accepted] == ["cycle-accepted"]


def test_reader_orders_versions_by_receipt_completion_time(tmp_path: Path) -> None:
    control = tmp_path / "control"
    later_link = write_memex_version(
        control,
        cycle_id="cycle-later",
        session_id="session-later",
        completed_at="2026-08-10T12:00:00+00:00",
        content="later",
        previous_content="earlier",
    )
    earlier_link = write_memex_version(
        control,
        cycle_id="cycle-earlier",
        session_id="session-earlier",
        completed_at="2026-08-10T10:00:00+00:00",
        content="earlier",
        previous_content="baseline",
    )
    assert later_link is not None
    assert earlier_link is not None

    receipts = [
        _receipt(
            cycle_id="cycle-later",
            session_id="session-later",
            completed_at="2026-08-10T12:00:00+00:00",
            link=later_link,
        ),
        _receipt(
            cycle_id="cycle-earlier",
            session_id="session-earlier",
            completed_at="2026-08-10T10:00:00+00:00",
            link=earlier_link,
        ),
    ]
    assert [version["cycle_id"] for version in load_accepted_memex_versions(control, receipts)] == [
        "cycle-earlier",
        "cycle-later",
    ]
