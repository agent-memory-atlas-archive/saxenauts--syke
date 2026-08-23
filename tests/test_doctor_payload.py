from __future__ import annotations

from types import SimpleNamespace


def test_network_probe_reports_not_ready_without_live_probe(monkeypatch) -> None:
    from syke.cli_support import doctor

    monkeypatch.setattr(
        doctor,
        "resolve_provider",
        lambda cli_provider=None: SimpleNamespace(id="openai"),
    )
    monkeypatch.setattr(
        doctor,
        "build_pi_runtime_env",
        lambda _provider: {},
    )
    monkeypatch.setattr(
        doctor,
        "evaluate_provider_readiness",
        lambda _provider_id: SimpleNamespace(ready=False, detail="missing OPENAI_API_KEY"),
    )

    payload = doctor.network_probe_payload(SimpleNamespace(obj={"provider": None}))

    assert payload["ok"] is False
    assert payload["live_probe"] is False
    assert "missing OPENAI_API_KEY" in str(payload["detail"])
