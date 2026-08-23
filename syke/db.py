"""SQLite schema and current-graph queries."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from syke.db_access import DatabaseLease, acquire_database_lease, maintenance_marker_path

SCHEMA_VERSION = 3
GRAPH_IDENTITY_TABLES = ("memories", "links", "current_memex")

_TABLE_COLUMNS = {
    "syke_identity": ("singleton", "user_id", "created_at"),
    "memories": ("id", "user_id", "content", "created_at", "updated_at"),
    "links": ("id", "user_id", "source_id", "target_id", "reason", "created_at"),
    "current_memex": (
        "singleton",
        "id",
        "user_id",
        "content",
        "created_at",
        "updated_at",
    ),
    "memories_fts": ("memory_id", "content"),
}

_FTS_SHADOW_TABLES = {
    "memories_fts_config",
    "memories_fts_content",
    "memories_fts_data",
    "memories_fts_docsize",
    "memories_fts_idx",
}

_REQUIRED_INDEXES = {
    "idx_memories_user_created",
    "idx_links_source",
    "idx_links_target",
}

_REQUIRED_TRIGGERS = {
    "enforce_memories_identity_insert",
    "enforce_memories_identity_update",
    "enforce_links_identity_insert",
    "enforce_links_identity_update",
    "enforce_current_memex_identity_insert",
    "enforce_current_memex_identity_update",
    "protect_syke_identity_update",
    "protect_syke_identity_delete",
    "protect_memories_stable_fields",
    "protect_links_stable_fields",
    "protect_current_memex_stable_fields",
    "protect_current_memex_delete",
    "validate_link_endpoints_insert",
    "validate_link_endpoints_update",
    "require_link_removal_before_memory_delete",
    "memories_fts_insert",
    "memories_fts_update",
    "memories_fts_delete",
}

_EXPECTED_SCHEMA_SIGNATURE = "022c723b41565db675291bbfaebe58c7f2e2de82b5fdb1f7ee3eb93da60e4992"

_SCHEMA_SQL = f"""
BEGIN IMMEDIATE;

CREATE TABLE syke_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE memories (
    id TEXT PRIMARY KEY NOT NULL CHECK (trim(id) <> ''),
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE links (
    id TEXT PRIMARY KEY NOT NULL CHECK (trim(id) <> ''),
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES memories(id) ON DELETE RESTRICT,
    FOREIGN KEY (target_id) REFERENCES memories(id) ON DELETE RESTRICT
);

CREATE TABLE current_memex (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    id TEXT NOT NULL UNIQUE CHECK (trim(id) <> ''),
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX idx_memories_user_created ON memories(user_id, created_at DESC);
CREATE INDEX idx_links_source ON links(source_id);
CREATE INDEX idx_links_target ON links(target_id);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    memory_id UNINDEXED,
    content,
    tokenize='porter unicode61'
);

CREATE TRIGGER enforce_memories_identity_insert
BEFORE INSERT ON memories
WHEN EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
 AND NEW.user_id != (SELECT user_id FROM syke_identity WHERE singleton = 1)
BEGIN
    SELECT RAISE(ABORT, 'memories.user_id does not match Syke identity');
END;

CREATE TRIGGER enforce_memories_identity_update
BEFORE UPDATE OF user_id ON memories
WHEN EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
 AND NEW.user_id != (SELECT user_id FROM syke_identity WHERE singleton = 1)
BEGIN
    SELECT RAISE(ABORT, 'memories.user_id does not match Syke identity');
END;

CREATE TRIGGER enforce_links_identity_insert
BEFORE INSERT ON links
WHEN EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
 AND NEW.user_id != (SELECT user_id FROM syke_identity WHERE singleton = 1)
BEGIN
    SELECT RAISE(ABORT, 'links.user_id does not match Syke identity');
END;

CREATE TRIGGER enforce_links_identity_update
BEFORE UPDATE OF user_id ON links
WHEN EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
 AND NEW.user_id != (SELECT user_id FROM syke_identity WHERE singleton = 1)
BEGIN
    SELECT RAISE(ABORT, 'links.user_id does not match Syke identity');
END;

CREATE TRIGGER enforce_current_memex_identity_insert
BEFORE INSERT ON current_memex
WHEN EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
 AND NEW.user_id != (SELECT user_id FROM syke_identity WHERE singleton = 1)
BEGIN
    SELECT RAISE(ABORT, 'current_memex.user_id does not match Syke identity');
END;

CREATE TRIGGER enforce_current_memex_identity_update
BEFORE UPDATE OF user_id ON current_memex
WHEN EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
 AND NEW.user_id != (SELECT user_id FROM syke_identity WHERE singleton = 1)
BEGIN
    SELECT RAISE(ABORT, 'current_memex.user_id does not match Syke identity');
END;

CREATE TRIGGER protect_syke_identity_update
BEFORE UPDATE ON syke_identity
BEGIN
    SELECT RAISE(ABORT, 'Syke identity is immutable');
END;

CREATE TRIGGER protect_syke_identity_delete
BEFORE DELETE ON syke_identity
BEGIN
    SELECT RAISE(ABORT, 'Syke identity cannot be deleted');
END;

CREATE TRIGGER protect_memories_stable_fields
BEFORE UPDATE OF id, created_at ON memories
WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'memory identity and created_at are immutable');
END;

CREATE TRIGGER protect_links_stable_fields
BEFORE UPDATE OF id, created_at ON links
WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'link identity and created_at are immutable');
END;

CREATE TRIGGER protect_current_memex_stable_fields
BEFORE UPDATE OF singleton, id, created_at ON current_memex
WHEN NEW.singleton IS NOT OLD.singleton
  OR NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'current MEMEX identity and created_at are immutable');
END;

CREATE TRIGGER protect_current_memex_delete
BEFORE DELETE ON current_memex
BEGIN
    SELECT RAISE(ABORT, 'current MEMEX cannot be deleted');
END;

CREATE TRIGGER validate_link_endpoints_insert
BEFORE INSERT ON links
WHEN (
    NOT EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
    OR NEW.user_id = (SELECT user_id FROM syke_identity WHERE singleton = 1)
)
 AND NOT EXISTS (
    SELECT 1
    FROM memories AS source
    JOIN memories AS target
      ON target.id = NEW.target_id AND target.user_id = NEW.user_id
    WHERE source.id = NEW.source_id AND source.user_id = NEW.user_id
)
BEGIN
    SELECT RAISE(ABORT, 'link endpoints must reference memories for the same Syke identity');
END;

CREATE TRIGGER validate_link_endpoints_update
BEFORE UPDATE OF user_id, source_id, target_id ON links
WHEN (
    NOT EXISTS (SELECT 1 FROM syke_identity WHERE singleton = 1)
    OR NEW.user_id = (SELECT user_id FROM syke_identity WHERE singleton = 1)
)
 AND NOT EXISTS (
    SELECT 1
    FROM memories AS source
    JOIN memories AS target
      ON target.id = NEW.target_id AND target.user_id = NEW.user_id
    WHERE source.id = NEW.source_id AND source.user_id = NEW.user_id
)
BEGIN
    SELECT RAISE(ABORT, 'link endpoints must reference memories for the same Syke identity');
END;

CREATE TRIGGER require_link_removal_before_memory_delete
BEFORE DELETE ON memories
WHEN EXISTS (
    SELECT 1 FROM links
    WHERE source_id = OLD.id OR target_id = OLD.id
)
BEGIN
    SELECT RAISE(ABORT, 'delete linked edges first');
END;

CREATE TRIGGER memories_fts_insert
AFTER INSERT ON memories
BEGIN
    INSERT INTO memories_fts(memory_id, content) VALUES (NEW.id, NEW.content);
END;

CREATE TRIGGER memories_fts_update
AFTER UPDATE OF content ON memories
WHEN NEW.content IS NOT OLD.content
BEGIN
    DELETE FROM memories_fts WHERE memory_id = OLD.id;
    INSERT INTO memories_fts(memory_id, content) VALUES (NEW.id, NEW.content);
END;

CREATE TRIGGER memories_fts_delete
AFTER DELETE ON memories
BEGIN
    DELETE FROM memories_fts WHERE memory_id = OLD.id;
END;

PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""


class UnsupportedSchemaError(RuntimeError):
    """Raised when a database is not an empty store or the exact current schema."""


class DatabaseMaintenanceError(RuntimeError):
    """Raised when the external maintenance marker blocks ordinary database use."""


def _application_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _normalize_schema_sql(sql: str) -> str:
    return " ".join(sql.split()).lower()


def _schema_signature(conn: sqlite3.Connection) -> str:
    tracked_names = set(_TABLE_COLUMNS) | _REQUIRED_INDEXES | _REQUIRED_TRIGGERS
    rows = conn.execute(
        """SELECT type, name, sql
           FROM sqlite_master
           WHERE sql IS NOT NULL
           ORDER BY type, name"""
    ).fetchall()
    definitions = [
        f"{row[0]}\0{row[1]}\0{_normalize_schema_sql(str(row[2]))}"
        for row in rows
        if str(row[1]) in tracked_names
    ]
    return sha256("\n".join(definitions).encode("utf-8")).hexdigest()


def _validate_current_schema(conn: sqlite3.Connection) -> None:
    version = _schema_version(conn)
    if version != SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"Unsupported Syke database schema version {version}; expected {SCHEMA_VERSION}"
        )

    expected_tables = set(_TABLE_COLUMNS) | _FTS_SHADOW_TABLES
    actual_tables = _application_tables(conn)
    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        unexpected = sorted(actual_tables - expected_tables)
        raise UnsupportedSchemaError(
            f"Syke database schema does not match v{SCHEMA_VERSION} "
            f"(missing tables: {missing}; unexpected tables: {unexpected})"
        )

    for table, expected_columns in _TABLE_COLUMNS.items():
        actual_columns = tuple(
            str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        )
        if actual_columns != expected_columns:
            raise UnsupportedSchemaError(
                f"Syke database table {table!r} does not match v{SCHEMA_VERSION}: {actual_columns}"
            )

    actual_triggers = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    }
    if actual_triggers != _REQUIRED_TRIGGERS:
        missing = sorted(_REQUIRED_TRIGGERS - actual_triggers)
        unexpected = sorted(actual_triggers - _REQUIRED_TRIGGERS)
        raise UnsupportedSchemaError(
            f"Syke database trigger set does not match v{SCHEMA_VERSION} "
            f"(missing: {missing}; unexpected: {unexpected})"
        )

    actual_indexes = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
        ).fetchall()
    }
    if actual_indexes != _REQUIRED_INDEXES:
        missing = sorted(_REQUIRED_INDEXES - actual_indexes)
        unexpected = sorted(actual_indexes - _REQUIRED_INDEXES)
        raise UnsupportedSchemaError(
            f"Syke database index set does not match v{SCHEMA_VERSION} "
            f"(missing: {missing}; unexpected: {unexpected})"
        )

    actual_signature = _schema_signature(conn)
    if actual_signature != _EXPECTED_SCHEMA_SIGNATURE:
        raise UnsupportedSchemaError(
            "Syke database schema definition signature does not match "
            f"v{SCHEMA_VERSION} ({actual_signature})"
        )


def initialize_current_schema(conn: sqlite3.Connection) -> None:
    """Create the v3 current-only schema in an already-open empty database."""
    version = _schema_version(conn)
    tables = _application_tables(conn)
    if version != 0 or tables:
        raise UnsupportedSchemaError(
            "Current Syke schema initialization requires an empty unversioned database"
        )

    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(_SCHEMA_SQL)
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    _validate_current_schema(conn)


class SykeDB:
    """SQLite facade for Syke's mutable current graph."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        user_id: str | None = None,
    ):
        if not isinstance(db_path, (str, os.PathLike)):
            raise TypeError(f"SykeDB(db_path) expects a path-like value, got {type(db_path)!r}")
        path_str = os.fspath(db_path)
        if (
            path_str != ":memory:"
            and "/" not in path_str
            and "\\" not in path_str
            and not path_str.endswith(".db")
        ):
            raise ValueError(
                f"SykeDB(db_path) looks like a username, not a file path: {path_str!r}. "
                "Use user_syke_db_path(user_id) to get the correct path."
            )

        self.db_path = path_str
        self._lease: DatabaseLease | None = None
        self._conn: sqlite3.Connection | None = None
        self._in_transaction = False
        try:
            self._ensure_lease()
            self._conn = self._connect_db(path_str)
            self.initialize()
            if user_id is not None:
                self.bind_identity(user_id)
        except BaseException:
            self.close()
            raise

    def _ensure_lease(self) -> None:
        if self.db_path != ":memory:" and self._lease is None:
            self._lease = acquire_database_lease(self.db_path)

    @staticmethod
    def _connect_db(db_path: str) -> sqlite3.Connection:
        if db_path != ":memory:":
            path = Path(db_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            maintenance_path = maintenance_marker_path(path)
            if maintenance_path.exists():
                raise DatabaseMaintenanceError(
                    f"Syke database is unavailable during maintenance: {maintenance_path}"
                )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            version = _schema_version(conn)
            tables = _application_tables(conn)
            if tables:
                _validate_current_schema(conn)
            elif version != 0:
                raise UnsupportedSchemaError(
                    f"Unsupported empty Syke database schema version {version}"
                )

            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        except BaseException:
            conn.close()
            raise

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Syke database connection is suspended or closed")
        return self._conn

    def __enter__(self) -> SykeDB:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        """Commit the outermost atomic write and roll it back on any failure."""
        conn = self.conn
        if self._in_transaction:
            yield
            return

        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            self._in_transaction = False

    def initialize(self) -> None:
        """Create a fresh v3 schema or validate an existing v3 store."""
        conn = self.conn
        if not _application_tables(conn):
            initialize_current_schema(conn)
        else:
            _validate_current_schema(conn)

    def bind_identity(self, user_id: str) -> None:
        """Bind the current graph to one person."""
        canonical_user_id = user_id.strip()
        if not canonical_user_id:
            raise ValueError("Syke identity cannot be empty")

        conn = self.conn
        identity_row = conn.execute(
            "SELECT user_id FROM syke_identity WHERE singleton = 1"
        ).fetchone()
        if identity_row is not None:
            bound_user_id = str(identity_row["user_id"])
            if bound_user_id != canonical_user_id:
                raise ValueError(
                    f"Syke store is bound to {bound_user_id!r}, not {canonical_user_id!r}"
                )
            alias_counts = {
                table: int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE user_id != ?",
                        (canonical_user_id,),
                    ).fetchone()[0]
                )
                for table in GRAPH_IDENTITY_TABLES
            }
            if any(alias_counts.values()):
                raise RuntimeError(f"Syke graph contains rows outside its identity: {alias_counts}")
            return

        with self.transaction():
            for table in GRAPH_IDENTITY_TABLES:
                conn.execute(
                    f"UPDATE {table} SET user_id = ? WHERE user_id != ?",
                    (canonical_user_id, canonical_user_id),
                )
            conn.execute(
                "INSERT INTO syke_identity (singleton, user_id, created_at) VALUES (1, ?, ?)",
                (canonical_user_id, datetime.now(UTC).isoformat()),
            )

    def get_graph_stats(self, user_id: str) -> dict:
        """Return mechanical statistics for the current ordinary graph."""
        conn = self.conn
        memory_count = int(
            conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()[
                0
            ]
        )
        link_count = int(
            conn.execute("SELECT COUNT(*) FROM links WHERE user_id = ?", (user_id,)).fetchone()[0]
        )
        hub_rows = conn.execute(
            """WITH degrees AS (
                   SELECT id AS link_id, source_id AS memory_id
                   FROM links WHERE user_id = ?
                   UNION ALL
                   SELECT id AS link_id, target_id AS memory_id
                   FROM links WHERE user_id = ?
               )
               SELECT memory.id, SUBSTR(memory.content, 1, 60) AS preview,
                      COUNT(DISTINCT degrees.link_id) AS link_count
               FROM memories AS memory
               JOIN degrees ON degrees.memory_id = memory.id
               WHERE memory.user_id = ?
               GROUP BY memory.id
               ORDER BY link_count DESC, memory.id
               LIMIT 5""",
            (user_id, user_id, user_id),
        ).fetchall()
        unlinked_count = int(
            conn.execute(
                """SELECT COUNT(*)
                   FROM memories AS memory
                   WHERE memory.user_id = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM links
                         WHERE user_id = memory.user_id
                           AND (source_id = memory.id OR target_id = memory.id)
                     )""",
                (user_id,),
            ).fetchone()[0]
        )
        return {
            "memories": memory_count,
            "links": link_count,
            "links_per_memory": round(link_count / memory_count, 2) if memory_count else 0,
            "hubs": [
                {
                    "preview": str(row["preview"]).strip().split("\n")[0],
                    "links": int(row["link_count"]),
                }
                for row in hub_rows
            ],
            "unlinked": unlinked_count,
            "unlinked_rate": round(unlinked_count / memory_count, 2) if memory_count else 0,
            "links_outside_graph": 0,
        }

    def count_memories(self, user_id: str) -> int:
        """Count current ordinary memories for a user."""
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        )

    def get_memex(self, user_id: str) -> dict | None:
        """Return the current MEMEX singleton for a user."""
        row = self.conn.execute(
            "SELECT * FROM current_memex WHERE singleton = 1 AND user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        try:
            self.suspend()
        finally:
            lease, self._lease = self._lease, None
            if lease is not None:
                lease.release()

    def suspend(self) -> None:
        """Close SQLite while retaining this process's shared database lease."""
        conn, self._conn = self._conn, None
        self._in_transaction = False
        if conn is not None:
            conn.close()

    def reopen(self) -> None:
        """Reopen SQLite, reusing a retained lease or acquiring a new one."""
        if self._conn is not None:
            self.suspend()
        try:
            self._ensure_lease()
            self._conn = self._connect_db(self.db_path)
            self._in_transaction = False
            self.initialize()
        except BaseException:
            self.close()
            raise
