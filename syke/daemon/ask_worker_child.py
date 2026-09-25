"""Child process entrypoint for daemon-owned temporary ask workers."""

from __future__ import annotations

import json
import logging
import os
import sys
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
    """Answer one ask request the daemon validated before spawning this worker."""
    user_id = request["user_id"]
    question = request["question"]
    details = dict(request["transport_details"])
    details["worker_pid"] = os.getpid()

    from syke.cli_support.context import get_db
    from syke.llm.backends.pi_ask import pi_ask

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
