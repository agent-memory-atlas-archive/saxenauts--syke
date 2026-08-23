# claude-code

Claude Code stores project conversations and a separate transcript event stream as JSONL. Sessions can branch and spawn subagents.

## Where

```
~/.claude/projects/**/*.jsonl
~/.claude/projects/*/memory/**/*.md
~/.claude/transcripts/*.jsonl
```

Files under `transcripts/` and `projects/` use different schemas.

## Sessions

One JSONL file equals one session. The filename stem is the session ID.

For project files, the `sessionId` field in each record may override the filename-derived ID. Subagent sessions live under a `subagents/` directory within the parent session's directory. If `subagents` appears in the path, the parent session ID is taken from the directory name above `subagents/`.

Recency is determined by file `st_mtime` and by the latest timestamp found across all records in the file.

## Format

JSONL. Each line is a self-contained JSON object (one record per line).

There are two distinct file families. Use project files when assistant text matters; transcript files do not contain complete assistant responses.

### Transcript files (under `transcripts/`)

Records have a top-level `type` field. Relevant types:

- `"user"` -- user turn. Content is in `content` (string).
- `"tool_use"` -- assistant invoked a tool. Fields: `tool_name`, `tool_input` (object).
- `"tool_result"` -- tool output. Fields: `tool_output` (string or object). May contain `is_error` or `stderr`.

All other record types are ignored.

Each record has a `timestamp` field (ISO 8601 string, or numeric epoch).

Transcript files do not contain assistant text responses directly. The assistant's behavior is reconstructed from the sequence of `tool_use` and `tool_result` records between `user` records.

### Project files (under `projects/`)

Records have a top-level `type` field and a `message` object. Relevant top-level types:

- Records where `message.role == "assistant"` -- assistant turns. The `message.content` is a list of blocks:
  - `{"type": "text", "text": "..."}` -- text content
  - `{"type": "thinking", "thinking": "..."}` -- extended thinking content (prefixed with `[thinking]`)
  - `{"type": "tool_use", "name": "...", "id": "...", "input": {...}}` -- tool invocation
- Records where `message.role == "user"` -- user turns. `message.content` is either a string or a list of blocks:
  - `{"type": "text", "text": "..."}` -- user text
  - `{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}` -- tool result

Records with `type == "queue-operation"` and `type == "last-prompt"` are metadata-only. Skip them for conversation reconstruction.

### What to ignore

- Records where `message` is not a dict (metadata-only lines).
- `queue-operation` and `last-prompt` record types.
- Empty content blocks.
- Records with no recognizable `type` in transcript files (anything other than `user`, `tool_use`, `tool_result`).

### Metadata

Per-record fields available in project files:

| Field | Location | Description |
|---|---|---|
| `timestamp` | top-level | ISO 8601 or epoch |
| `sessionId` | top-level | Session identifier |
| `cwd` | top-level | Working directory at time of record |
| `version` | top-level | Claude Code version string |
| `gitBranch` | top-level | Current git branch |
| `isSidechain` | top-level | Boolean, true if sidechain session |
| `promptId` | top-level | Prompt identifier |
| `agentId` | top-level | Agent identifier (for subagents) |
| `slug` | top-level | Agent slug |
| `permissionMode` | top-level | Permission mode for user turn |
| `userType` | top-level | User type for user turn |
| `entrypoint` | top-level | Entrypoint for user turn |

Per assistant message:

| Field | Location | Description |
|---|---|---|
| `model` | `message.model` | Model identifier |
| `stop_reason` | `message.stop_reason` | Why generation stopped |
| `usage` | `message.usage` | Token usage object (`input_tokens`, `output_tokens`, etc.) |

## Project instructions

After resolving the project from a session's `cwd` and project-store path, treat applicable `AGENTS.md` files as current shared project instructions. Claude Code's native project context also includes:

- `CLAUDE.md` in the project root
- `.claude/CLAUDE.md` in the project root (typically gitignored)
- `~/.claude/CLAUDE.md` (user-level global instructions)
- `.claude/rules/` (project-level rules)
- `.claude/settings.json` and `.claude/settings.local.json` (project settings and local override)

These current files do not prove what an older session received. Use the native session and any recorded file reads for historical context.

## Harness memory

Claude Code keeps project-scoped auto-memory beside its project sessions:

- `~/.claude/projects/<project-key>/memory/MEMORY.md` is the memory index loaded for that project.
- Other Markdown files under the same `memory/` directory hold topic-specific retained context.
- Resolve `<project-key>` from the parent directory of a known native session instead of guessing the path encoding.

Auto-memory is Claude's retained interpretation. Verify material claims against project files or native sessions.

## Distribution

To write context back to Claude Code:

- Write to `CLAUDE.md` in the project root (markdown, project instructions injected into system prompt)
- Write to `.claude/CLAUDE.md` for gitignored project context
- Write to `~/.claude/CLAUDE.md` for global context across all projects
- Edit `.claude/settings.json` or `.claude/settings.local.json` for project configuration
- Write plain markdown files to `.claude/rules/` for project rules
- Write agent definitions to `.claude/agents/` for subagent configuration
- Write slash commands to `.claude/commands/` (project) or `~/.claude/commands/` (user)
- Write skills to `~/.claude/skills/` (personal) or `.claude/skills/` (project), each with a SKILL.md
- Edit `.claude/.mcp.json` to configure MCP servers for the project
