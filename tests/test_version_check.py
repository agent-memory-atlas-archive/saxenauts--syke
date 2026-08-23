from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import patch

import syke.version_check as version_check


def test_version_check_fails_closed_on_unusable_external_data(tmp_path: Path) -> None:
    cache = tmp_path / "version_cache.json"
    cache.write_text("{")

    with (
        patch("syke.version_check.CACHE_PATH", cache),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
    ):
        assert version_check.get_latest_version() is None

    assert version_check._version_gt("2.0.0", "1.9.9") is True
    assert version_check._version_gt("invalid", "1.0.0") is False
    assert version_check._version_gt("1.0.0", "invalid") is False
