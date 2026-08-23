from __future__ import annotations

from pathlib import Path

from syke.llm import pi_client


def test_pi_bash_spill_path_from_event_accepts_only_owned_spills(tmp_path: Path) -> None:
    runtime_tmp = tmp_path / "control" / "runtime" / "tmp"
    runtime_tmp.mkdir(parents=True)
    owned = runtime_tmp / "pi-bash-0123456789abcdef.log"
    owned.write_text("full output", encoding="utf-8")
    outside = tmp_path / "pi-bash-fedcba9876543210.log"
    outside.write_text("outside", encoding="utf-8")

    assert (
        pi_client.pi_bash_spill_path_from_event(
            {
                "type": "tool_execution_update",
                "toolName": "bash",
                "partialResult": {"details": {"fullOutputPath": str(owned)}},
            },
            runtime_tmp,
        )
        == owned
    )
    assert (
        pi_client.pi_bash_spill_path_from_event(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "failed output\n\n[Showing lines 1-2 of 4. "
                                f"Full output: {owned}]\n\nCommand exited with code 1"
                            ),
                        }
                    ],
                    "details": {},
                },
            },
            runtime_tmp,
        )
        is None
    )
    assert (
        pi_client.pi_bash_spill_path_from_event(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "result": {"details": {"fullOutputPath": str(owned)}},
            },
            runtime_tmp,
        )
        == owned
    )
    assert (
        pi_client.pi_bash_spill_path_from_event(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "result": {"details": {"fullOutputPath": str(outside)}},
            },
            runtime_tmp,
        )
        is None
    )
    assert (
        pi_client.pi_bash_spill_path_from_event(
            {
                "type": "tool_execution_end",
                "toolName": "read",
                "result": {"details": {"fullOutputPath": str(owned)}},
            },
            runtime_tmp,
        )
        is None
    )


def test_remove_pi_bash_spills_does_not_touch_unreported_files(tmp_path: Path) -> None:
    owned = tmp_path / "pi-bash-0123456789abcdef.log"
    unreported = tmp_path / "pi-bash-fedcba9876543210.log"
    owned.write_text("owned", encoding="utf-8")
    unreported.write_text("unreported", encoding="utf-8")

    pi_client.remove_pi_bash_spills({owned})

    assert not owned.exists()
    assert unreported.read_text(encoding="utf-8") == "unreported"
