from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from syke import pi_state


def test_pi_state_paths_follow_override_and_current_default(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "override"
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(override))

    assert pi_state.get_pi_agent_dir() == override.resolve()
    assert pi_state.get_pi_auth_path() == override.resolve() / "auth.json"
    assert pi_state.get_pi_settings_path() == override.resolve() / "settings.json"
    assert pi_state.get_pi_models_path() == override.resolve() / "models.json"

    monkeypatch.delenv("SYKE_PI_AGENT_DIR")
    monkeypatch.setattr(pi_state.config, "SYKE_HOME", tmp_path / ".syke")
    assert pi_state.get_pi_agent_dir() == (tmp_path / ".syke" / "pi-agent").resolve()


def test_api_key_state_is_private_and_audit_redacted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(tmp_path / "pi-state-audit.log"))
    monkeypatch.setattr(
        pi_state.sys,
        "argv",
        ["syke", "auth", "set", "openrouter", "--api-key", "sk-or-test"],
    )

    pi_state.set_api_key("openrouter", "sk-or-test")

    auth_path = pi_state.get_pi_auth_path()
    assert json.loads(auth_path.read_text(encoding="utf-8")) == {
        "openrouter": {"type": "api_key", "key": "sk-or-test"}
    }
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(auth_path.parent.stat().st_mode) == 0o700

    audit_path = tmp_path / "pi-state-audit.log"
    payload = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["event"] == "set_api_key"
    assert payload["after"]["openrouter"]["key"] == "[REDACTED]"
    assert payload["argv"][-1] == "[REDACTED]"
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600


def test_provider_and_model_defaults_commit_as_one_transition(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(tmp_path / "pi-state-audit.log"))

    pi_state.set_default_provider_and_model("openai", "gpt-5.4")

    assert pi_state.get_default_provider() == "openai"
    assert pi_state.get_default_model() == "gpt-5.4"
    audit = [
        json.loads(line)
        for line in (tmp_path / "pi-state-audit.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["event"] for entry in audit] == ["set_default_provider_and_model"]
    assert audit[0]["after"] == {
        "defaultModel": "gpt-5.4",
        "defaultProvider": "openai",
    }

    with pytest.raises(ValueError, match="changed together"):
        pi_state.set_default_provider_and_model("anthropic", None)
    assert pi_state.get_default_provider() == "openai"
    assert pi_state.get_default_model() == "gpt-5.4"


def test_failed_default_publication_preserves_previous_pair(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    pi_state.set_default_provider_and_model("openai", "gpt-5.4")
    monkeypatch.setattr(pi_state.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))

    with pytest.raises(OSError):
        pi_state.set_default_provider_and_model("anthropic", "claude-sonnet-4-6")

    assert pi_state.get_default_provider() == "openai"
    assert pi_state.get_default_model() == "gpt-5.4"
    assert not list((tmp_path / "pi-agent").glob("*.tmp"))


def test_credential_removal_does_not_implicitly_change_defaults(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    pi_state.set_api_key("anthropic", "sk-ant-test")
    pi_state.set_default_provider_and_model("anthropic", "claude-sonnet-4-6")

    assert pi_state.remove_credential("anthropic") is True
    assert pi_state.get_credential("anthropic") is None
    assert pi_state.get_default_provider() == "anthropic"
    assert pi_state.get_default_model() == "claude-sonnet-4-6"


def test_provider_override_write_and_removal_are_audited(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(tmp_path / "pi-state-audit.log"))

    pi_state.upsert_provider_override(
        "custom-provider",
        base_url="https://example.com",
        api_key="super-secret",
    )
    assert pi_state.remove_provider_override("custom-provider") is True

    assert pi_state.load_pi_models() == {"providers": {}}
    audit = [
        json.loads(line)
        for line in (tmp_path / "pi-state-audit.log").read_text(encoding="utf-8").splitlines()
    ]
    assert audit[0]["after"]["providers"]["custom-provider"]["apiKey"] == "[REDACTED]"
    assert audit[-1]["event"] == "remove_provider_override"
    assert audit[-1]["after"] == {"providers": {}}


def test_pi_agent_env_uses_current_state_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "pi-agent"
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(root))
    assert pi_state.build_pi_agent_env() == {"PI_CODING_AGENT_DIR": str(root.resolve())}

    monkeypatch.delenv("SYKE_PI_AGENT_DIR")
    monkeypatch.setattr(pi_state.config, "SYKE_HOME", tmp_path / ".syke")
    assert pi_state.build_pi_agent_env() == {
        "PI_CODING_AGENT_DIR": str((tmp_path / ".syke" / "pi-agent").resolve())
    }
