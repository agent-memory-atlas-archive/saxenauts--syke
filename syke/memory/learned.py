"""One bounded current learned-language memory."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.memory.memex_budget import MEMEX_TOKEN_ENCODING, count_memex_tokens

LEARNED_MEMORY_ID = "syke-learned"
LEARNED_PROJECTION_TOKEN_LIMIT = 1_000


def render_learned_projection(content: str) -> str:
    """Render non-empty learned language as its complete prompt section."""
    body = content.strip()
    return f"# Learned\n\n{body}" if body else ""


def measure_learned_projection(content: str) -> dict[str, int | str | bool]:
    """Measure the complete learned section against its hard prompt budget."""
    tokens = count_memex_tokens(render_learned_projection(content))
    return {
        "tokens": tokens,
        "limit": LEARNED_PROJECTION_TOKEN_LIMIT,
        "encoding": MEMEX_TOKEN_ENCODING,
        "fill_pct": min(100, round(tokens / LEARNED_PROJECTION_TOKEN_LIMIT * 100)),
        "over_budget": tokens > LEARNED_PROJECTION_TOKEN_LIMIT,
    }


def get_learned_memory(db: Any, user_id: str) -> dict[str, Any] | None:
    """Return the one memory selected for learned-language projection."""
    row = db.conn.execute(
        """SELECT id, user_id, content, created_at, updated_at
           FROM memories
           WHERE id = ? AND user_id = ?""",
        (LEARNED_MEMORY_ID, user_id),
    ).fetchone()
    return dict(row) if row is not None else None


def get_valid_learned_memory_from_path(
    db_path: str | Path,
    user_id: str,
) -> dict[str, Any] | None:
    """Read one committed learned row that is safe to project after recovery."""
    path = Path(db_path).expanduser().resolve()
    uri = f"file:{path}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT id, user_id, content, created_at, updated_at
               FROM memories
               WHERE id = ? AND user_id = ?""",
            (LEARNED_MEMORY_ID, user_id),
        ).fetchone()
    if row is None:
        return None
    snapshot = dict(row)
    measurement = measure_learned_projection(str(snapshot.get("content") or ""))
    return None if measurement["over_budget"] else snapshot


def update_learned_memory(
    db: Any,
    user_id: str,
    content: str,
    *,
    created_at: str | None = None,
) -> str:
    """Create or revise the learned memory while preserving its identity."""
    now = datetime.now(UTC).isoformat()
    existing = get_learned_memory(db, user_id)
    with db.transaction():
        if existing is None:
            db.conn.execute(
                """INSERT INTO memories (id, user_id, content, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL)""",
                (LEARNED_MEMORY_ID, user_id, content, created_at or now),
            )
        elif str(existing["content"]) != content:
            db.conn.execute(
                """UPDATE memories
                   SET content = ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (content, now, LEARNED_MEMORY_ID, user_id),
            )
    return LEARNED_MEMORY_ID


def apply_learned_memory_snapshot_to_connection(
    conn: sqlite3.Connection,
    user_id: str,
    snapshot: dict[str, Any],
) -> None:
    """Merge one valid learned row through an existing SQLite transaction."""
    if str(snapshot.get("id") or "") != LEARNED_MEMORY_ID:
        raise ValueError("learned memory snapshot has the wrong identity")
    if str(snapshot.get("user_id") or "") != user_id:
        raise ValueError("learned memory snapshot belongs to another Syke identity")
    if measure_learned_projection(str(snapshot.get("content") or ""))["over_budget"]:
        raise ValueError("learned memory snapshot is over its prompt budget")

    existing = conn.execute(
        "SELECT 1 FROM memories WHERE id = ? AND user_id = ?",
        (LEARNED_MEMORY_ID, user_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO memories (id, user_id, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                LEARNED_MEMORY_ID,
                user_id,
                str(snapshot.get("content") or ""),
                str(snapshot.get("created_at") or datetime.now(UTC).isoformat()),
                snapshot.get("updated_at"),
            ),
        )
    else:
        conn.execute(
            """UPDATE memories
               SET content = ?, updated_at = ?
               WHERE id = ? AND user_id = ?""",
            (
                str(snapshot.get("content") or ""),
                snapshot.get("updated_at"),
                LEARNED_MEMORY_ID,
                user_id,
            ),
        )
