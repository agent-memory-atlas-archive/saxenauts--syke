from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import syke.llm.pi_install as pi_install
from syke.llm.pi_client import PiRuntime

pytestmark = pytest.mark.live


@pytest.fixture
def pi_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[PiRuntime]:
    if os.environ.get("SYKE_RUN_PI_INTEGRATION") != "1":
        pytest.fail("Pi integration requires SYKE_RUN_PI_INTEGRATION=1")
    live_pi_agent_dir = os.environ.get("SYKE_LIVE_PI_AGENT_DIR")
    if not live_pi_agent_dir:
        pytest.fail(
            "Set SYKE_LIVE_PI_AGENT_DIR to a configured Pi agent dir, "
            "for example: "
            "SYKE_LIVE_PI_AGENT_DIR=$HOME/.syke/pi-agent"
        )

    pi_agent_dir = Path(live_pi_agent_dir).expanduser().resolve()
    if not (pi_agent_dir / "settings.json").exists():
        pytest.fail(f"Configured Pi settings missing at {pi_agent_dir / 'settings.json'}")

    live_home = pi_agent_dir.parent.parent
    workspace_dir = (
        Path(
            os.environ.get(
                "SYKE_LIVE_WORKSPACE_ROOT", str(pi_agent_dir.parent / "pi-integration-smoke")
            )
        )
        .expanduser()
        .resolve()
    )
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent_dir))
    monkeypatch.setenv("HOME", str(live_home))
    monkeypatch.delenv("SYKE_DISABLE_SANDBOX", raising=False)
    pi_prefix = pi_agent_dir.parent / "pi"
    package_root = pi_prefix / "node_modules" / "@earendil-works" / "pi-coding-agent"
    monkeypatch.setattr(pi_install, "PI_LOCAL_PREFIX", pi_prefix)
    monkeypatch.setattr(pi_install, "PI_BIN", pi_agent_dir.parent / "bin" / "pi")
    monkeypatch.setattr(pi_install, "PI_NODE_BIN", pi_agent_dir.parent / "bin" / "node")
    monkeypatch.setattr(pi_install, "PI_PACKAGE_ROOT", package_root)
    monkeypatch.setattr(pi_install, "PI_CLI_JS", package_root / "dist" / "cli.js")
    monkeypatch.setattr(pi_install, "PI_TOOL_EXTENSION", pi_prefix / "syke-tools.mjs")

    runtime = PiRuntime(
        workspace_dir=workspace_dir,
        session_dir=workspace_dir.parent / "control" / "sessions",
    )

    try:
        runtime.start()
    except FileNotFoundError as exc:
        pytest.fail(f"Pi binary unavailable: {exc}")

    try:
        yield runtime
    finally:
        runtime.stop()
        shutil.rmtree(workspace_dir, ignore_errors=True)


def test_live_runtime_prompts_and_reuses_one_process(pi_runtime: PiRuntime) -> None:
    first_status = pi_runtime.status()
    first_pid = first_status["pid"]
    assert first_status["alive"] is True
    assert isinstance(first_status["workspace"], str)
    assert first_status["model"]
    assert first_pid is not None

    for expected in ("first", "second"):
        result = pi_runtime.prompt(f"Reply with exactly: {expected}", timeout=30)
        assert result.ok, f"Pi prompt failed: status={result.status} error={result.error!r}"
        assert result.output.strip()

    final_status = pi_runtime.status()
    assert final_status["alive"] is True
    assert final_status["pid"] == first_pid
    assert final_status["uptime_s"] is not None
