from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from syke.config import PROJECT_ROOT, user_control_dir, user_syke_db_path
from syke.control import list_records
from syke.db import SykeDB
from syke.entrypoint import cli
from syke.onboarding import read_onboarding_state, write_onboarding_state
from syke.source_selection import get_selected_sources, set_selected_sources


def test_setup_json_is_inspect_only(tmp_path: Path) -> None:
    home = tmp_path / "clean-home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "PYTHONPATH": str(PROJECT_ROOT),
        }
    )
    for name in (
        "SYKE_CONTROL_ROOT",
        "SYKE_WORKSPACE_ROOT",
        "SYKE_PI_AGENT_DIR",
        "SYKE_PI_STATE_AUDIT_PATH",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [sys.executable, "-m", "syke", "--user", "test", "setup", "--json"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["mode"] == "inspect"
    assert parsed["user"] == "test"
    assert all(point["id"] != "daemon" for point in parsed["consent_points"])
    assert not (home / ".syke").exists()


def test_setup_noninteractive_without_provider_returns_auth_exit(cli_runner) -> None:
    payload = {
        "provider": {"configured": False},
        "sources": [],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.cli_commands.setup.render_setup_inspect_summary"),
        patch("syke.cli_commands.setup.run_setup_stage", side_effect=lambda _label, fn: fn()),
        patch("syke.cli_commands.setup.ensure_setup_pi_runtime", return_value=("pi", "1.0.0")),
        patch("syke.cli_commands.setup.sys.stdin.isatty", return_value=False),
        patch("syke.cli_commands.setup.run_interactive_provider_flow") as provider_flow,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes"])

    assert result.exit_code == 3
    assert "Setup requires a configured provider." in result.output
    provider_flow.assert_not_called()


def test_setup_runtime_failure_uses_runtime_exit_code(cli_runner) -> None:
    from syke.cli_support.exit_codes import SykeRuntimeException

    payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {"source": "codex", "roots": [], "files_found": 1, "detected": True},
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.cli_commands.setup.render_setup_inspect_summary"),
        patch("syke.cli_commands.setup.run_setup_stage", side_effect=lambda _label, fn: fn()),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            side_effect=SykeRuntimeException("Pi runtime unavailable."),
        ),
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--yes", "--source", "codex"],
        )

    assert result.exit_code == 4
    assert "Pi runtime unavailable." in result.output
    assert get_selected_sources("test") is None


def test_setup_agent_does_not_mislabel_programming_errors_as_missing_runtime(cli_runner) -> None:
    payload = {"provider": {"configured": False}, "sources": []}
    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch(
            "syke.llm.pi_client.ensure_pi_binary",
            side_effect=ValueError("broken invariant"),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--agent"])

    parsed = json.loads(result.output)
    assert result.exit_code == 1
    assert parsed["status"] == "failed"
    assert "broken invariant" in parsed["error"]


def test_setup_agent_rechecks_provider_after_installing_pi_runtime(cli_runner) -> None:
    first_payload = {
        "provider": {"configured": False},
        "sources": [],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }
    second_payload = {
        "provider": {"configured": True, "id": "openai-codex", "model": "gpt-5.4"},
        "sources": [],
        "daemon": {
            "platform": "Darwin",
            "installable": True,
            "running": False,
            "persistence": {"manager": "launchd"},
        },
    }

    with (
        patch(
            "syke.cli_commands.setup.build_setup_inspect_payload",
            side_effect=[first_payload, second_payload],
        ) as inspect_payload,
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection",
            return_value="syke loaded",
        ),
        patch(
            "syke.cli_commands.setup.run_macos_filesystem_access_check",
            return_value={"applicable": True, "ok": True, "status": "granted"},
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/syke-onboarding.log"),
        ) as launch_onboarding,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--agent"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert {
        "status": parsed["status"],
        "provider": parsed["provider"],
        "daemon": parsed["daemon"],
        "onboarding_mode": parsed["onboarding"]["mode"],
    } == {
        "status": "complete",
        "provider": {"id": "openai-codex", "model": "gpt-5.4"},
        "daemon": "started",
        "onboarding_mode": "daemon",
    }
    assert parsed["next_steps"][-1] == "syke status --json"
    assert inspect_payload.call_count == 2
    launch_onboarding.assert_called_once_with(user_id="test", selected_sources=[])
    onboarding = read_onboarding_state("test")
    assert onboarding is not None
    assert onboarding["mode"] == "daemon"


def test_setup_agent_verifies_macos_folders_before_background_start(cli_runner) -> None:
    payload = {
        "provider": {"configured": True, "id": "openai-codex", "model": "gpt-5.4"},
        "sources": [],
        "daemon": {
            "platform": "Darwin",
            "installable": True,
            "running": False,
            "persistence": {"manager": "launchd"},
        },
    }
    filesystem_access = {
        "applicable": True,
        "ok": True,
        "status": "granted",
        "detail": "Desktop, Documents, and Downloads verified for background Syke",
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection",
            return_value="syke loaded",
        ),
        patch(
            "syke.cli_commands.setup.run_macos_filesystem_access_check",
            return_value=filesystem_access,
        ) as verify_access,
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/syke-onboarding.log"),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--agent"])

    parsed = json.loads(result.output)
    assert result.exit_code == 0
    assert parsed["status"] == "complete"
    assert parsed["daemon"] == "started"
    assert parsed["filesystem_access"] == filesystem_access
    verify_access.assert_called_once_with("test")


def test_setup_agent_returns_secret_safe_auth_and_exact_retry(cli_runner) -> None:
    payload = {
        "provider": {"configured": False},
        "provider_choices": [
            {
                "id": "openai-codex",
                "label": "OpenAI (ChatGPT Plus/Pro)",
                "oauth": True,
                "ready": False,
                "default_model": "gpt-5.4",
                "models": ["gpt-5.4", "gpt-5.3-codex"],
                "detail": "No auth configured",
            }
        ],
        "sources": [
            {
                "source": "codex",
                "detected": True,
                "files_found": 5,
                "format_cluster": "jsonl",
            }
        ],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
    ):
        result = cli_runner.invoke(
            cli,
            [
                "--user",
                "test",
                "setup",
                "--agent",
                "--source",
                "codex",
            ],
        )

    parsed = json.loads(result.output)
    assert result.exit_code == 3
    assert parsed["status"] == "needs_provider"
    assert "get their API key" not in parsed["instructions"]
    assert parsed["provider_choices"] == [
        {
            "id": "openai-codex",
            "label": "OpenAI (ChatGPT Plus/Pro)",
            "oauth": True,
            "ready": False,
            "default_model": "gpt-5.4",
        }
    ]
    assert parsed["auth_options"] == {
        "oauth": "syke auth login <provider> --use",
        "api_key": "syke auth set <provider> --api-key <KEY> --use",
        "inspect": "syke auth status --json",
    }
    assert parsed["next_steps"][-1] == "syke setup --agent --source codex"


def test_setup_agent_fails_when_background_service_is_unavailable(cli_runner) -> None:
    payload = {
        "provider": {"configured": True, "id": "openai-codex", "model": "gpt-5.4"},
        "sources": [],
        "daemon": {
            "platform": "Linux",
            "installable": False,
            "running": False,
            "detail": "systemd user manager unavailable",
        },
    }
    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection",
            return_value="syke loaded",
        ),
        patch("syke.cli_commands.setup._launch_background_onboarding") as launch_onboarding,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--agent"])

    parsed = json.loads(result.output)
    assert result.exit_code == 1
    assert parsed["status"] == "failed"
    assert parsed["error"] == (
        "Background service is required for setup: systemd user manager unavailable"
    )
    launch_onboarding.assert_not_called()


def test_setup_agent_fails_without_downgrading_when_background_launch_fails(
    cli_runner,
) -> None:
    payload = {
        "provider": {"configured": True, "id": "openai-codex", "model": "gpt-5.4"},
        "sources": [],
        "daemon": {
            "platform": "Linux",
            "installable": True,
            "running": False,
            "persistence": {"manager": "systemd"},
        },
    }
    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection",
            return_value="syke loaded",
        ),
        patch("syke.daemon.daemon.LOG_PATH", Path("/tmp/onboarding.log")),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            side_effect=OSError("cannot spawn"),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--agent"])

    parsed = json.loads(result.output)
    assert result.exit_code == 1
    assert parsed["status"] == "failed"
    assert "cannot spawn" in parsed["error"]
    assert parsed["onboarding"]["mode"] == "daemon"
    assert parsed["onboarding"]["monitor"] == "/tmp/onboarding.log"
    onboarding = read_onboarding_state("test")
    assert onboarding is not None
    assert onboarding["mode"] == "daemon"


def test_setup_agent_source_flag_limits_ingestion(cli_runner) -> None:
    payload = {
        "provider": {"configured": True, "id": "openai-codex", "model": "gpt-5.4"},
        "sources": [
            {
                "source": "codex",
                "detected": True,
                "files_found": 5,
                "format_cluster": "jsonl",
            },
            {
                "source": "claude-code",
                "detected": True,
                "files_found": 50,
                "format_cluster": "jsonl",
            },
        ],
        "daemon": {
            "platform": "Linux",
            "installable": True,
            "running": False,
            "persistence": {"manager": "systemd"},
        },
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection",
            return_value="syke loaded",
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/syke-onboarding.log"),
        ) as launch_onboarding,
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--agent", "--source", "codex"],
        )

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["sources_ingesting"] == ["codex"]
    assert parsed["total_files"] == 5
    assert parsed["onboarding"]["selected_sources"] == ["codex"]
    launch_onboarding.assert_called_once_with(user_id="test", selected_sources=["codex"])


def test_setup_rejects_removed_skip_daemon_option(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["setup", "--agent", "--skip-daemon"])

    assert result.exit_code == 2
    assert "No such option: --skip-daemon" in result.output


def test_setup_agent_source_flag_rejects_undetected_source(cli_runner) -> None:
    payload = {
        "provider": {"configured": False},
        "sources": [
            {
                "source": "codex",
                "detected": True,
                "files_found": 5,
                "format_cluster": "jsonl",
            },
        ],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }

    with patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--agent", "--source", "claude-code"],
        )

    assert result.exit_code == 2
    parsed = json.loads(result.output)
    assert parsed["status"] == "failed"
    assert "not detected" in parsed["error"]


def test_status_rejects_a_second_identity_without_traceback(cli_runner) -> None:
    with SykeDB(user_syke_db_path("canonical"), user_id="canonical"):
        pass

    result = cli_runner.invoke(cli, ["--user", "other", "status", "--json"])

    assert result.exit_code == 6
    assert "Syke store is bound to 'canonical', not 'other'" in result.output
    assert "Traceback" not in result.output


def test_status_json_reports_persisted_operator_state(cli_runner) -> None:
    set_selected_sources("test", ("codex",))
    write_onboarding_state(
        "test",
        selected_sources=("codex",),
        total_files=4,
        estimated_minutes=2,
        estimate_method="test",
        mode="manual",
    )

    result = cli_runner.invoke(cli, ["--user", "test", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["selected_sources"] == ["codex"]
    assert payload["selection_mode"] == "explicit"
    assert payload["onboarding"]["selected_sources"] == ["codex"]
    assert payload["memex"]["present"] is False


def test_memex_json_returns_the_machine_payload(cli_runner) -> None:
    fake_db = MagicMock()
    with (
        patch("syke.cli_commands.status.get_db", return_value=fake_db),
        patch(
            "syke.memory.memex.get_memex_for_injection",
            return_value="# Memex\n- current focus",
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "memex", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "memex": "# Memex\n- current focus",
        "user": "test",
    }
    fake_db.close.assert_called_once()


def test_ask_json_returns_structured_result(cli_runner) -> None:
    fake_db = MagicMock()

    with (
        patch("syke.cli_commands.ask.get_db", return_value=fake_db),
        patch(
            "syke.llm.env.resolve_provider",
            return_value=SimpleNamespace(id="openai"),
        ),
        patch(
            "syke.llm.pi_runtime.run_ask",
            return_value=(
                "final answer",
                {
                    "provider": "openai",
                    "duration_ms": 123,
                    "cost_usd": 0.01,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "tool_calls": 1,
                },
            ),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "what changed?", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["question"] == "what changed?"
    assert parsed["answer"] == "final answer"
    assert parsed["provider"] == "openai"
    fake_db.close.assert_called_once()


def test_ask_jsonl_streams_status_events_and_result(cli_runner) -> None:
    fake_db = MagicMock()

    def fake_run_ask(*, db, user_id, question, on_event):
        del db, user_id, question
        on_event(SimpleNamespace(type="thinking", content="considering", metadata=None))
        on_event(
            SimpleNamespace(
                type="tool_call",
                content="search",
                metadata={"input": {"query": "recent work"}},
            )
        )
        on_event(SimpleNamespace(type="text", content="answer text", metadata=None))
        return "answer text", {
            "provider": "openai",
            "duration_ms": 50,
            "cost_usd": 0.0,
            "input_tokens": 5,
            "output_tokens": 7,
            "tool_calls": 1,
        }

    with (
        patch("syke.cli_commands.ask.get_db", return_value=fake_db),
        patch(
            "syke.llm.env.resolve_provider",
            return_value=SimpleNamespace(id="openai"),
        ),
        patch("syke.llm.pi_runtime.run_ask", side_effect=fake_run_ask),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "what changed?", "--jsonl"])

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert lines[0] == {"type": "status", "phase": "starting", "provider": "openai"}
    assert any(line["type"] == "thinking" for line in lines)
    assert any(line["type"] == "tool_call" for line in lines)
    assert any(line["type"] == "text" for line in lines)
    result_line = next(line for line in lines if line["type"] == "result")
    assert result_line["ok"] is True
    assert result_line["answer"] == "answer text"
    fake_db.close.assert_called_once()


def test_ask_machine_formats_report_backend_failure(cli_runner) -> None:
    for output_arg in ("--json", "--jsonl"):
        fake_db = MagicMock()
        with (
            patch("syke.cli_commands.ask.get_db", return_value=fake_db),
            patch(
                "syke.llm.env.resolve_provider",
                return_value=SimpleNamespace(id="openai"),
            ),
            patch(
                "syke.llm.pi_runtime.run_ask",
                return_value=(
                    "backend failed",
                    {"provider": "openai", "duration_ms": 123, "error": "runtime down"},
                ),
            ),
        ):
            result = cli_runner.invoke(
                cli,
                ["--user", "test", "ask", "what changed?", output_arg],
            )

        assert result.exit_code == 1
        payload = [json.loads(line) for line in result.output.strip().splitlines()]
        error = payload[-1]
        if output_arg == "--json":
            assert error["ok"] is False
        else:
            assert error["type"] == "error"
        assert error["error"] == "runtime down"
        fake_db.close.assert_called_once()


def test_ask_machine_errors_keep_auth_and_usage_exit_codes(cli_runner) -> None:
    cases = (
        (RuntimeError("No provider configured. Run `syke setup`."), [], 3, "No provider"),
        (
            ValueError("Unknown provider 'bad'. Valid providers: openai"),
            ["--provider", "bad"],
            2,
            "Unknown provider",
        ),
    )
    for error, provider_args, expected_exit, expected_message in cases:
        fake_db = MagicMock()
        with (
            patch("syke.cli_commands.ask.get_db", return_value=fake_db),
            patch("syke.llm.env.resolve_provider", side_effect=error),
        ):
            result = cli_runner.invoke(
                cli,
                ["--user", "test", *provider_args, "ask", "what changed?", "--json"],
            )

        assert result.exit_code == expected_exit
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert expected_message in parsed["error"]
        fake_db.close.assert_called_once()


def test_observe_json_reports_window_and_rejects_watch(cli_runner) -> None:
    result = cli_runner.invoke(
        cli,
        ["--user", "test", "observe", "--json", "--days", "30"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["evolution"]["days"] == 30

    incompatible = cli_runner.invoke(
        cli,
        ["--user", "test", "observe", "--json", "--watch"],
    )
    assert incompatible.exit_code == 2


def test_daemon_status_json_prefers_last_cycle_truth_over_last_run(cli_runner) -> None:
    with (
        patch(
            "syke.cli_support.daemon_state.daemon_process_state",
            return_value={"running": True, "pid": 321, "source": "launchd"},
        ),
        patch("syke.cli_support.daemon_state.launchd_metadata", return_value={"registered": True}),
        patch(
            "syke.control.list_receipts",
            return_value=[
                {
                    "id": "cycle-failed",
                    "status": "failed",
                    "completed_at": "2026-04-03T04:00:45+00:00",
                }
            ],
        ),
        patch("syke.runtime.pi_sessions.list_sessions") as list_sessions,
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=SimpleNamespace()),
        patch("syke.runtime.locator.describe_runtime_target", return_value="runtime-target"),
        patch(
            "syke.runtime.locator.resolve_background_syke_runtime", return_value=SimpleNamespace()
        ),
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={"reachable": False, "alive": False, "detail": "socket missing"},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["last_run"]["success"] is False
    assert parsed["last_run"]["status"] == "failed"
    list_sessions.assert_not_called()


def test_cost_days_uses_the_bounded_native_session_window(cli_runner) -> None:
    recent_run = {
        "kind": "ask",
        "started_at": "2026-04-03T04:00:00+00:00",
        "status": "completed",
        "cost_usd": 0.01,
        "input_tokens": 10,
        "output_tokens": 5,
    }
    unnamed_run = {
        "kind": "session",
        "started_at": "2026-04-03T03:00:00+00:00",
        "status": "completed",
        "cost_usd": 0.0,
        "input_tokens": 3,
        "output_tokens": 2,
    }

    with (
        patch(
            "syke.runtime.pi_sessions.list_sessions_between",
            return_value=[recent_run, unnamed_run],
        ) as list_between,
        patch("syke.runtime.pi_sessions.list_sessions") as list_sessions,
    ):
        result = cli_runner.invoke(cli, ["cost", "--days", "1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total_runs"] == 2
    assert payload["total_tokens"] == 20
    assert payload["by_operation"]["session"]["count"] == 1
    list_between.assert_called_once()
    list_sessions.assert_not_called()


def test_daemon_logs_json_returns_bounded_lines(cli_runner, tmp_path: Path) -> None:
    log_path = tmp_path / "daemon.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    with patch("syke.daemon.daemon.LOG_PATH", log_path):
        result = cli_runner.invoke(cli, ["daemon", "logs", "--json", "--lines", "2"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["requested_lines"] == 2
    assert payload["lines"] == ["two", "three"]


def test_record_json_preserves_the_complete_payload(cli_runner) -> None:
    raw = '{"kind":"correction","details":{"text":"keep all fields"}}'

    result = cli_runner.invoke(cli, ["--user", "test", "record", "--json", raw])

    assert result.exit_code == 0
    assert [record["payload"] for record in list_records(user_control_dir("test"))] == [raw]


def test_record_jsonl_accepts_each_record_in_order(cli_runner) -> None:
    raw = '{"kind":"first","value":1}\n{"kind":"second","value":2}\n'

    result = cli_runner.invoke(
        cli,
        ["--user", "test", "record", "--jsonl"],
        input=raw,
    )

    assert result.exit_code == 0
    assert "Accepted 2 records" in result.output
    assert [record["payload"] for record in list_records(user_control_dir("test"))] == [
        '{"kind":"first","value":1}',
        '{"kind":"second","value":2}',
    ]
