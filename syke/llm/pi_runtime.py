"""Pi-native ask and synthesis dispatch."""

from __future__ import annotations

import logging
from typing import Any

from syke.config import ASK_TIMEOUT
from syke.db import SykeDB

logger = logging.getLogger(__name__)


def _resolve_ask_timeout(timeout_raw: object) -> float | None:
    if isinstance(timeout_raw, (int, float)) and timeout_raw > 0:
        return float(timeout_raw)
    if ASK_TIMEOUT > 0:
        return float(ASK_TIMEOUT)
    return None


def run_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    **kwargs: Any,
) -> tuple[str, dict[str, object]]:
    logger.info("Routing ask to Pi runtime")

    db_path = getattr(db, "db_path", None)
    if isinstance(db_path, str) and db_path and db_path != ":memory:":
        from syke.daemon.ipc import ask_via_daemon

        # The daemon owns ask runtime policy. Caller-local timeout knobs must
        # not shorten shared Syke answers or force a direct Pi fallback.
        return ask_via_daemon(
            user_id=user_id,
            syke_db_path=db_path,
            question=question,
            on_event=kwargs.get("on_event"),
        )

    timeout = _resolve_ask_timeout(kwargs.get("timeout"))
    if timeout is not None:
        kwargs["timeout"] = timeout
    else:
        kwargs.pop("timeout", None)

    from syke.llm.backends.pi_ask import pi_ask

    return pi_ask(db, user_id, question, **kwargs)
