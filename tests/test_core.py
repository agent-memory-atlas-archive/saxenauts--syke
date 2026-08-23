from __future__ import annotations

from datetime import tzinfo

import pytest

from syke.time import (
    resolve_user_tz,
)


@pytest.mark.parametrize(
    "env_value,expected_key",
    [
        ("America/New_York", "America/New_York"),
        ("", None),
        ("Not/AZone", None),
    ],
)
def test_resolve_user_tz_honors_env_and_auto_fallback(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_key: str | None,
) -> None:
    monkeypatch.setenv("SYKE_TIMEZONE", env_value)
    tz = resolve_user_tz()

    assert isinstance(tz, tzinfo)
    if expected_key is not None:
        assert getattr(tz, "key", None) == expected_key
