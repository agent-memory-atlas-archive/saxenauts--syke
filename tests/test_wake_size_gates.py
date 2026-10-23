from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import syke.runtime as runtime_module
from syke.db import SykeDB
from syke.llm import pi_client
from syke.llm.backends import pi_synthesis
from syke.memory.learned import operating_notes_tokens
from syke.memory.memex import update_memex
from syke.memory.memex_budget import format_memex_projection
from syke.runtime import self_view
from syke.runtime.self_view import graph_file_paths, measured_bytes

pytestmark = pytest.mark.usefixtures("isolated_synthesis_paths")

GIB = 1024**3
LIMITS = {
    "operating_notes_tokens": 6_000,
    "workspace_bytes": 3 * GIB,
    "runtime_bytes": 3 * GIB,
}


def _sizes(key: str, value: int | None) -> dict[str, int | None]:
    sizes: dict[str, int | None] = {name: 0 for name in LIMITS}
    sizes[key] = value
    return sizes


def _issues(key: str, start: int | None, end: int | None) -> list[str]:
    return pi_synthesis._budget_issues(_sizes(key, start), _sizes(key, end))


@pytest.mark.parametrize("key", list(LIMITS))
def test_each_size_follows_the_memory_rule(key: str) -> None:
    limit = LIMITS[key]
    assert _issues(key, limit - 1, limit) == []
    assert len(_issues(key, limit, limit + 1)) == 1
    assert len(_issues(key, limit + 10, limit + 11)) == 1
    assert _issues(key, limit + 10, limit + 10) == []
    assert _issues(key, limit + 10, limit + 5) == []
    assert _issues(key, None, limit + 1) == []
    assert _issues(key, limit + 10, None) == []


def test_size_messages_name_the_numbers() -> None:
    assert _issues("operating_notes_tokens", 100, 6_500) == [
        "OPERATING.md is 6,500 tokens; the limit is 6,000. Bring it to 6,000 or less."
    ]
    assert _issues("operating_notes_tokens", 7_000, 7_200) == [
        "OPERATING.md grew from 7,000 to 7,200 tokens. It is over 6,000, so it may shrink "
        "or stay, not grow. Bring it to 7,000 or less."
    ]
    assert _issues("workspace_bytes", 0, 4 * GIB) == [
        "The workspace (not counting syke.db) is 4.0 GiB (4,294,967,296 bytes); the limit "
        "is 3.0 GiB. Bring it to 3.0 GiB or less."
    ]
    assert _issues("runtime_bytes", 4 * GIB, 4 * GIB + 1) == [
        "The runtime folder grew from 4.0 GiB (4,294,967,296 bytes) to 4.0 GiB "
        "(4,294,967,297 bytes). It is over 3.0 GiB, so it may shrink or stay, not grow. "
        "Bring it to 4.0 GiB (4,294,967,296 bytes) or less."
    ]


def test_measurements_skip_the_graph_and_give_up_when_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    graph = workspace / "syke.db"
    for path in (graph, workspace / "syke.db-wal", workspace / "syke.db-shm"):
        path.write_bytes(b"x" * 100)
    (workspace / "notes.txt").write_bytes(b"x" * 7)
    assert measured_bytes(workspace, graph_file_paths(str(graph))) == 7
    assert measured_bytes(tmp_path / "missing") == 0

    (workspace / "tools").mkdir()
    for index in range(3):
        (workspace / "tools" / f"t{index}").write_bytes(b"x")
    monkeypatch.setattr(self_view, "GATE_SCAN_ENTRY_LIMIT", 6)
    assert measured_bytes(workspace) is None

    assert operating_notes_tokens(workspace) == 0
    (workspace / "OPERATING.md").write_bytes(b"\xff\xfe not text")
    assert operating_notes_tokens(workspace) is None


def test_validation_reports_size_issues_against_the_start(tmp_path: Path) -> None:
    notes = tmp_path / "OPERATING.md"
    notes.write_text("lesson\n", encoding="utf-8")

    def measure() -> dict[str, int | None]:
        return pi_synthesis._measure_budgets(tmp_path, tmp_path / "runtime", "syke.db")

    start = measure()
    notes.write_text(" x" * 6_500, encoding="utf-8")
    validation = pi_synthesis._validate_cycle_output(measure, start)

    assert validation["valid"] is False
    issues = validation["issues"]
    assert isinstance(issues, list)
    assert "OPERATING.md is 6,500 tokens; the limit is 6,000." in issues[0]
    stats = validation["stats"]
    assert isinstance(stats, dict)
    assert stats["budgets"]["end"]["operating_notes_tokens"] == 6_500


def test_wake_over_a_limit_is_sent_back_and_accepted_once_fixed(
    user_id: str, tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "syke.db"
    db = SykeDB(db_path)
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")
    monkeypatch.setattr(pi_synthesis, "SYKE_DB", db_path)
    update_memex(db, user_id, "canonical memex")
    notes = tmp_path / "OPERATING.md"
    prompts: list[str] = []

    def _prompt(prompt: str, **_kwargs) -> SimpleNamespace:
        prompts.append(prompt)
        notes.write_text(" x" * (6_500 if len(prompts) == 1 else 10), encoding="utf-8")
        return SimpleNamespace(
            ok=True, output="done", duration_ms=5, cost_usd=0.0, input_tokens=1,
            output_tokens=1, cache_read_tokens=0, cache_write_tokens=0, provider="p",
            response_model="m", response_id="r", stop_reason="stop", tool_calls=[],
            events=[], num_turns=1, thinking=[], session_id="native-session",
            session_file="/protected/native-session.jsonl", session_name=None,
        )  # fmt: skip

    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(provider="p", model="m"),
    )
    runtime = SimpleNamespace(prompt=_prompt)
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, workspace_root=tmp_path)

        assert result["status"] == "completed"
        acceptance = result["acceptance"]
        assert isinstance(acceptance, dict)
        assert acceptance["repair_prompts"] == 1
        assert len(prompts) == 2
        assert "Files are not restored by the host; fix sizes in place." in prompts[1]
        assert (
            "OPERATING.md is 6,500 tokens; the limit is 6,000. Bring it to 6,000 or less."
            in prompts[1]
        )
    finally:
        db.close()


def test_workspace_measurements_exclude_nested_graph_and_sidecars(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    graph = workspace / "graph" / "syke.db"
    graph.parent.mkdir(parents=True)
    for path in graph_file_paths(str(graph)):
        Path(path).write_bytes(b"x" * 100)
    (workspace / "notes.txt").write_bytes(b"x" * 7)

    assert measured_bytes(workspace, graph_file_paths(str(graph))) == 7


def test_wake_rechecks_workspace_budget_after_projecting_memex(
    user_id: str, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "bounded-workspace"
    workspace.mkdir()
    db_path = workspace / "syke.db"
    memex_path = workspace / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "SYKE_DB", db_path)
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(pi_synthesis, "WORKSPACE_LIMIT_BYTES", 200)
    db = SykeDB(db_path)
    update_memex(db, user_id, "old")
    memex_path.write_text(format_memex_projection("old") + "\n", encoding="utf-8")
    (workspace / "padding").write_bytes(b"x" * (200 - memex_path.stat().st_size))
    prompts: list[str] = []

    def prompt(text: str, **_kwargs) -> SimpleNamespace:
        prompts.append(text)
        update_memex(db, user_id, "new " * 30 if len(prompts) == 1 else "old")
        return SimpleNamespace(
            ok=True,
            output="done",
            session_id="temporary-review-session",
            session_file="/protected/temporary-review-session.jsonl",
        )

    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(provider="p", model="m"),
    )
    monkeypatch.setattr(
        runtime_module, "start_pi_runtime", lambda **kwargs: SimpleNamespace(prompt=prompt)
    )

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            workspace_root=workspace,
            skill_override="temporary fixture",
            first_run=False,
        )

        assert result["status"] == "completed"
        assert len(prompts) == 2
        acceptance = result["acceptance"]
        assert isinstance(acceptance, dict)
        assert acceptance["repair_prompts"] == 1
        assert "workspace" in prompts[1]
        workspace_bytes = measured_bytes(workspace, graph_file_paths(str(db_path)))
        assert workspace_bytes is not None
        assert workspace_bytes <= 200
        validation = result["validation"]
        assert isinstance(validation, dict)
        assert validation["stats"]["budgets"]["end"]["workspace_bytes"] <= 200
    finally:
        db.close()
