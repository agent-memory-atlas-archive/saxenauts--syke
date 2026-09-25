from __future__ import annotations

from syke.llm.pi_client import PiLaunchBinding
from syke.runtime import _normalize_runtime_key


def test_runtime_key_changes_only_when_the_sandbox_boundary_changes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "syke.llm.pi_client.resolve_pi_launch_binding",
        lambda _model: PiLaunchBinding(provider="test", model="model"),
    )
    monkeypatch.setattr("syke.runtime.sandbox.sandbox_available", lambda: True)
    monkeypatch.delenv("SYKE_DISABLE_SANDBOX", raising=False)

    workspace = tmp_path / "workspace"
    sessions = tmp_path / "sessions"
    home_key = _normalize_runtime_key(workspace, sessions, None)
    assert home_key == _normalize_runtime_key(workspace, sessions, None)

    frozen_slice = tmp_path / "frozen-slice"
    monkeypatch.setattr(
        "syke.runtime.sandbox.sandbox_read_paths", lambda: (str(frozen_slice.resolve()),)
    )
    frozen_key = _normalize_runtime_key(workspace, sessions, None)
    assert frozen_key != home_key

    monkeypatch.setenv("SYKE_DISABLE_SANDBOX", "1")
    disabled_key = _normalize_runtime_key(workspace, sessions, None)
    assert disabled_key != frozen_key
