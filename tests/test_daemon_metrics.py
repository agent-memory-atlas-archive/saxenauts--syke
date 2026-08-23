from __future__ import annotations

from pathlib import Path

from syke.daemon.metrics import run_health_check


class _FakeDB:
    def get_graph_stats(self, _user_id: str) -> dict[str, int]:
        return {"memories": 0}

    def get_memex(self, _user_id: str) -> dict[str, object]:
        return {}

    def close(self) -> None:
        return None


def test_health_requires_the_daemon_runtime_to_be_alive(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.metrics.user_data_dir", lambda _user: tmp_path)
    monkeypatch.setattr("syke.cli_support.context.get_db", lambda _user: _FakeDB())
    monkeypatch.setattr("syke.health.synthesis_health", lambda _db, _user: {"assessment": "active"})
    monkeypatch.setattr("syke.health.signals", lambda _db, _user: [])

    for alive in (False, True):
        monkeypatch.setattr(
            "syke.daemon.ipc.daemon_runtime_status",
            lambda _user, timeout=0.5, alive=alive: {
                "alive": alive,
                "detail": "runtime alive" if alive else "runtime down",
            },
        )

        health = run_health_check("test")

        assert health["checks"]["runtime"]["ok"] is alive
        assert health["healthy"] is alive
