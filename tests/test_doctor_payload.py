from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("assessment", "healthy"),
    [
        ("active", True),
        ("recent", True),
        ("idle", True),
        ("stale", False),
        ("degraded", False),
        ("never_run", False),
        ("unknown", False),
    ],
)
def test_doctor_treats_overnight_idle_synthesis_as_healthy(assessment: str, healthy: bool) -> None:
    from syke.cli_support.doctor import synthesis_assessment_is_healthy

    assert synthesis_assessment_is_healthy(assessment) is healthy
