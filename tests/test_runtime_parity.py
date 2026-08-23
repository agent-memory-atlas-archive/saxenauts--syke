from __future__ import annotations

import types
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch

import pytest

from syke.llm import pi_runtime

MetadataValue = str | int | float | bool | None
AskMetadata = dict[str, MetadataValue]


class RunAskFn(Protocol):
    def __call__(
        self,
        db: object,
        user_id: str,
        question: str,
        **kwargs: object,
    ) -> tuple[str, AskMetadata]: ...


RUN_ASK = cast(RunAskFn, pi_runtime.run_ask)


def _canonical_ask_metadata(**overrides: MetadataValue) -> AskMetadata:
    metadata: AskMetadata = {
        "backend": "pi",
        "cost_usd": None,
        "duration_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "tool_calls": None,
        "num_turns": None,
        "error": None,
    }
    metadata.update(overrides)
    return metadata


def test_run_ask_uses_direct_pi_for_nonpersistent_database() -> None:
    db = types.SimpleNamespace(db_path=":memory:")
    expected = ("answer from pi", _canonical_ask_metadata(tool_calls=2))

    with patch("syke.llm.backends.pi_ask.pi_ask", return_value=expected) as pi_ask:
        result = RUN_ASK(db, "user", "question", timeout=17)

    assert result == expected
    pi_ask.assert_called_once_with(db, "user", "question", timeout=17.0)


def test_run_ask_uses_daemon_ipc_for_persistent_path_before_file_exists(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "new-syke.db"
    assert not syke_db_path.exists()

    with (
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            return_value=("answer from daemon", _canonical_ask_metadata(tool_calls=1)),
        ) as daemon_mock,
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
    ):
        answer_text, metadata = RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
            timeout=240,
        )

    assert answer_text == "answer from daemon"
    assert metadata["tool_calls"] == 1
    daemon_mock.assert_called_once()
    assert daemon_mock.call_args.kwargs["syke_db_path"] == str(syke_db_path)
    assert "timeout" not in daemon_mock.call_args.kwargs
    pi_mock.assert_not_called()


def test_run_ask_requires_daemon_ipc_for_persistent_db(tmp_path: Path) -> None:
    syke_db_path = tmp_path / "syke.db"
    syke_db_path.write_text("", encoding="utf-8")

    with (
        patch(
            "syke.daemon.ipc.ask_via_daemon",
            side_effect=RuntimeError("socket missing"),
        ),
        patch("syke.llm.backends.pi_ask.pi_ask") as pi_mock,
        pytest.raises(RuntimeError, match="socket missing"),
    ):
        RUN_ASK(
            types.SimpleNamespace(
                db_path=str(syke_db_path),
            ),
            "user",
            "question",
        )

    pi_mock.assert_not_called()
