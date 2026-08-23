"""Focused contracts for the v3 current-only SQLite store."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from syke.db import (
    SCHEMA_VERSION,
    SykeDB,
    UnsupportedSchemaError,
    initialize_current_schema,
)


def _insert_memory(
    db: SykeDB,
    memory_id: str,
    user_id: str,
    content: str = "durable memory",
    *,
    created_at: datetime | None = None,
) -> None:
    with db.transaction():
        db.conn.execute(
            """INSERT INTO memories (id, user_id, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL)""",
            (
                memory_id,
                user_id,
                content,
                (created_at or datetime(2026, 1, 1, tzinfo=UTC)).isoformat(),
            ),
        )


def _memory_row(db: SykeDB, memory_id: str) -> dict | None:
    row = db.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return dict(row) if row else None


def _search_memory_ids(db: SykeDB, user_id: str, query: str) -> list[str]:
    rows = db.conn.execute(
        """SELECT memory.id
           FROM memories_fts
           JOIN memories AS memory ON memory.id = memories_fts.memory_id
           WHERE memories_fts MATCH ? AND memory.user_id = ?
           ORDER BY bm25(memories_fts)""",
        (query, user_id),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def test_fresh_database_uses_exact_v3_current_only_schema(tmp_path: Path) -> None:
    with SykeDB(tmp_path / "syke.db") as db:
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert _table_columns(db.conn, "memories") == [
            "id",
            "user_id",
            "content",
            "created_at",
            "updated_at",
        ]
        assert _table_columns(db.conn, "current_memex") == [
            "singleton",
            "id",
            "user_id",
            "content",
            "created_at",
            "updated_at",
        ]
        assert "active" not in _table_columns(db.conn, "memories")
        assert "superseded_by" not in _table_columns(db.conn, "memories")
        assert "source_event_ids" not in _table_columns(db.conn, "memories")
        assert (
            db.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_sources'"
            ).fetchone()
            is None
        )


def test_initialization_is_idempotent(tmp_path: Path) -> None:
    with SykeDB(tmp_path / "idem.db") as db:
        before = db.conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        db.initialize()
        db.initialize()
        after = db.conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]


def test_exported_initializer_builds_an_already_open_empty_connection(tmp_path: Path) -> None:
    path = tmp_path / "builder.db"
    with sqlite3.connect(path) as conn:
        initialize_current_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

        with pytest.raises(UnsupportedSchemaError, match="requires an empty"):
            initialize_current_schema(conn)


@pytest.mark.parametrize("version", [0, 1, 99])
def test_legacy_or_unknown_schema_fails_before_wal_and_preserves_data(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / f"legacy-{version}.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_rows (value TEXT NOT NULL)")
        conn.execute("INSERT INTO legacy_rows VALUES ('preserve me')")
        conn.execute(f"PRAGMA user_version = {version}")
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    with pytest.raises(UnsupportedSchemaError, match="Unsupported Syke database schema"):
        SykeDB(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT value FROM legacy_rows").fetchone()[0] == "preserve me"


def test_extra_table_in_v3_fails_closed_without_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "extra.db"
    with sqlite3.connect(path) as conn:
        initialize_current_schema(conn)
        conn.execute("CREATE TABLE hidden_history (value TEXT NOT NULL)")
        conn.execute("INSERT INTO hidden_history VALUES ('must remain')")
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    with pytest.raises(UnsupportedSchemaError, match="unexpected tables"):
        SykeDB(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT value FROM hidden_history").fetchone()[0] == "must remain"


@pytest.mark.parametrize(
    "tamper_sql",
    [
        """DROP TRIGGER validate_link_endpoints_insert;
           CREATE TRIGGER validate_link_endpoints_insert
           AFTER INSERT ON links BEGIN SELECT 1; END;""",
        """DROP INDEX idx_links_source;
           CREATE INDEX idx_links_source ON links(user_id);""",
    ],
)
def test_same_name_schema_safety_object_tampering_fails_signature(
    tmp_path: Path, tamper_sql: str
) -> None:
    path = tmp_path / "tampered.db"
    with sqlite3.connect(path) as conn:
        initialize_current_schema(conn)
        conn.executescript(tamper_sql)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    with pytest.raises(UnsupportedSchemaError, match="definition signature"):
        SykeDB(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_fk_off_link_guards_reject_missing_and_cross_identity_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "links.db"
    with SykeDB(path) as db:
        _insert_memory(db, "u1-a", "u1")
        _insert_memory(db, "u1-b", "u1")
        _insert_memory(db, "u2-a", "u2")

    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="same Syke identity"):
            conn.execute(
                """INSERT INTO links
                   (id, user_id, source_id, target_id, reason, created_at)
                   VALUES ('missing', 'u1', 'u1-a', 'absent', 'bad', '2026-01-01')"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="same Syke identity"):
            conn.execute(
                """INSERT INTO links
                   (id, user_id, source_id, target_id, reason, created_at)
                   VALUES ('cross', 'u1', 'u1-a', 'u2-a', 'bad', '2026-01-01')"""
            )

        conn.execute(
            """INSERT INTO links
               (id, user_id, source_id, target_id, reason, created_at)
               VALUES ('valid', 'u1', 'u1-a', 'u1-b', 'real', '2026-01-01')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="same Syke identity"):
            conn.execute("UPDATE links SET target_id = 'absent' WHERE id = 'valid'")


def test_linked_memory_delete_requires_explicit_edge_removal_with_fk_off(tmp_path: Path) -> None:
    path = tmp_path / "linked-delete.db"
    with SykeDB(path) as db:
        _insert_memory(db, "a", "u1")
        _insert_memory(db, "b", "u1")
        db.conn.execute(
            """INSERT INTO links
               (id, user_id, source_id, target_id, reason, created_at)
               VALUES ('edge', 'u1', 'a', 'b', 'related', '2026-01-01')"""
        )
        db.conn.commit()

    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="delete linked edges first"):
            conn.execute("DELETE FROM memories WHERE id = 'a'")
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = 'a'").fetchone()[0] == 1


def test_link_schema_uses_restrict_and_rejects_blank_ids(db: SykeDB, user_id: str) -> None:
    _insert_memory(db, "a", user_id)
    _insert_memory(db, "b", user_id)
    foreign_keys = db.conn.execute("PRAGMA foreign_key_list(links)").fetchall()
    assert {str(row["on_delete"]) for row in foreign_keys} == {"RESTRICT"}

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.conn.execute(
            """INSERT INTO links
               (id, user_id, source_id, target_id, reason, created_at)
               VALUES (' ', ?, 'a', 'b', 'bad', '2026-01-01')""",
            (user_id,),
        )
    db.conn.rollback()


def test_bound_identity_guards_all_user_scoped_tables(tmp_path: Path) -> None:
    from syke.memory.memex import update_memex

    with SykeDB(tmp_path / "identity.db", user_id="canonical") as db:
        _insert_memory(db, "a", "canonical")
        _insert_memory(db, "b", "canonical")
        db.conn.execute(
            """INSERT INTO links
               (id, user_id, source_id, target_id, reason, created_at)
               VALUES ('edge', 'canonical', 'a', 'b', 'related', '2026-01-01')"""
        )
        db.conn.commit()
        update_memex(db, "canonical", "map")

        with pytest.raises(sqlite3.IntegrityError, match="memories.user_id"):
            db.conn.execute("UPDATE memories SET user_id = 'other' WHERE id = 'a'")
        with pytest.raises(sqlite3.IntegrityError, match="links.user_id"):
            db.conn.execute("UPDATE links SET user_id = 'other' WHERE id = 'edge'")
        with pytest.raises(sqlite3.IntegrityError, match="current_memex.user_id"):
            db.conn.execute("UPDATE current_memex SET user_id = 'other'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.conn.execute("UPDATE syke_identity SET user_id = 'other'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            db.conn.execute("DELETE FROM syke_identity")
        db.conn.rollback()


def test_bind_identity_consolidates_an_unbound_fresh_store(tmp_path: Path) -> None:
    from syke.memory.memex import update_memex

    created = datetime(2026, 2, 3, tzinfo=UTC)
    with SykeDB(tmp_path / "identity.db") as db:
        _insert_memory(db, "a", "alias", created_at=created)
        _insert_memory(db, "b", "alias")
        db.conn.execute(
            """INSERT INTO links
               (id, user_id, source_id, target_id, reason, created_at)
               VALUES ('edge', 'alias', 'a', 'b', 'related', '2026-02-03')"""
        )
        db.conn.commit()
        memex_id = update_memex(db, "alias", "map")

        db.bind_identity("canonical")

        assert _memory_row(db, "a")["created_at"] == created.isoformat()
        assert db.get_memex("canonical")["id"] == memex_id
        for table in ("memories", "links", "current_memex"):
            assert db.conn.execute(f"SELECT DISTINCT user_id FROM {table}").fetchone()[0] == (
                "canonical"
            )
        with pytest.raises(ValueError, match="bound to 'canonical'"):
            db.bind_identity("other")


def test_ids_and_created_at_are_immutable(db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    _insert_memory(db, "a", user_id)
    _insert_memory(db, "b", user_id)
    db.conn.execute(
        """INSERT INTO links
           (id, user_id, source_id, target_id, reason, created_at)
           VALUES ('edge', ?, 'a', 'b', 'related', '2026-01-01')""",
        (user_id,),
    )
    db.conn.commit()
    update_memex(db, user_id, "map")

    statements = (
        "UPDATE memories SET id = 'changed' WHERE id = 'a'",
        "UPDATE memories SET created_at = 'changed' WHERE id = 'a'",
        "UPDATE links SET id = 'changed' WHERE id = 'edge'",
        "UPDATE links SET created_at = 'changed' WHERE id = 'edge'",
        "UPDATE current_memex SET id = 'changed'",
        "UPDATE current_memex SET created_at = 'changed'",
    )
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.conn.execute(statement)
        db.conn.rollback()


def test_update_memex_uses_singleton_and_preserves_identity(db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    memex_id = update_memex(db, user_id, "Version 1")
    first = db.get_memex(user_id)
    assert first["content"] == "Version 1"
    assert first["updated_at"] is None
    assert db.count_memories(user_id) == 0

    assert update_memex(db, user_id, "Version 1") == memex_id
    assert db.get_memex(user_id) == first
    assert update_memex(db, user_id, "Version 2") == memex_id

    updated = db.get_memex(user_id)
    assert updated["id"] == memex_id
    assert updated["created_at"] == first["created_at"]
    assert updated["content"] == "Version 2"
    assert updated["updated_at"] is not None
    assert db.conn.execute("SELECT COUNT(*) FROM current_memex").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0] == 0


def test_update_memex_strips_projection_header(db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "# MEMEX [10 / 2,000 tokens · 1%]\n\ncanonical body")
    assert db.get_memex(user_id)["content"] == "canonical body"


def test_get_memex_for_injection_fallback_and_current_content(db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import get_memex_for_injection, update_memex

    fallback = get_memex_for_injection(db, user_id)
    assert "First run" in fallback
    assert "syke status --json" in fallback
    assert get_memex_for_injection(db, user_id, context="synthesis") == ""

    update_memex(db, user_id, "current map")
    assert get_memex_for_injection(db, user_id) == "current map"


def test_count_memories_counts_only_ordinary_current_rows(db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    _insert_memory(db, "a", user_id)
    _insert_memory(db, "b", user_id)
    update_memex(db, user_id, "map")

    assert db.count_memories(user_id) == 2
    with pytest.raises(TypeError):
        db.count_memories(user_id, True)  # type: ignore[call-arg]


def test_fts_tracks_every_memory_insert_revision_and_delete(db: SykeDB, user_id: str) -> None:
    _insert_memory(db, "fts", user_id, "old content about dogs")
    assert _search_memory_ids(db, user_id, "dogs") == ["fts"]

    db.conn.execute(
        "UPDATE memories SET content = 'new content about cats', updated_at = '2026-02-01' "
        "WHERE id = 'fts'"
    )
    db.conn.commit()
    assert _search_memory_ids(db, user_id, "dogs") == []
    assert _search_memory_ids(db, user_id, "cats") == ["fts"]

    db.conn.execute("DELETE FROM memories WHERE id = 'fts'")
    db.conn.commit()
    assert _search_memory_ids(db, user_id, "cats") == []


def test_graph_stats_describe_only_current_ordinary_graph(db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    _insert_memory(db, "a", user_id, "alpha")
    _insert_memory(db, "b", user_id, "beta")
    _insert_memory(db, "c", user_id, "gamma")
    db.conn.execute(
        """INSERT INTO links
           (id, user_id, source_id, target_id, reason, created_at)
           VALUES ('edge', ?, 'a', 'b', 'related', '2026-01-01')""",
        (user_id,),
    )
    db.conn.commit()
    update_memex(db, user_id, "map")

    stats = db.get_graph_stats(user_id)
    assert stats["memories"] == 3
    assert stats["links"] == 1
    assert stats["unlinked"] == 1
    assert stats["links_outside_graph"] == 0


def test_outer_transaction_defers_memories_links_and_memex(tmp_path: Path) -> None:
    from syke.memory.memex import update_memex

    path = tmp_path / "transaction.db"
    with SykeDB(path) as db:
        _insert_memory(db, "a", "u1")
        with SykeDB(path) as observer:
            assert _memory_row(observer, "a") is not None

        with db.transaction():
            _insert_memory(db, "b", "u1")
            db.conn.execute(
                """INSERT INTO links
                   (id, user_id, source_id, target_id, reason, created_at)
                   VALUES ('edge', 'u1', 'a', 'b', 'related', '2026-01-01')"""
            )
            update_memex(db, "u1", "map")

            with sqlite3.connect(path) as observer:
                assert (
                    observer.execute("SELECT COUNT(*) FROM memories WHERE id = 'b'").fetchone()[0]
                    == 0
                )
                assert observer.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0
                assert observer.execute("SELECT COUNT(*) FROM current_memex").fetchone()[0] == 0

        with SykeDB(path) as observer:
            assert _memory_row(observer, "b") is not None
            assert observer.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1
            assert observer.get_memex("u1")["content"] == "map"
