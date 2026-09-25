from __future__ import annotations

from datetime import UTC, datetime

from syke.config import user_control_dir
from syke.control import write_receipt
from syke.health import evolution_trends, signals, synthesis_health


def test_evolution_trends_reports_operations_without_graph_change_history(db, user_id) -> None:
    now = datetime.now(UTC).isoformat()
    write_receipt(
        user_control_dir(user_id),
        {
            "id": "completed",
            "started_at": now,
            "completed_at": now,
            "status": "completed",
            "session_id": None,
            "acknowledged_record_ids": [],
            "memex_updated": True,
            "memex_version": {"path": "memex/completed.md", "sha256": "0" * 64},
        },
    )
    write_receipt(
        user_control_dir(user_id),
        {
            "id": "failed",
            "started_at": now,
            "completed_at": now,
            "status": "failed",
            "session_id": None,
            "acknowledged_record_ids": [],
            "memex_updated": True,
        },
    )

    trends = evolution_trends(db, user_id)

    assert trends["cycles"] == 2
    assert trends["completed"] == 1
    assert trends["failed"] == 1
    assert trends["memex_movements"] == 1


def test_synthesis_health_requires_a_host_receipt(db, user_id) -> None:
    health = synthesis_health(db, user_id)

    assert health["last_run_iso"] is None
    assert health["last_status"] == "unknown"
    assert health["recent_runs"] == 0
    assert health["total_cost_usd"] == 0
    assert health["assessment"] == "never_run"


def test_signals_include_runtime_visibility_warnings(db, user_id, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.metrics.runtime_metrics_status",
        lambda _user_id: {
            "file_logging": {
                "ok": False,
                "detail": "File logging disabled: Operation not permitted",
            },
            "session_history": {
                "ok": False,
                "detail": "Native Pi session history is not available",
            },
        },
    )
    monkeypatch.setattr("syke.daemon.daemon.is_running", lambda: (True, 1234))
    monkeypatch.setattr(
        "syke.daemon.ipc.daemon_ipc_status",
        lambda _user_id: {
            "socket_path": "/tmp/daemon.sock",
            "socket_present": False,
            "ok": False,
            "detail": "socket not found",
        },
    )

    result = signals(db, user_id)
    signal_types = {item["type"] for item in result}

    assert "file_logging_disabled" in signal_types
    assert "session_history_unavailable" in signal_types
    assert "daemon_ipc_unavailable" in signal_types
