from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from syke.runtime.prompt_context import format_now_for_prompt


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="host timezone cannot be changed")
def test_format_now_for_prompt_uses_host_timezone_at_reference_instant() -> None:
    previous_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        assert (
            format_now_for_prompt(datetime.fromisoformat("2026-05-29T10:00:00+00:00"))
            == "2026-05-29 03:00 PDT (UTC-7)"
        )
        assert (
            format_now_for_prompt(datetime.fromisoformat("2026-03-07T23:59:00-08:00"))
            == "2026-03-07 23:59 PST (UTC-8)"
        )
        assert format_now_for_prompt(datetime(2026, 1, 15, 12, 0)) == (
            "2026-01-15 12:00 PST (UTC-8)"
        )
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()
