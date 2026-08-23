from __future__ import annotations

import subprocess
from pathlib import Path

from syke.runtime import child_env


def test_child_environment_is_bounded_and_explicitly_extensible(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(child_env.sys, "platform", "linux")
    runtime_tmp = tmp_path / "runtime-tmp"
    runtime_tmp.mkdir()
    host = {
        "HOME": "/tmp/home",
        "PATH": "/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(runtime_tmp),
        "OPENAI_API_KEY": "host-openai",
        "ANTHROPIC_API_KEY": "opted-in-anthropic",
        "UNSAFE_SECRET": "must-not-leak",
        "CLAUDECODE": "1",
        "PI_CODING_AGENT_DIR": "/tmp/pi-agent",
        "SYKE_PI_PASSTHROUGH_ENV": "ANTHROPIC_API_KEY",
    }

    env = child_env.build_child_process_env(
        {
            "OPENAI_API_KEY": "runtime-openai",
            "AZURE_OPENAI_API_KEY": "runtime-azure",
        },
        provider="openai",
        host_env=host,
    )

    assert env["OPENAI_API_KEY"] == "runtime-openai"
    assert env["ANTHROPIC_API_KEY"] == "opted-in-anthropic"
    assert env["AZURE_OPENAI_API_KEY"] == "runtime-azure"
    assert env["PI_CODING_AGENT_DIR"] == "/tmp/pi-agent"
    assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(runtime_tmp)
    assert "UNSAFE_SECRET" not in env
    assert "CLAUDECODE" not in env


def test_child_environment_uses_the_darwin_user_temp_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    inherited_tmp = tmp_path / "inherited"
    runtime_tmp = tmp_path / "runtime"
    darwin_tmp = tmp_path / "darwin"
    for path in (inherited_tmp, runtime_tmp, darwin_tmp):
        path.mkdir()
    monkeypatch.setattr(child_env.sys, "platform", "darwin")
    monkeypatch.setattr(
        child_env.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, f"{darwin_tmp}\n", ""),
    )

    env = child_env.build_child_process_env(
        {"TMPDIR": str(runtime_tmp)},
        provider="openai-codex",
        host_env={"TMPDIR": str(inherited_tmp)},
    )

    assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(darwin_tmp)
