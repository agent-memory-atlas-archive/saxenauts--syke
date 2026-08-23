"""Tests for Syke's Pi-native provider resolution and workspace config."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from syke.llm.env import evaluate_provider_readiness, resolve_provider
from syke.llm.pi_client import PiProviderCatalogEntry
from syke.runtime.pi_settings import configure_pi_workspace


def _catalog(*entries: PiProviderCatalogEntry) -> tuple[PiProviderCatalogEntry, ...]:
    return entries


class TestResolveProvider:
    def test_cli_flag_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "zai")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry("openrouter", ("gpt-5",), ("gpt-5",), "gpt-5", False),
                PiProviderCatalogEntry("zai", ("glm-5",), ("glm-5",), "glm-5", False),
            ),
        )
        spec = resolve_provider(cli_provider="openrouter")
        assert spec.id == "openrouter"

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openrouter")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry("openrouter", ("gpt-5",), ("gpt-5",), "gpt-5", False)
            ),
        )
        spec = resolve_provider()
        assert spec.id == "openrouter"


class TestProviderReadiness:
    def test_azure_requires_endpoint_before_being_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "azure-openai-responses",
                    ("gpt-5.4-mini",),
                    ("gpt-5.4-mini",),
                    "gpt-5.2",
                    False,
                    requires_base_url=True,
                )
            ),
        )

        status = evaluate_provider_readiness("azure-openai-responses")

        assert not status.ready
        assert "Configure a base URL/resource endpoint" in status.detail

    def test_azure_accepts_daemon_environment_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.example.com")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "azure-openai-responses",
                    ("gpt-5.4-mini",),
                    ("gpt-5.4-mini",),
                    "gpt-5.4-mini",
                    False,
                    requires_base_url=True,
                )
            ),
        )
        monkeypatch.setattr(
            "syke.llm.env.get_credential",
            lambda provider_id: {"type": "api_key"},
        )

        assert evaluate_provider_readiness("azure-openai-responses").ready

    def test_default_model_mismatch_only_marks_active_provider_unready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "kimi-coding",
                    ("k2p5", "kimi-k2-thinking"),
                    ("k2p5", "kimi-k2-thinking"),
                    "kimi-k2-thinking",
                    False,
                )
            ),
        )
        monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: "kimi-coding")
        monkeypatch.setattr("syke.llm.env.get_default_model", lambda: "sonnet")
        status = evaluate_provider_readiness("kimi-coding")

        assert not status.ready
        assert "Configured default model 'sonnet'" in status.detail


class TestPiWorkspaceSettings:
    def test_pi_runtime_env_uses_control_runtime_without_writing_workspace_settings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
        workspace = tmp_path / "workspace"
        sessions = tmp_path / "control" / "sessions"
        workspace.mkdir()

        env = configure_pi_workspace(workspace, session_dir=sessions)
        runtime_tmp = tmp_path / "control" / "runtime" / "tmp"

        assert env["PI_CODING_AGENT_DIR"] == str((tmp_path / "pi-agent").resolve())
        assert env["SYKE_PI_TMPDIR"] == str(runtime_tmp)
        assert env["TMPDIR"] == str(runtime_tmp)
        assert env["TMP"] == str(runtime_tmp)
        assert env["TEMP"] == str(runtime_tmp)
        assert runtime_tmp.is_dir()
        assert list(workspace.iterdir()) == []


class TestConfigImportBehavior:
    def test_config_import_does_not_mutate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-preserved")
        _ = importlib.reload(importlib.import_module("syke.config"))
        assert (
            importlib.import_module("os").environ.get("ANTHROPIC_API_KEY")
            == "sk-ant-test-preserved"
        )

    def test_config_import_does_not_load_project_dotenv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SYKE_PROJECT_DOTENV_SENTINEL", raising=False)
        (tmp_path / ".env").write_text(
            "SYKE_PROJECT_DOTENV_SENTINEL=from-project-dotenv\n",
            encoding="utf-8",
        )

        _ = importlib.reload(importlib.import_module("syke.config"))

        assert importlib.import_module("os").environ.get("SYKE_PROJECT_DOTENV_SENTINEL") is None
