from __future__ import annotations

import concurrent.futures
import json
import threading
from pathlib import Path

import syke.onboarding as onboarding


def _write(user_id: str, index: int) -> dict[str, object]:
    return onboarding.write_onboarding_state(
        user_id,
        selected_sources=("codex",),
        total_files=index,
        estimated_minutes=2,
        estimate_method="test",
        mode="manual",
    )


def test_first_synthesis_completion_preserves_onboarding_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding.config, "SYKE_HOME", tmp_path)
    _write("test", 7)

    completed = onboarding.mark_first_synthesis_complete("test")

    assert completed is not None
    assert completed["status"] == "first_synthesis_completed"
    assert completed["total_files"] == 7
    assert onboarding.read_onboarding_state("test") == completed


def test_concurrent_onboarding_writers_do_not_share_temp_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding.config, "SYKE_HOME", tmp_path)
    barrier = threading.Barrier(32)

    def write(index: int) -> dict[str, object]:
        barrier.wait()
        return _write("test", index)

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(write, range(32)))

    assert len(results) == 32
    path = onboarding.onboarding_state_path("test")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["total_files"] in range(32)
