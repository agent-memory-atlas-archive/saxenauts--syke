from __future__ import annotations

import concurrent.futures
import json
import threading
from pathlib import Path

import pytest

import syke.source_selection as selection


def test_selection_distinguishes_missing_state_from_an_explicit_choice(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "source_selection.json"
    monkeypatch.setattr(selection, "_selection_path", lambda _user_id: path)

    assert selection.get_selected_sources("test") is None
    assert selection.set_selected_sources("test", ["codex", "claude-code", "codex"]) == (
        "codex",
        "claude-code",
    )
    assert selection.get_selected_sources("test") == ("codex", "claude-code")
    assert selection.set_selected_sources("test", []) == ()
    assert selection.get_selected_sources("test") == ()


def test_selection_rejects_unknown_sources() -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        selection.set_selected_sources("test", ["fake-source"])


def test_invalid_persisted_selection_fails_closed(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source_selection.json"
    monkeypatch.setattr(selection, "_selection_path", lambda _user_id: path)
    invalid_payloads = (
        "{not-json",
        json.dumps({"schema_version": 1, "selected_sources": "codex"}),
        json.dumps({"schema_version": 1, "selected_sources": ["fake-source"]}),
        json.dumps({"schema_version": 2, "selected_sources": ["codex"]}),
    )

    for payload in invalid_payloads:
        path.write_text(payload, encoding="utf-8")
        assert selection.get_selected_sources("test") == ()


def test_concurrent_selection_writers_leave_valid_state(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source_selection.json"
    monkeypatch.setattr(selection, "_selection_path", lambda _user_id: path)
    barrier = threading.Barrier(32)

    def write(index: int) -> tuple[str, ...]:
        barrier.wait()
        source = "codex" if index % 2 == 0 else "claude-code"
        return selection.set_selected_sources("test", [source])

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(write, range(32)))

    assert len(results) == 32
    assert selection.get_selected_sources("test") in {("codex",), ("claude-code",)}
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
