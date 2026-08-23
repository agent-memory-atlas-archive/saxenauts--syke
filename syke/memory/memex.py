"""Current MEMEX access for the agent's map of the user."""

from __future__ import annotations

from datetime import UTC, datetime

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.memory.memex_budget import strip_memex_header


def update_memex(db: SykeDB, user_id: str, new_content: str) -> str:
    """Update the canonical memex while preserving its identity."""
    canonical_content = strip_memex_header(new_content)
    now = datetime.now(UTC).isoformat()

    with db.transaction():
        existing = db.conn.execute(
            """SELECT id, content
               FROM current_memex
               WHERE singleton = 1 AND user_id = ?""",
            (user_id,),
        ).fetchone()
        if existing is not None:
            if existing["content"] != canonical_content:
                db.conn.execute(
                    """UPDATE current_memex
                       SET content = ?, updated_at = ?
                       WHERE singleton = 1 AND user_id = ? AND id = ?""",
                    (canonical_content, now, user_id, existing["id"]),
                )
            return str(existing["id"])

        memex_id = str(uuid7())
        db.conn.execute(
            """INSERT INTO current_memex
               (singleton, id, user_id, content, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?, NULL)""",
            (memex_id, user_id, canonical_content, now),
        )
        return memex_id


def get_memex_for_injection(
    db: SykeDB,
    user_id: str,
    *,
    context: str = "ask",
) -> str:
    """Get memex content formatted for system prompt injection.

    Returns the memex content if it exists, or a minimal fallback
    with memory stats so the agent knows what's available.

    `context` controls the empty-memex fallback:
      - "ask" (default): user-facing placeholder explaining first-run state
      - "synthesis": returns empty string so the agent builds from scratch
        without echoing the placeholder into its output.
    """
    memex = db.get_memex(user_id)
    content = ""

    if memex:
        content = memex["content"]
    else:
        # Synthesis context never wants user-facing placeholder text.
        # The placeholder is an ask-path UX affordance — in synthesis it
        # leaks into the prompt and the agent literally echoes it instead
        # of doing its work. Callers pass context="synthesis" to opt out.
        if context == "synthesis":
            return ""
        mem_count = db.count_memories(user_id)
        if mem_count > 0:
            return (
                f"[No memex yet. {mem_count} memories are available in Syke's canonical database.]"
            )
        return (
            "[First run — no memories yet.]\n\n"
            "Synthesis hasn't completed its first cycle. You can still help:\n"
            "- Read adapter markdowns in `adapters/` to discover what harness data exists.\n"
            "- Explore harness directories directly — the data is there, "
            "the memex just hasn't mapped it yet.\n"
            "- `syke record` sends new evidence to the next synthesis; it is not "
            "memory yet.\n"
            "- Do not guess a wait time. Tell the user setup is complete but "
            "MEMEX is not ready yet; they can run `syke sync`, check "
            "`syke status --json`, or keep working while the daemon builds it."
        )

    return content
