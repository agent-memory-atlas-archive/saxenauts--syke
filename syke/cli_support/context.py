"""Shared CLI runtime context helpers."""

from __future__ import annotations

from syke.cli_support.exit_codes import SykeDataException
from syke.config import user_syke_db_path, user_workspace_dir
from syke.db import SykeDB
from syke.db_safety import try_reconcile_before_database_use


def get_db(user_id: str) -> SykeDB:
    """Get the initialized DB bound to this installation's one person."""
    try:
        db_path = user_syke_db_path(user_id)
        try_reconcile_before_database_use(
            user_id,
            memex_path=user_workspace_dir(user_id) / "MEMEX.md",
            expected_db_path=db_path,
        )
        return SykeDB(db_path, user_id=user_id)
    except (ValueError, RuntimeError) as exc:
        raise SykeDataException(str(exc)) from exc
