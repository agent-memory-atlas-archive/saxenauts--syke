"""Regression tests for the minimal private self-learning loop."""

from __future__ import annotations

from pathlib import Path

from syke.db import SykeDB
from syke.db_safety import capture_baseline, validate_state_after_cycle
from syke.memory.learned import (
    LEARNED_PROJECTION_TOKEN_LIMIT,
    measure_learned_projection,
    update_learned_memory,
)
from syke.memory.memex import update_memex
from syke.runtime import pi_settings
from syke.runtime.prompt_context import build_prompt

NOW = "2026-08-14 21:00 PDT (UTC-7)"


def test_pi_runtime_installs_private_self_learn_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pi_agent_dir = tmp_path / "pi-agent"
    workspace = tmp_path / "workspace"
    sessions = tmp_path / "control" / "sessions"
    workspace.mkdir()
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent_dir))

    env = pi_settings.configure_pi_workspace(workspace, session_dir=sessions)

    installed = pi_agent_dir / "skills" / "self-learn" / "SKILL.md"
    packaged = Path(pi_settings.__file__).parent / "skills" / "self-learn" / "SKILL.md"
    installed_content = installed.read_text(encoding="utf-8")
    assert env["PI_CODING_AGENT_DIR"] == str(pi_agent_dir.resolve())
    assert installed_content == packaged.read_text(encoding="utf-8")


def test_fresh_operations_project_the_exact_learned_memory(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    empty_ask = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="ask")
    assert "# Learned" not in empty_ask

    update_learned_memory(
        db,
        user_id,
        "Before creating a memory, consider whether a continuing subject should be revised.\n",
    )

    ask = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="ask")
    synthesis = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="synthesis")
    for prompt in (ask, synthesis):
        assert prompt.count("# Learned") == 1
        assert "consider whether a continuing subject should be revised" in prompt
        assert prompt.index("# Learned") < prompt.index("# Operation")

    update_learned_memory(db, user_id, "Preserve useful evidence routes.\n")
    next_ask = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="ask")
    assert "Preserve useful evidence routes." in next_ask
    assert "consider whether a continuing subject should be revised" not in next_ask


def test_over_budget_learned_memory_is_withheld_with_pressure(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    content = " x" * LEARNED_PROJECTION_TOKEN_LIMIT
    measurement = measure_learned_projection(content)
    assert measurement["over_budget"] is True
    update_learned_memory(db, user_id, content)

    prompt = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    assert content not in prompt
    assert "# Learned" in prompt
    assert f"{measurement['tokens']:,}" in prompt
    assert f"{LEARNED_PROJECTION_TOKEN_LIMIT:,}" in prompt


def test_learned_projection_accepts_the_exact_token_limit(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    content = "x " * 997
    measurement = measure_learned_projection(content)
    assert measurement["tokens"] == LEARNED_PROJECTION_TOKEN_LIMIT
    update_learned_memory(db, user_id, content)

    prompt = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    assert content.strip() in prompt
    assert "is inactive" not in prompt


def test_semantic_gate_rejects_an_over_budget_learned_memory(
    db: SykeDB,
    user_id: str,
) -> None:
    db.bind_identity(user_id)
    update_memex(db, user_id, "canonical memex")
    baseline = capture_baseline(db, user_id)
    update_learned_memory(db, user_id, " x" * LEARNED_PROJECTION_TOKEN_LIMIT)

    verdict = validate_state_after_cycle(db, user_id, baseline)

    assert verdict["valid"] is False
    assert verdict["stats"]["learned_over_budget"] is True
    assert verdict["stats"]["learned_token_limit"] == LEARNED_PROJECTION_TOKEN_LIMIT
    assert any("learned memory over budget" in issue for issue in verdict["issues"])
