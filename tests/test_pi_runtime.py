from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from syke.llm import pi_client, pi_install


def _make_runtime(
    tmp_path: Path,
    monkeypatch,
    *,
    provider: str = "zai",
    model: str = "glm-5",
) -> pi_client.PiRuntime:
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider=provider,
            model=model_override or model,
        ),
    )
    return pi_client.PiRuntime(
        workspace_dir=tmp_path,
        session_dir=tmp_path.with_name(f"{tmp_path.name}-sessions"),
        model=model,
    )


def test_runtime_rejects_session_history_inside_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="zai",
            model=model_override or "glm-5",
        ),
    )

    with pytest.raises(ValueError, match="outside the controller workspace"):
        pi_client.PiRuntime(
            workspace_dir=tmp_path,
            session_dir=tmp_path / "sessions",
            model="glm-5",
        )


def test_runtime_start_builds_sandbox_profile_without_source_scope(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    runtime = pi_client.PiRuntime(
        workspace_dir=tmp_path,
        session_dir=tmp_path.with_name(f"{tmp_path.name}-sessions"),
        model="glm-5",
    )

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.pid = 4242

        def poll(self):
            return None

    child_tmp = tmp_path / "child-tmp"
    child_tmp.mkdir()
    monkeypatch.setenv("SYKE_PI_TMPDIR", str(child_tmp))

    def fake_write_sandbox_profile(
        workspace_root: Path,
        *,
        control_root=None,
        runtime_root=None,
        extra_temp_dirs=None,
    ):
        captured["workspace_root"] = workspace_root
        captured["control_root"] = control_root
        captured["runtime_root"] = runtime_root
        captured["extra_temp_dirs"] = extra_temp_dirs
        return tmp_path / "sandbox.sb"

    monkeypatch.setattr(pi_client, "resolve_pi_binary", lambda: "/tmp/pi")
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(provider="zai", model="glm-5"),
    )
    monkeypatch.setattr(
        pi_client,
        "configure_pi_workspace",
        lambda *args, **kwargs: {"PI_CODING_AGENT_DIR": "/tmp/pi-agent"},
    )
    monkeypatch.delenv("SYKE_DISABLE_SANDBOX", raising=False)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _FakeProcess()

    extension_path = tmp_path / "syke-tools.mjs"
    extension_path.write_text("export default () => {};\n", encoding="utf-8")

    monkeypatch.setattr(pi_client.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pi_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pi_client, "SYNC_THINKING_LEVEL", "medium")
    monkeypatch.setattr("syke.runtime.sandbox.sandbox_available", lambda: True)
    monkeypatch.setattr("syke.runtime.sandbox.write_sandbox_profile", fake_write_sandbox_profile)
    monkeypatch.setattr(pi_install, "_install_pi_tool_extension", lambda: extension_path)

    runtime.start()

    assert captured["workspace_root"] == tmp_path
    assert captured["control_root"] == runtime.session_dir.parent
    assert captured["runtime_root"] == runtime.session_dir.parent / "runtime"
    assert str(child_tmp) in captured["extra_temp_dirs"]
    command = captured["cmd"]
    assert isinstance(command, list)
    assert command[:3] == ["/tmp/pi", "--mode", "rpc"]
    assert command[command.index("--extension") + 1] == str(extension_path)
    assert "--no-builtin-tools" in command
    assert "--no-extensions" in command
    assert command[0] != "/usr/bin/sandbox-exec"
    assert captured["env"]["SYKE_TOOL_SANDBOX_PROFILE"] == str(tmp_path / "sandbox.sb")


def test_runtime_removes_sandbox_profiles_after_stop_and_launch_failure(
    tmp_path: Path, monkeypatch
) -> None:
    stop_profile = tmp_path / "stop.sb"
    stop_profile.write_text("(version 1)\n", encoding="utf-8")
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._sandbox_profile_path = stop_profile
    runtime._process = cast(Any, SimpleNamespace(pid=4242, poll=lambda: 0))

    runtime.stop()

    assert not stop_profile.exists()

    failed_profile = tmp_path / "failed-start.sb"
    failed_profile.write_text("(version 1)\n", encoding="utf-8")
    failed_runtime = _make_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pi_client,
        "configure_pi_workspace",
        lambda *args, **kwargs: {"PI_CODING_AGENT_DIR": "/tmp/pi-agent"},
    )
    monkeypatch.delenv("SYKE_DISABLE_SANDBOX", raising=False)
    monkeypatch.setattr("syke.runtime.sandbox.sandbox_available", lambda: True)
    monkeypatch.setattr(
        "syke.runtime.sandbox.write_sandbox_profile",
        lambda *_args, **_kwargs: failed_profile,
    )
    monkeypatch.setattr(pi_client._pi_catalog, "_build_pi_process_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        pi_client,
        "_build_rpc_launch_command",
        lambda **kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    with pytest.raises(OSError, match="launch failed"):
        failed_runtime.start()

    assert not failed_profile.exists()


def test_stop_waits_for_prompt_lock_before_clearing_runtime(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(pid=1234, poll=lambda: 0)
    stop_started = threading.Event()

    def stop_runtime() -> None:
        stop_started.set()
        runtime.stop()

    runtime._prompt_lock.acquire()
    thread = threading.Thread(target=stop_runtime)
    try:
        thread.start()
        assert stop_started.wait(timeout=1)
        thread.join(timeout=0.05)
        assert thread.is_alive()
        assert runtime._process is not None
    finally:
        runtime._prompt_lock.release()

    thread.join(timeout=1)
    assert not thread.is_alive()
    assert runtime._process is None


def test_prompt_timeout_returns_timeout_and_restarts_runtime(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(
        poll=lambda: None,
        pid=4321,
        wait=lambda timeout=5: None,
        terminate=lambda: None,
        kill=lambda: None,
    )

    class _FakeStream:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = [{"type": "text", "content": "partial"}]
            self.error = None

        def set_callback(self, callback) -> None:
            self.callback = callback

        def reset(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> bool:
            return False

        def get_output(self) -> str:
            return "partial"

        def get_thinking_chunks(self) -> list[str]:
            return ["thinking"]

        def get_usage(self) -> dict[str, int | float | None]:
            return {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }

        def get_message_metadata(self) -> dict[str, str | None]:
            return {
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "response_id": "resp_timeout",
                "stop_reason": None,
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return []

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(
        runtime,
        "get_session_stats",
        lambda timeout=10.0: (_ for _ in ()).throw(
            AssertionError("should not fetch stats after timeout")
        ),
    )
    result = runtime.prompt("What happened?", timeout=0.01)

    assert result.status == "timeout"
    assert result.error == "Pi did not complete within 0.01s"
    assert result.output == "partial"
    assert runtime._process is None
    assert runtime._stream is None


def test_send_request_deadline_uses_wall_clock_across_frozen_monotonic(
    tmp_path: Path, monkeypatch
) -> None:
    """A system-sleep-frozen monotonic clock must not extend the RPC wait.

    Regression for the sleep-stretch bug: deadlines previously derived from
    time.monotonic(), which stops advancing during macOS sleep. Here the
    monotonic clock is pinned near zero while the wall clock advances past
    the deadline; the wait must expire anyway.
    """
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(
        poll=lambda: None,
        pid=4321,
        wait=lambda timeout=5: None,
        terminate=lambda: None,
        kill=lambda: None,
    )

    class _EmptyStream:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

    runtime._stream = _EmptyStream()

    sent: list[dict] = []
    monkeypatch.setattr(runtime, "_send", lambda payload: sent.append(payload))

    # Wall clock advances normally; the monotonic clock is frozen at 1.0
    # (as if the machine slept right after sending). The deadline is wall
    # time now, so the wait must expire regardless of the frozen monotonic.
    monkeypatch.setattr(pi_client.time, "time", time.time)
    monkeypatch.setattr(pi_client.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(pi_client.time, "sleep", lambda _s: None)

    with pytest.raises(TimeoutError, match="Timed out waiting for Pi RPC response"):
        runtime._send_request({"type": "probe"}, timeout=0.05)

    assert sent == [{"type": "probe", "id": "req_1"}]
