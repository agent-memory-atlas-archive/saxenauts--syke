"""Distribution orchestration for downstream agent surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from syke.distribution.context_files import distribute_memex, install_skill

if TYPE_CHECKING:
    from syke.db import SykeDB


@dataclass
class DistributionRefreshResult:
    memex_path: Path | None = None
    skill_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def refresh_distribution(
    db: SykeDB, user_id: str, *, memex_updated: bool = True
) -> DistributionRefreshResult:
    """Refresh the downstream memex and capability surfaces agents rely on."""
    result = DistributionRefreshResult()

    if memex_updated:
        try:
            result.memex_path = distribute_memex(db, user_id)
        except Exception as exc:
            result.warnings.append(f"memex export failed: {exc}")

    try:
        result.skill_paths = install_skill()
    except Exception as exc:
        result.warnings.append(f"skill install failed: {exc}")

    return result


__all__ = ["DistributionRefreshResult", "refresh_distribution"]
