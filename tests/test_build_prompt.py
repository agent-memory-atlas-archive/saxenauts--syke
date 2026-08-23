"""System contracts for Syke's operative prompt assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from syke.db import SykeDB
from syke.memory.memex import update_memex
from syke.memory.memex_budget import warm_memex_tokenizer
from syke.runtime.prompt_context import build_prompt

NOW = "2026-04-15 14:00 PDT (UTC-7)"


def test_prompt_assembles_state_and_operation_without_writes(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    row_id = update_memex(db, user_id, "## Active threads\n- Verify self-continuation")
    warm_memex_tokenizer()
    graph_changes = db.conn.total_changes
    files_before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    result = build_prompt(
        tmp_path,
        db=db,
        user_id=user_id,
        now=NOW,
        context="ask",
        operation_id="cycle-ask",
        condition="after recovery",
        incoming_records='record record-1\npayload: "new evidence"',
        answer_obligation="What changed?",
    )

    sections = ["# Self-observation", "# MEMEX", "# Operation"]
    assert all(result.count(section) == 1 for section in sections)
    assert [result.index(section) for section in sections] == sorted(
        result.index(section) for section in sections
    )
    for value in (
        user_id,
        str(Path(db.db_path).resolve()),
        str(tmp_path.resolve()),
        row_id,
        "Verify self-continuation",
        "cycle-ask",
        "after recovery",
        "new evidence",
        "What changed?",
        NOW,
    ):
        assert value in result
    assert db.conn.total_changes == graph_changes
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == files_before


def test_prompt_routes_each_trigger_and_optional_guidance(tmp_path: Path) -> None:
    guidance = tmp_path / "condition.md"
    guidance.write_text("condition-specific guidance", encoding="utf-8")

    cases = (
        ("ask", "direct ask", "direct answer to waiting caller"),
        ("synthesis", "scheduled daemon wake", "background maintenance result"),
        ("replay", "replay", "replay result"),
    )
    for context, trigger, route in cases:
        result = build_prompt(
            tmp_path,
            now=NOW,
            context=context,
            synthesis_path=guidance if context == "synthesis" else None,
            first_run_guidance=(
                "Inspect the bounded source inventory." if context == "synthesis" else ""
            ),
        )
        assert f"Trigger: {trigger}" in result
        assert f"Output route: {route}" in result

    assert "condition-specific guidance" in build_prompt(
        tmp_path,
        now=NOW,
        context="synthesis",
        synthesis_path=guidance,
    )


def test_prompt_dependency_failures_degrade_without_disclosing_exceptions(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    with patch(
        "syke.runtime.self_view.build_self_view",
        side_effect=RuntimeError("private history failure"),
    ):
        self_view_failure = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    with patch(
        "syke.memory.memex.get_memex_for_injection",
        side_effect=RuntimeError("private database failure"),
    ):
        memex_failure = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    assert "unknown rather than healthy" in self_view_failure
    assert "private history failure" not in self_view_failure
    assert "No current MEMEX is available" in memex_failure
    assert "private database failure" not in memex_failure
    assert "# Operation" in self_view_failure
    assert "# Operation" in memex_failure


def test_reference_time_rule_can_be_disabled_without_removing_the_reference(
    tmp_path: Path,
) -> None:
    with_directive = build_prompt(tmp_path, now=NOW)
    without_directive = build_prompt(tmp_path, now=NOW, time_directive=False)
    directive = "Resolve relative time against this reference time. Do not use the host clock"

    assert NOW in with_directive
    assert NOW in without_directive
    assert directive in with_directive
    assert directive not in without_directive
