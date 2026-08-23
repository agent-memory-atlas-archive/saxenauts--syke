# codex

Codex stores conversations as JSONL rollouts and thread metadata in SQLite. Sessions can fork or spawn subagent threads.

## Where

```
~/.codex/**/*.jsonl
~/.codex/**/*.db
~/.codex/**/*.sqlite
~/.codex/memories/**/*.md
~/.codex/config.toml
```

Session data lives under two directory names within `~/.codex`:

- `sessions/` -- active session rollout files (JSONL)
- `archived_sessions/` -- archived session rollout files (JSONL)

Additional data sources:

- `session_index.jsonl` -- index file mapping session IDs to metadata
- `state.sqlite` or `state_N.sqlite` (regex: `^state(?:_\d+)?\.sqlite$`) -- SQLite database with thread metadata
- `config.toml` -- may contain a `sqlite_home` key pointing to an alternate SQLite directory
- The `CODEX_SQLITE_HOME` environment variable can override the SQLite location

Also check `~/.codex/sqlite/` as a default SQLite home.

## Sessions

One JSONL file equals one session (a "rollout"). Its filename stem ends with the session UUID.

Sessions can have parent-child relationships:

- `forked_from_id` in session metadata indicates a fork
- `source.subagent.thread_spawn.parent_thread_id` indicates a subagent

The `session_index.jsonl` file provides a secondary index with fields `id`, `thread_name`, `updated_at`.

The SQLite state database `threads` table provides richer metadata per session.

## Format

Mixed: JSONL for conversation data, SQLite for metadata, TOML for configuration.

### JSONL rollout files

Each line is a JSON object. Records use a wrapper structure:

```
{"type": "<wrapper_type>", "payload": {...}, "timestamp": "..."}
```

If no `payload` key exists, the record itself may serve as the payload. Records with `record_type == "state"` are skipped.

A record with a top-level `id` and `timestamp` but no `type`/`payload` wrapper is treated as `session_meta`.

### Message types / Turn structure

Payload `type` values that produce turns:

**Content messages** (`message`, `reasoning`):
- `role`: `"user"` or `"assistant"`
- Text content from `payload.text` or from `payload.content[]` blocks of type `input_text`, `output_text`, `summary_text`, `text`
- `reasoning` type messages are prefixed with `[reasoning]`

**Tool call types** (`function_call`, `custom_tool_call`, `web_search_call`, `computer_call`):
- `name` or type name used as tool name
- `call_id` as tool ID (fallback: `{session_id}:tool:{line_index}`)
- `arguments` or `input` as tool input
- `status` field if present

**Tool result types** (`function_call_output`, `custom_tool_call_output`, `web_search_call_output`, `computer_call_output`):
- `call_id` to match back to tool call
- `output` contains result content
- `status` field; non-success statuses (`completed`, `succeeded`, `success`) mark errors
- Output may be JSON string with `metadata.exit_code`

### What to ignore

- Records with `record_type == "state"`
- `session_meta` records (used for metadata extraction only, not conversation turns)
- Records where the unwrap produces no recognizable type
- Empty text content

### SQLite state database (`threads` table)

The `threads` table schema (columns may vary by version):

| Column | Description |
|---|---|
| `id` | Session/thread ID |
| `rollout_path` | Filesystem path to the JSONL rollout file |
| `created_at` | Creation timestamp |
| `updated_at` | Last update timestamp |
| `source` | Session source descriptor |
| `agent_nickname` | Agent display name |
| `agent_role` | Agent role |
| `agent_path` | Agent configuration path |
| `model_provider` | Model provider name |
| `model` | Model identifier |
| `reasoning_effort` | Reasoning effort setting |
| `cwd` | Working directory |
| `cli_version` | CLI version string |
| `title` | Session title |
| `sandbox_policy` | Sandbox policy |
| `approval_mode` | Approval mode |
| `tokens_used` | Total tokens used (integer) |
| `first_user_message` | First user message text |
| `archived_at` | Archive timestamp |
| `git_sha` | Git commit SHA |
| `git_branch` | Git branch |
| `git_origin_url` | Git remote origin URL |

Query example:
```sql
SELECT id, rollout_path, cwd, model, title, created_at, updated_at
FROM threads
ORDER BY updated_at DESC
LIMIT 20;
```

For current work, prefer `updated_at` over `created_at`; both are epoch seconds in the observed schema. After selecting a thread, open its `rollout_path`: the thread row identifies the session but does not contain the conversation.

### Metadata

Per-session metadata from the session meta record and state DB:

- `cli_version`, `originator`, `model_provider` from session meta
- `base_instructions`, `git` (object), `forked_from_id` from session meta
- `thread_name`, `indexed_updated_at` from session index
- All `threads` table columns listed above from state DB
- `source` object containing subagent spawn information

## Project instructions

Codex loads `~/.codex/AGENTS.md` and applicable `AGENTS.md` files from the project hierarchy. `AGENTS.override.md` at the project or user level takes priority.

Rollouts may serialize injected `<INSTRUCTIONS>` and `<environment_context>` blocks as `role: "user"` messages. They are harness context, not the person's request. Embedded blocks describe that session; current files describe the project now.

## Harness memory

When the Codex `memories` feature is enabled, retained context lives under `~/.codex/memories/`:

- `MEMORY.md` groups durable task and project understanding.
- `memory_summary.md` is a shorter index over retained topics.
- `rollout_summaries/*.md` contains source-session summaries referenced by the indexes.

These files are Codex-generated memory, not native session evidence. Follow their rollout paths or thread IDs when the underlying conversation matters.

## Distribution

To write context back to Codex:

- Write to `AGENTS.md` in the project root (markdown format, injected into system prompt)
- Write to `AGENTS.override.md` in the project root or `~/.codex/` to override AGENTS.md
- Edit `~/.codex/config.toml` for global configuration
- Edit `.codex/config.toml` for project-level config override
