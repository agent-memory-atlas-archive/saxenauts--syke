"""Executable tests for the sandboxed Pi bash tool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from syke.llm.pi_client import PI_PACKAGE, PI_PACKAGE_VERSION
from syke.runtime.sandbox import sandbox_available, write_sandbox_profile

pytestmark = pytest.mark.platform

_BASH_CASE_HARNESS = r"""
import { existsSync } from "node:fs";
import registerSykeTools from "./pi_tools.mjs";

const tools = [];
registerSykeTools({ registerTool: (tool) => tools.push(tool) });
const bash = tools.find((tool) => tool.name === "bash");
if (!bash) throw new Error("Syke bash tool was not registered");

const mode = process.env.SYKE_BASH_TEST_MODE;
if (mode !== "timeout" && mode !== "abort") {
  throw new Error(`Unknown bash test mode: ${mode}`);
}

const marker = `${mode}-child-marker`;
const outsideMarker = `../${mode}-outside-marker`;
const command = `(
  printf blocked > ${outsideMarker} || true
  sleep 1.5
  printf leaked > ${marker}
) & wait`;
const controller = new AbortController();
if (mode === "abort") setTimeout(() => controller.abort(), 100);

const started = Date.now();
let message = "";
let ok = true;
try {
  await bash.execute(
    "bash_cleanup_test",
    { command, timeout: mode === "timeout" ? 0.1 : undefined },
    controller.signal,
  );
} catch (error) {
  ok = false;
  message = String(error?.message || error);
}

const elapsedMs = Date.now() - started;
await new Promise((resolve) => setTimeout(resolve, Math.max(0, 1800 - elapsedMs)));
process.stdout.write(JSON.stringify({
  ok,
  message,
  elapsedMs,
  toolNames: tools.map((tool) => tool.name).sort(),
  markerExists: existsSync(marker),
  outsideMarkerExists: existsSync(outsideMarker),
}));
"""

_HEREDOC_CASE_HARNESS = r"""
import registerSykeTools from "./pi_tools.mjs";

const tools = [];
registerSykeTools({ registerTool: (tool) => tools.push(tool) });
const bash = tools.find((tool) => tool.name === "bash");
if (!bash) throw new Error("Syke bash tool was not registered");

const command = `cat <<'EOF'
hello from heredoc
EOF
printf 'shell:%s\\n' "$BASH_VERSION"`;
const result = await bash.execute("heredoc_test", { command }, undefined);
const text = typeof result === "string" ? result : JSON.stringify(result);
process.stdout.write(JSON.stringify({ output: text }));
"""


def _real_pi_node_modules() -> Path:
    env_value = os.environ.get("SYKE_TEST_PI_NODE_MODULES")
    if not env_value:
        pytest.fail("SYKE_TEST_PI_NODE_MODULES is not set")

    node_modules = Path(env_value)
    package = node_modules.joinpath(*PI_PACKAGE.split("/"))
    if not package.is_dir():
        pytest.fail(f"prepared Pi runtime is missing: {package}")

    package_json = package / "package.json"
    if not package_json.is_file():
        pytest.fail(f"prepared Pi package metadata is missing: {package_json}")

    package_data = json.loads(package_json.read_text(encoding="utf-8"))
    if package_data.get("name") != PI_PACKAGE:
        pytest.fail(
            "prepared Pi runtime package name did not match "
            f"{PI_PACKAGE}: {package_data.get('name')!r}"
        )
    if package_data.get("version") != PI_PACKAGE_VERSION:
        pytest.fail(
            "prepared Pi runtime package version did not match "
            f"{PI_PACKAGE_VERSION}: {package_data.get('version')!r}"
        )
    return node_modules


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [("timeout", "timed out"), ("abort", "aborted")],
)
def test_sandboxed_pi_bash_stops_descendants(
    tmp_path: Path, mode: str, expected_error: str
) -> None:
    if not sandbox_available():
        pytest.fail("macOS sandbox-exec is unavailable")
    node_modules = _real_pi_node_modules()
    node = node_modules.parent.parent / "bin" / "node"
    if not node.is_file() or not os.access(node, os.X_OK):
        pytest.fail(f"prepared Node.js runtime is unavailable: {node}")

    extension_root = tmp_path / "extension"
    extension_root.mkdir()
    (extension_root / "node_modules").symlink_to(node_modules, target_is_directory=True)
    shutil.copy2(
        Path(__file__).resolve().parents[1] / "syke" / "runtime" / "pi_tools.mjs",
        extension_root / "pi_tools.mjs",
    )
    harness = extension_root / "run-bash-case.mjs"
    harness.write_text(_BASH_CASE_HARNESS, encoding="utf-8")

    workspace = tmp_path / "workspace"
    control = tmp_path / "control"
    runtime = control / "runtime"
    workspace.mkdir()
    runtime.mkdir(parents=True)
    profile = write_sandbox_profile(workspace, control_root=control, runtime_root=runtime)
    assert profile is not None

    env = {
        **os.environ,
        "SYKE_TOOL_SANDBOX_PROFILE": str(profile),
        "SYKE_BASH_TEST_MODE": mode,
    }
    try:
        completed = subprocess.run(
            [node, str(harness)],
            cwd=workspace,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        profile.unlink(missing_ok=True)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is False
    assert result["toolNames"] == ["bash", "edit", "read", "write"]
    assert expected_error in result["message"].lower()
    assert result["elapsedMs"] < 1000
    assert result["markerExists"] is False
    assert result["outsideMarkerExists"] is False
    assert not (workspace / f"{mode}-child-marker").exists()
    assert not (tmp_path / f"{mode}-outside-marker").exists()


def test_sandboxed_pi_bash_heredoc_uses_tmpdir(tmp_path: Path) -> None:
    """Heredocs must work under the sandbox, and the sandbox shell must be
    bash (not the operator's login shell) so heredoc scratch lands in the
    writable TMPDIR instead of zsh's /tmp/zsh default."""
    if not sandbox_available():
        pytest.fail("macOS sandbox-exec is unavailable")
    node_modules = _real_pi_node_modules()
    node = node_modules.parent.parent / "bin" / "node"
    if not node.is_file() or not os.access(node, os.X_OK):
        pytest.fail(f"prepared Node.js runtime is unavailable: {node}")

    extension_root = tmp_path / "extension"
    extension_root.mkdir()
    (extension_root / "node_modules").symlink_to(node_modules, target_is_directory=True)
    shutil.copy2(
        Path(__file__).resolve().parents[1] / "syke" / "runtime" / "pi_tools.mjs",
        extension_root / "pi_tools.mjs",
    )
    harness = extension_root / "run-heredoc-case.mjs"
    harness.write_text(_HEREDOC_CASE_HARNESS, encoding="utf-8")

    workspace = tmp_path / "workspace"
    control = tmp_path / "control"
    runtime = control / "runtime"
    workspace.mkdir()
    runtime.mkdir(parents=True)
    profile = write_sandbox_profile(workspace, control_root=control, runtime_root=runtime)
    assert profile is not None

    env = {
        **os.environ,
        "SHELL": "/bin/zsh",  # login-shell inheritance must not win
        "SYKE_TOOL_SANDBOX_PROFILE": str(profile),
    }
    try:
        completed = subprocess.run(
            [node, str(harness)],
            cwd=workspace,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        profile.unlink(missing_ok=True)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert "hello from heredoc" in result["output"]
    assert "shell:" in result["output"]
    assert "can't create temp file" not in result["output"]
