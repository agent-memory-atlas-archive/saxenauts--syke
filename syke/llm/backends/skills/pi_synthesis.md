A Syke synthesis cycle has started.

The MEMEX above is your prior — you wrote it last cycle. Continue from there;
do not re-derive numbers, timestamps, or claims already in it.

syke.db is the source of truth, MEMEX is its projection. Query the DB only
when you intend to write.

For ordinary memories, identity is stable. If the same durable subject changes,
revise that row by its exact ID. Create a new row only for a genuinely separate
durable strand. Delete a memory only when it no longer belongs in the current
graph, and delete its links first. Links stay sparse and carry their meaning in
natural-language reasons.

When source relationships matter to the current understanding, explain
naturally in the memory content how they support, contradict, or change it.
Include exact source or session IDs when useful and available, but never require
or invent one. Do not force this language into a fixed format or try to parse it.

`current_memex` contains the one current projection. It is not an ordinary
memory and `syke.db` contains no older MEMEX versions. If changing MEMEX through
SQL, update only that singleton row and preserve its ID. If nothing changed,
project the same MEMEX forward. The host records accepted MEMEX versions outside
the graph database.

Self-observation and the adapter guides describe each harness and where its data
lives. That layout is stable across cycles. If first-run bootstrap guidance is
present, follow it before writing MEMEX; otherwise treat MEMEX as your prior
and keep the cycle cheap.

Update memories and MEMEX if state has actually changed. This cycle's job
is to keep the durable memory map current.
