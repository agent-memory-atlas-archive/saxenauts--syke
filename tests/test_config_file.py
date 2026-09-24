"""Tests for the supported config.toml contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

from syke.config_file import generate_default_config, load_config


def test_missing_config_uses_current_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.toml")

    assert cfg.timezone == "auto"
    assert cfg.synthesis.thinking_level == "medium"
    assert cfg.synthesis.timeout == 600
    assert cfg.synthesis.first_run_timeout == 1500
    assert cfg.daemon.interval == 900
    assert cfg.ask.timeout == 600
    assert cfg.ask.max_parallel == 8
    assert "~/.agents/skills" in cfg.paths.distribution.skills_dirs
    assert "~/.pi/agent/skills" in cfg.paths.distribution.skills_dirs
    assert "~/.gemini/antigravity-cli/skills" in cfg.paths.distribution.skills_dirs
    assert "~/.gemini/skills" not in cfg.paths.distribution.skills_dirs


def test_supported_config_values_load(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
user = "alice"
timezone = "America/New_York"

[synthesis]
thinking_level = "high"
timeout = 300
first_run_timeout = 900

[daemon]
interval = 120

[ask]
timeout = 45
max_parallel = 3

[paths.distribution]
skills_dirs = ["~/one", "~/two"]
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.user == "alice"
    assert cfg.timezone == "America/New_York"
    assert cfg.synthesis.thinking_level == "high"
    assert cfg.synthesis.timeout == 300
    assert cfg.synthesis.first_run_timeout == 900
    assert cfg.daemon.interval == 120
    assert cfg.ask.timeout == 45
    assert cfg.ask.max_parallel == 3
    assert cfg.paths.distribution.skills_dirs == ("~/one", "~/two")


def test_invalid_config_values_fall_back_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    invalid_configs = (
        "not valid toml = [",
        '[daemon]\ninterval = "oops"\n',
        "[daemon]\ninterval = 0\n",
        "[ask]\nmax_parallel = -1\n",
        '[synthesis]\nthinking_level = "wild"\n',
        "[synthesis]\ntimeout = true\n",
    )

    for content in invalid_configs:
        path.write_text(content, encoding="utf-8")
        cfg = load_config(path)
        assert cfg.daemon.interval == 900
        assert cfg.ask.max_parallel == 8
        assert cfg.synthesis.thinking_level == "medium"


def test_max_thinking_level_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[synthesis]\nthinking_level = "max"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.synthesis.thinking_level == "max"


def test_removed_and_unknown_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
unknown_top = "ignored"

[synthesis]
threshold = 99

[paths]
data_dir = "/obsolete"

[paths.sources]
claude_code = "/obsolete"
codex = "/obsolete"

[paths.distribution]
claude_md = "/obsolete"
skills_dirs = ["~/current"]

[providers]
default = "removed"
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert not hasattr(cfg.synthesis, "threshold")
    assert not hasattr(cfg.paths, "data_dir")
    assert not hasattr(cfg.paths, "sources")
    assert not hasattr(cfg.paths.distribution, "claude_md")
    assert cfg.paths.distribution.skills_dirs == ("~/current",)


def test_generated_config_round_trips_arbitrary_user_text() -> None:
    user = 'alice"\nadmin = true'
    content = generate_default_config(user=user)
    parsed = tomllib.loads(content)

    assert parsed["user"] == user
    assert parsed["daemon"]["interval"] == 900
    assert parsed["paths"]["distribution"]["skills_dirs"]
    assert "threshold" not in content
    assert "data_dir" not in content
    assert "claude_md" not in content
    assert "~/.gemini/antigravity-cli/skills" in content
    assert "~/.gemini/skills" not in content
