"""Tests for cross-process database leases and connection ownership."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from syke.daemon.web import _open_ro
from syke.db import DatabaseMaintenanceError, SykeDB
from syke.db_access import (
    DatabaseLeaseUnavailable,
    acquire_database_lease,
    database_lock_path,
)


@pytest.mark.skipif(os.name == "nt", reason="Windows fallback serializes shared leases")
def test_shared_leases_coexist_and_block_exclusive_lease(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    first = acquire_database_lease(path, blocking=False)
    second = acquire_database_lease(path, blocking=False)
    try:
        with pytest.raises(DatabaseLeaseUnavailable):
            acquire_database_lease(path, exclusive=True, blocking=False)
    finally:
        second.release()
        first.release()

    with acquire_database_lease(path, exclusive=True, blocking=False):
        pass


def test_exclusive_lease_blocks_normal_database_lease(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    with acquire_database_lease(path, exclusive=True, blocking=False):
        with pytest.raises(DatabaseLeaseUnavailable):
            acquire_database_lease(path, blocking=False)

    with acquire_database_lease(path, blocking=False):
        pass


def test_shared_lease_blocks_exclusive_lease_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    script = (
        "import sys\n"
        "from syke.db_access import acquire_database_lease\n"
        "lease = acquire_database_lease(sys.argv[1])\n"
        "print('ready', flush=True)\n"
        "try:\n"
        "    input()\n"
        "finally:\n"
        "    lease.release()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == "ready\n"
        with pytest.raises(DatabaseLeaseUnavailable):
            acquire_database_lease(path, exclusive=True, blocking=False)
    finally:
        stdout, stderr = process.communicate("\n", timeout=5)

    assert process.returncode == 0, stdout + stderr
    with acquire_database_lease(path, exclusive=True, blocking=False):
        pass


def test_maintenance_marker_is_checked_after_shared_lease(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    marker = Path(f"{path}.maintenance")
    marker.write_text("migration in progress\n", encoding="utf-8")

    with pytest.raises(DatabaseMaintenanceError, match="unavailable during maintenance"):
        SykeDB(path)

    assert database_lock_path(path).exists()
    assert not path.exists()


def test_suspend_retains_lease_and_reopen_reuses_it(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    db = SykeDB(path, user_id="test_user")
    retained_lease = db._lease

    db.suspend()

    with pytest.raises(RuntimeError, match="suspended or closed"):
        _ = db.conn
    with pytest.raises(DatabaseLeaseUnavailable):
        acquire_database_lease(path, exclusive=True, blocking=False)

    db.reopen()
    assert db._lease is retained_lease
    assert db.conn.execute("SELECT 1").fetchone()[0] == 1

    db.close()
    with acquire_database_lease(path, exclusive=True, blocking=False):
        pass


def test_reopen_after_close_acquires_a_new_lease(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    db = SykeDB(path)
    old_lease = db._lease

    db.close()
    assert old_lease is not None and old_lease.released

    db.reopen()
    assert db._lease is not old_lease
    assert db.conn.execute("SELECT 1").fetchone()[0] == 1
    db.close()


def test_read_only_connection_holds_shared_lease(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    SykeDB(path).close()

    with _open_ro(str(path)) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        with pytest.raises(DatabaseLeaseUnavailable):
            acquire_database_lease(path, exclusive=True, blocking=False)

    with acquire_database_lease(path, exclusive=True, blocking=False):
        pass


def test_read_only_connection_checks_maintenance_after_shared_lease(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    SykeDB(path).close()
    marker = Path(f"{path}.maintenance")
    marker.write_text("migration in progress\n", encoding="utf-8")

    with pytest.raises(DatabaseMaintenanceError, match="unavailable during maintenance"):
        with _open_ro(str(path)):
            pass

    assert database_lock_path(path).exists()
