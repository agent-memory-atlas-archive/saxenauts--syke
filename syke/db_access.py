"""Cross-process access leases for a Syke database file."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any, BinaryIO, cast

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows platforms
    msvcrt = None


class DatabaseLeaseUnavailable(RuntimeError):
    """Raised when another process holds an incompatible database lease."""


def _resolved_database_path(db_path: str | os.PathLike[str]) -> Path:
    path_str = os.fspath(db_path)
    if path_str == ":memory:":
        raise ValueError("an in-memory database has no cross-process lease")
    return Path(path_str).expanduser().resolve()


def database_lock_path(db_path: str | os.PathLike[str]) -> Path:
    """Return the stable sidecar used to coordinate database replacement."""
    return Path(f"{_resolved_database_path(db_path)}.lock")


def maintenance_marker_path(db_path: str | os.PathLike[str]) -> Path:
    """Return the marker that blocks a database open after its lease is held."""
    return Path(f"{_resolved_database_path(db_path)}.maintenance")


class DatabaseLease:
    """A held shared or exclusive lease on a database sidecar."""

    def __init__(self, handle: BinaryIO, lock_path: Path, *, exclusive: bool):
        self._handle = handle
        self.lock_path = lock_path
        self.exclusive = exclusive
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        """Release this lease. Repeated calls are safe."""
        if self._released:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows fallback
                windows_lock = cast(Any, msvcrt)
                self._handle.seek(0)
                windows_lock.locking(self._handle.fileno(), windows_lock.LK_UNLCK, 1)
        finally:
            self._released = True
            self._handle.close()

    def __enter__(self) -> DatabaseLease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_database_lease(
    db_path: str | os.PathLike[str],
    *,
    exclusive: bool = False,
    blocking: bool = True,
) -> DatabaseLease:
    """Acquire a shared normal-use lease or an exclusive maintenance lease."""
    lock_path = database_lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if fcntl is not None:
            flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), flags)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                raise DatabaseLeaseUnavailable(str(lock_path)) from exc
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            # msvcrt has no shared-lock operation, so serialize all access there.
            windows_lock = cast(Any, msvcrt)
            if lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            mode = windows_lock.LK_LOCK if blocking else windows_lock.LK_NBLCK
            try:
                windows_lock.locking(handle.fileno(), mode, 1)
            except OSError as exc:
                raise DatabaseLeaseUnavailable(str(lock_path)) from exc
        else:  # pragma: no cover - no supported lock API
            raise RuntimeError("this platform has no supported database lock API")
        return DatabaseLease(handle, lock_path, exclusive=exclusive)
    except BaseException:
        handle.close()
        raise
