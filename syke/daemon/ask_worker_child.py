"""Child process entrypoint for daemon-owned temporary ask workers."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from syke.llm.backends import AskEvent


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _event_to_payload(event: AskEvent) -> dict[str, object]:
    return {
        "type": "event",
        "event": {
            "type": event.type,
            "content": event.content,
            "metadata": event.metadata,
        },
    }


def run_child(request: dict[str, Any]) -> int:
    user_id = request.get("user_id")
    syke_db_path = request.get("syke_db_path")
    question = request.get("question")
    transport_details = request.get("transport_details")

    if not isinstance(user_id, str) or not user_id:
        raise ValueError("worker request missing user_id")
    if not isinstance(syke_db_path, str) or not syke_db_path:
        raise ValueError("worker request missing syke_db_path")
    if not isinstance(question, str) or not question:
        raise ValueError("worker request missing question")
    if not isinstance(transport_details, dict):
        transport_details = {}

    details = dict(transport_details)
    details["worker_pid"] = os.getpid()

    from syke.cli_support.context import get_db
    from syke.config import user_syke_db_path
    from syke.llm.backends.pi_ask import pi_ask

    expected_db = Path(user_syke_db_path(user_id)).expanduser().resolve(strict=False)
    requested_db = Path(syke_db_path).expanduser().resolve(strict=False)
    if requested_db != expected_db:
        raise ValueError(f"worker rejected syke_db_path outside user scope: {requested_db}")

    with get_db(user_id) as db:
        answer, metadata = pi_ask(
            db,
            user_id,
            question,
            on_event=lambda event: _emit(_event_to_payload(event)),
            transport="daemon_worker",
            transport_details=details,
        )

    _emit({"type": "result", "answer": answer, "metadata": metadata})
    return 0


def main() -> int:
    logging.basicConfig(stream=sys.stderr)
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ValueError("worker request must be a JSON object")
        return run_child(request)
    except Exception as exc:
        _emit({"type": "error", "error": str(exc), "worker_pid": os.getpid()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
