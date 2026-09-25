"""Temporal grounding — timezone-aware formatting for Syke.

Store UTC. Precompute local. The LLM is a narrator, not a clock.
"""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

SYKE_TIMEZONE_ENV = "SYKE_TIMEZONE"


def _detect_system_tz() -> tzinfo:
    """Best-effort system timezone. Prefers IANA name over fixed offset."""
    with suppress(Exception):
        link = Path("/etc/localtime").resolve()
        parts = link.parts
        if "zoneinfo" in parts:
            idx = parts.index("zoneinfo")
            iana_key = "/".join(parts[idx + 1 :])
            return ZoneInfo(iana_key)
    return datetime.now().astimezone().tzinfo or UTC


def resolve_user_tz() -> tzinfo:
    """Resolve user timezone. Precedence: SYKE_TIMEZONE env > config.toml > auto-detect.

    Falls back to auto-detect if the value is not a valid IANA timezone.
    """
    from syke.config import CFG

    raw = (os.getenv(SYKE_TIMEZONE_ENV) or CFG.timezone or "auto").strip()
    if raw.lower() in ("", "auto", "local", "system"):
        return _detect_system_tz()
    try:
        return ZoneInfo(raw)
    except (KeyError, ValueError):
        import logging

        logging.getLogger(__name__).warning(
            "Invalid timezone '%s', falling back to auto-detect", raw
        )
        return _detect_system_tz()
