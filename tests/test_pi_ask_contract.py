from __future__ import annotations

from pathlib import Path

from syke.llm.backends import AskEvent
from syke.llm.backends import pi_ask as pi_ask_module
from syke.llm.pi_client import PiCycleResult
from syke.runtime import workspace as workspace_module


class _FakeRuntime:
    is_alive = True

    def __init__(self, result: PiCycleResult, workspace_root: Path):
        self._result = result
        self._workspace_root = workspace_root
        self.prompt_args: tuple[object, ...] | None = None
        self.prompt_kwargs: dict[str, object] | None = None

    def status(self) -> dict[str, object]:
        return {"workspace": str(self._workspace_root), "pid": 1234}

    def prompt(self, *args: object, **kwargs: object) -> PiCycleResult:
        self.prompt_args = args
        self.prompt_kwargs = kwargs
        on_event = kwargs.get("on_event")
        if callable(on_event):
            on_event(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {"type": "thinking_delta", "delta": "checking"},
                }
            )
            on_event(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": "answer"},
                }
            )
        return self._result


def _cycle_result(*, status: str = "completed", error: str | None = None) -> PiCycleResult:
    return PiCycleResult(
        status=status,
        output="answer" if status == "completed" else "",
        thinking=["checking"],
        tool_calls=[{"name": "read", "input": {"path": "MEMEX.md"}}],
        events=[],
        num_turns=1,
        duration_ms=12,
        input_tokens=20,
        output_tokens=4,
        cache_read_tokens=3,
        cache_write_tokens=0,
        cost_usd=0.01,
        provider="test-provider",
        response_model="test-model",
        response_id="response-1",
        stop_reason="stop",
        session_id="session-1",
        session_file="/control/sessions/session-1.jsonl",
        session_name="syke:ask:ask-1",
        error=error,
    )


def test_translate_pi_event_uses_pinned_tool_execution_shape() -> None:
    events: list[AskEvent] = []

    emitted_text = pi_ask_module._translate_pi_event(
        {
            "type": "tool_execution_start",
            "toolCallId": "call_1",
            "toolName": "read",
            "args": {"path": "MEMEX.md"},
        },
        events.append,
    )

    assert emitted_text is False
    assert events == [
        AskEvent(type="tool_call", content="read", metadata={"input": {"path": "MEMEX.md"}})
    ]


def test_pi_ask_runs_a_standalone_attention_episode(
    db,
    user_id: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    session_dir = tmp_path / "control" / "sessions"
    workspace_root.mkdir()
    session_dir.mkdir(parents=True)
    runtime = _FakeRuntime(_cycle_result(), workspace_root)
    events: list[AskEvent] = []
    started_with: dict[str, object] = {}
    prompt_context: dict[str, object] = {}

    def fake_start_runtime(**kwargs):
        started_with.update(kwargs)
        return runtime

    def fake_build_prompt(*args, **kwargs):
        prompt_context["args"] = args
        prompt_context["kwargs"] = kwargs
        return "standalone ask prompt"

    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace_module, "SESSIONS_DIR", session_dir)
    monkeypatch.setattr("syke.runtime.start_pi_runtime", fake_start_runtime)
    monkeypatch.setattr("syke.runtime.prompt_context.build_prompt", fake_build_prompt)
    monkeypatch.setattr(pi_ask_module, "get_selected_sources", lambda _user_id: ("codex",))

    answer, metadata = pi_ask_module.pi_ask(
        db,
        user_id,
        "What should I do next?",
        model="test-model",
        timeout=90,
        on_event=events.append,
        transport="daemon_ipc",
        transport_details={"routing_reason": "warm_runtime"},
    )

    assert answer == "answer"
    assert [(event.type, event.content) for event in events] == [
        ("thinking", "checking"),
        ("text", "answer"),
    ]
    assert started_with["workspace_dir"] == workspace_root
    assert started_with["session_dir"] == session_dir
    assert started_with["model"] == "test-model"
    assert "selected_sources" not in started_with
    assert runtime.prompt_args == ("standalone ask prompt",)
    assert runtime.prompt_kwargs is not None
    assert runtime.prompt_kwargs["new_session"] is True
    assert str(runtime.prompt_kwargs["session_name"]).startswith("syke:ask:")
    assert prompt_context["args"] == (workspace_root,)
    prompt_kwargs = prompt_context["kwargs"]
    assert isinstance(prompt_kwargs, dict)
    assert prompt_kwargs["context"] == "ask"
    assert prompt_kwargs["answer_obligation"] == "What should I do next?"
    assert prompt_kwargs["selected_sources"] == ("codex",)
    assert metadata["session_name"] == "syke:ask:ask-1"
    assert metadata["transport"] == "daemon_ipc"
    assert metadata["routing_reason"] == "warm_runtime"


def test_pi_ask_returns_runtime_failure(db, user_id: str, monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    session_dir = tmp_path / "control" / "sessions"
    workspace_root.mkdir()
    session_dir.mkdir(parents=True)
    runtime = _FakeRuntime(
        _cycle_result(status="error", error="provider failed"),
        workspace_root,
    )

    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace_module, "SESSIONS_DIR", session_dir)
    monkeypatch.setattr("syke.runtime.start_pi_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr(
        "syke.runtime.prompt_context.build_prompt",
        lambda *_args, **_kwargs: "standalone ask prompt",
    )
    monkeypatch.setattr(pi_ask_module, "get_selected_sources", lambda _user_id: ())

    answer, metadata = pi_ask_module.pi_ask(db, user_id, "question")

    assert answer == "provider failed"
    assert metadata["error"] == "provider failed"


def test_pi_ask_removes_only_spills_reported_by_its_runtime(
    db,
    user_id: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    session_dir = tmp_path / "control" / "sessions"
    runtime_tmp = session_dir.parent / "runtime" / "tmp"
    workspace_root.mkdir()
    session_dir.mkdir(parents=True)
    runtime_tmp.mkdir(parents=True)
    owned = runtime_tmp / "pi-bash-0123456789abcdef.log"
    unreported = runtime_tmp / "pi-bash-fedcba9876543210.log"
    owned.write_text("owned", encoding="utf-8")
    unreported.write_text("unreported", encoding="utf-8")
    runtime = _FakeRuntime(_cycle_result(), workspace_root)
    callback_calls = 0

    def disconnected_callback(_event: AskEvent) -> None:
        nonlocal callback_calls
        callback_calls += 1
        raise BrokenPipeError("caller disconnected")

    def prompt(*args: object, **kwargs: object) -> PiCycleResult:
        del args
        on_event = kwargs.get("on_event")
        assert callable(on_event)
        on_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "checking"},
            }
        )
        on_event(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "result": {"details": {"fullOutputPath": str(owned)}},
            }
        )
        on_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "still checking"},
            }
        )
        assert owned.exists()
        return _cycle_result()

    runtime.prompt = prompt  # type: ignore[method-assign]
    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace_module, "SESSIONS_DIR", session_dir)
    monkeypatch.setattr("syke.runtime.start_pi_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr(
        "syke.runtime.prompt_context.build_prompt",
        lambda *_args, **_kwargs: "standalone ask prompt",
    )
    monkeypatch.setattr(pi_ask_module, "get_selected_sources", lambda _user_id: ())

    answer, _metadata = pi_ask_module.pi_ask(
        db,
        user_id,
        "question",
        on_event=disconnected_callback,
    )

    assert answer == "answer"
    assert callback_calls == 1
    assert not owned.exists()
    assert unreported.read_text(encoding="utf-8") == "unreported"
