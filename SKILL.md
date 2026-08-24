---
name: syke
description: "Local-first cross-harness memory for agents. Syke observes activity across supported harnesses, keeps a current memex in context, and gives agents `syke ask`, `syke memex`, and `syke record` for continuity across sessions."
version: 0.6.0
author: saxenauts
license: AGPL-3.0-only
metadata:
  hermes:
    tags: [Memory, Context, Identity, Cross-Platform, Agentic-Memory]
    related_skills: []
    requires_toolsets: [terminal]
  requires:
    bins: ["syke"]
  install:
    - id: pipx
      kind: pipx
      package: syke
      bins: ["syke"]
      label: "Install Syke (pipx)"
---

# Syke

Read the user's memex before doing anything else. It is the current map of what is active, what changed, and where deeper evidence lives.

Canonical memex path: `~/.syke/workspace/MEMEX.md`

## When to Use

- **`syke ask`**: deeper timeline and evidence-backed queries
- **`syke memex`**: fastest read of the current memex
- **`syke record`**: send an external observation to a later synthesis
- **`syke status`**: quick operational snapshot
- **`syke doctor`**: deeper diagnostic when setup or runtime looks wrong

## Quick Reference

| Command | Use | Exit 0 | Exit 1 |
|---------|-----|--------|--------|
| `syke ask "question"` | Deep memory query | Answer on stdout | Error on stderr, stdout empty |
| `syke memex` | Current memex | Memex on stdout | Error message |
| `syke record "text"` | Send observation | Admission confirmation | Error message |
| `syke status` | Runtime snapshot | Status on stdout | Error message |
| `syke doctor` | Health check | All OK | Issues found |

For long or shell-sensitive records, pipe stdin:

```bash
printf '%s\n' 'Decision: keep $(literal) and `quoted` chars.' | syke record
```

## Procedure

1. Read the memex already in context or call `syke memex`.
   If you need the file directly, start with `~/.syke/workspace/MEMEX.md`.
2. Use `syke ask` when the memex is not enough.
3. Use `syke record` after useful work so ordinary synthesis can consider it.
4. Use `syke status` for a quick state check.
5. Use `syke doctor` when setup or runtime looks wrong.

## Pitfalls

- If `syke ask` fails, do not treat stderr as the answer. Fall back to `syke memex`.
- Thinking, progress, and tool notices streamed while `syke ask` runs are interim output, not the answer.
- If your command runner yields a still-running session or process ID, retain that exact ID and poll or resume it until the child command exits. Completion of an outer orchestration cell, or a fixed yield such as 30 seconds, does not mean `syke ask` completed.
- Interpret the result only after the process exits: exit 0 means the final stdout contains the answer; a nonzero exit means failure. `syke ask --json` suppresses progress ambiguity, but it still requires waiting for process completion.
- If external tool infrastructure kills `syke ask`, say that the ask was incomplete and use `syke memex` as a degraded fallback, not as a replacement for the deeper answer.
- Some sandboxes can read the memex but cannot open the live store. In those cases, use `syke memex` or the injected memex there, and run `syke ask` from a trusted host shell if needed.
- If the memex is empty, Syke may not be set up yet or synthesis may not have produced a useful memex.
- `syke record` does not directly create a memory or force a synthesis. It admits another external record for the next ordinary synthesis cycle.
- The background loop can lag behind the newest event. `syke ask` can still search the underlying timeline.

## Verification

- After `syke ask`, check the exit code. Exit 0 means answer on stdout. Exit 1 means failure on stderr.
- After `syke record`, exit 0 means the record was admitted to protected history. It does not mean synthesis turned it into memory.
- After setup, `syke doctor` confirms health.

## Setup & Onboarding

If Syke is unavailable, install it with `pipx install syke` or
`uv tool install syke`. Syke requires Python 3.12+ and Node.js 22.19 or newer.

- Humans run `syke setup` and follow the interactive prompts.
- Agents run `syke setup --agent`, parse its JSON, and follow the returned
  `status` and `next_steps` instead of inventing a setup sequence.
- For `needs_provider`, present `provider_choices` and run the selected
  `auth_options` command. Pi opens the browser or shows a device code.
  Never request credentials in chat or print them.
- For `complete`, stop setup work. Do not loop on setup.
- Use `--skip-daemon` when background operation is not intended, then run
  `syke sync` explicitly.
- Verify the result with `syke doctor`.

## Provider Commands

| Command | What It Does |
|---------|-------------|
| `syke auth status` | Show selected provider, auth source, model, and endpoint |
| `syke auth use <name>` | Switch active provider |
| `syke auth set <name> --api-key <KEY> --use` | Store credentials and make this the active provider |
| `syke config show` | Show effective config |

Provider resolution: CLI `--provider` flag > `SYKE_PROVIDER` env > Pi `defaultProvider` in `~/.syke/pi-agent/settings.json`.
