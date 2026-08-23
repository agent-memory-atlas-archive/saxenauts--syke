# opencode

OpenCode stores sessions, messages, and content parts in SQLite. Sessions can have parent-child relationships for subagent tasks.

## Where

```
~/.local/share/opencode/opencode*.db
```

Inspect databases matching `opencode*.db`.

## Sessions

Multiple sessions live in a single SQLite database. Each session is a row in the `session` table. Sessions are identified by their `id` column.

Parent-child relationships are tracked via the `parent_id` column. Subagent sessions have a non-null `parent_id`. Agent identity is inferred from the session title (pattern: `(@agent_name subagent)`) or from the `agent` field in message data.

Recency is determined by `time_updated`, `time_created`, and `time_archived` on the session row, plus `time_created`/`time_updated` on associated `message` and `part` rows (all stored as millisecond epoch integers).

## Format

SQLite. The database contains three primary tables for conversation data.

### Table: `session`

| Column | Type | Description |
|---|---|---|
| `id` | text | Session identifier |
| `project_id` | text | Foreign key to `project` table |
| `parent_id` | text | Parent session ID (null for root sessions) |
| `slug` | text | URL-friendly session slug |
| `directory` | text | Working directory |
| `title` | text | Session title |
| `version` | text | Opencode version |
| `share_url` | text | Share URL if shared |
| `summary_additions` | integer | Lines added |
| `summary_deletions` | integer | Lines deleted |
| `summary_files` | integer | Files changed |
| `summary_diffs` | text | JSON blob of diff summaries |
| `revert` | text | JSON blob of revert info |
| `permission` | text | JSON blob of permission config |
| `time_created` | integer | Creation time (ms epoch) |
| `time_updated` | integer | Last update time (ms epoch) |
| `time_compacting` | integer | Compaction start time (ms epoch) |
| `time_archived` | integer | Archive time (ms epoch) |
| `workspace_id` | text | Foreign key to `workspace` table |

### Table: `message`

| Column | Type | Description |
|---|---|---|
| `id` | text | Message identifier |
| `session_id` | text | Foreign key to `session` |
| `time_created` | integer | Creation time (ms epoch) |
| `time_updated` | integer | Last update time (ms epoch) |
| `data` | text | JSON blob containing message payload |

The `data` JSON contains:

| Field | Description |
|---|---|
| `role` | `"user"` or `"assistant"` |
| `parentID` | Parent message ID (for tree-structured conversations) |
| `modelID` | Model identifier |
| `providerID` | Provider identifier |
| `model` | Object with `modelID` and `providerID` |
| `tokens` | Object: `{input, output, reasoning, total, cache: {read, write}}` |
| `cost` | Numeric cost value |
| `mode` | Operating mode |
| `agent` | Agent identifier (for subagent sessions) |
| `finish` | Finish reason |
| `path` | Path object |
| `summary` | Summary object |
| `tools` | Tools object |
| `error` | Error object |
| `time` | Object with `created` timestamp |

### Table: `part`

| Column | Type | Description |
|---|---|---|
| `id` | text | Part identifier |
| `message_id` | text | Foreign key to `message` |
| `session_id` | text | Foreign key to `session` |
| `time_created` | integer | Creation time (ms epoch) |
| `time_updated` | integer | Last update time (ms epoch) |
| `data` | text | JSON blob containing part payload |

Parts are the atomic content units within a message. The `data` JSON contains a `type` field:

| Part type | Fields | Description |
|---|---|---|
| `text` | `text` | Plain text content |
| `reasoning` | `text` | Reasoning/thinking content (prefixed with `[reasoning]`) |
| `tool` | `tool`, `callID`, `state` | Tool invocation. `state.input` has input, `state.output` has output, `state.status` is `"completed"` or `"error"`, `state.error` has error text |
| `patch` | `hash`, `files` | Code patch applied. `files` is a list of affected paths |
| `file` | `filename`, `mime`, `source` | File attachment |
| `compaction` | `auto` | Context compaction marker. Skip for conversation reconstruction |
| `step-finish` | `reason` | Step completion marker |

### Joined tables

Related context is stored in:

- `project` table: `worktree`, `vcs`, `name`, `commands`, `time_created`, `time_updated`
- `workspace` table: `type`, `name`, `directory`, `extra`
- `session_share` table: `id`, `secret`, `url`

### Message types / Turn structure

Messages are ordered by `time_created ASC, id ASC`. Parts within each message are ordered the same way.

User turns: messages with `role == "user"`. Content is assembled from `text` parts.

Assistant turns: messages with `role == "assistant"`. Content is assembled from `text` parts, `reasoning` parts (prefixed), and tool parts produce tool_use/tool_result blocks.

### What to ignore

- `compaction` parts (context compaction artifacts)
- `step-finish` parts (metadata only, used for finish reason)
- Messages with no content and no tool calls after part assembly
- Messages with roles other than `user` or `assistant`

### Metadata

Per message: `modelID`, `providerID`, `tokens` (usage breakdown), `cost`, `mode`, `agent`, `finish` reason, `error`.

Per session: `title`, `slug`, `version`, `directory`, project info, workspace info, share info, summary stats (additions, deletions, files).

Query example to list recent sessions:
```sql
SELECT s.id, s.title, s.directory, s.time_created, s.time_updated,
       p.name AS project_name, p.worktree
FROM session s
LEFT JOIN project p ON p.id = s.project_id
ORDER BY s.time_updated DESC
LIMIT 20;
```

Use `time_updated` for active work; these timestamps are epoch milliseconds. After selecting a session, read its `message` and `part` rows: the session row is metadata. Follow `parent_id` when delegated work matters.

## Project instructions

OpenCode uses `AGENTS.md` in the project and `~/.config/opencode/AGENTS.md` for global instructions. The `/init` command can generate a project `AGENTS.md`. Project configuration is in `.opencode/config.json`; global configuration is in `~/.config/opencode/opencode.json`.

Resolve the project through `session.directory`, `project.worktree`, and `workspace.directory` before opening applicable instructions. Current files do not prove what an older session received; use its messages and tool reads for historical context.

## Harness memory

No separate harness-owned durable memory surface is present in the supported local layout. `AGENTS.md` is persistent context; summaries, compaction parts, and conversation rows are session history.

## Distribution

To write context back to OpenCode:

- Write to `AGENTS.md` in the project root (markdown format, project instructions)
- Write to `~/.config/opencode/AGENTS.md` for global context across all projects
