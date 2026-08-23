from __future__ import annotations

from syke.cli_support import setup_support


def test_setup_daemon_viability_uses_systemd_on_linux(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(
        setup_support,
        "daemon_payload",
        lambda: {"running": False, "registered": False, "detail": "not running"},
    )
    monkeypatch.setattr(
        "syke.daemon.daemon.systemd_user_available",
        lambda: (True, "systemd user manager available"),
    )

    payload = setup_support.setup_daemon_viability_payload()

    assert payload["platform"] == "Linux"
    assert payload["installable"] is True
    assert payload["detail"] == "background service manager available"
    assert payload["persistence"]["manager"] == "systemd"
