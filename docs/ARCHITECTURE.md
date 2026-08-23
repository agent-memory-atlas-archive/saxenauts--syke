# Syke Architecture

Syke is a local-first memory agent for AI tools. It reads activity from
supported local harnesses, maintains a compact memory graph and MEMEX, and
returns that context through a CLI and agent skills.

## Data Flow

```text
local harness activity
        |
        | read in place using source guides
        v
background synthesis ---------> local memory graph
        |                              |
        |                              v
        +--------------------------> MEMEX.md
                                       |
                              CLI and agent skills
```

Harness artifacts remain in the applications that created them. Syke does not
copy them into a second event warehouse. Source guides describe where each
supported harness stores its history and how that evidence should be read.

A synthesis cycle inspects relevant evidence, updates Syke's local memory, and
refreshes the MEMEX. `syke ask` uses the same memory and available evidence to
answer a question without waiting for the next background cycle. `syke record`
admits an external observation for a later synthesis; it does not directly
create a memory.

## Local Ownership

The active installation lives under `~/.syke/`:

```text
~/.syke/
├── workspace/
│   ├── syke.db          current memory graph
│   ├── MEMEX.md         current human- and agent-readable projection
│   └── adapters/        source-reading guides
├── control/             operational history and recovery state
├── pi-agent/            provider and model configuration
└── bin/                 managed runtime launchers
```

The graph and MEMEX are local files owned by the user. Native harness history
also stays local unless the configured model needs content from it to perform
an Ask or synthesis operation. Content supplied to a model is subject to that
provider's data handling policy.

## Memory Model

Syke stores current memories as free-form text in SQLite. Memories can be
connected by sparse links with natural-language reasons. SQLite FTS provides
text search; Syke does not require a vector database or a separate graph
service.

The MEMEX is a bounded map over that state. It provides fast orientation—what
matters now and where deeper context lives—without trying to duplicate every
memory or source transcript.

## Runtime

Syke uses Pi as its model runtime. Pi supplies provider integrations and the
agent execution loop; Syke supplies the memory context, local tools, source
guides, persistence rules, and host-side validation.

Foreground and background work have different obligations:

- `syke ask` owes the caller an answer now.
- Background synthesis maintains memory and refreshes the MEMEX.
- `syke record` stores an observation for later consideration without invoking
  the model immediately.

The daemon schedules synthesis and serves local Ask IPC and the loopback
visualizer. macOS uses launchd, Linux uses a user systemd service, and other
environments can run the daemon in the foreground.

## Safety Boundary

Syke separates mutable learned state from operational evidence and recovery
state. The model can update the workspace during synthesis, while the trusted
host validates the resulting graph and restores the previous graph if the
candidate is invalid or interrupted.

On macOS, model-invoked file tools run inside a deny-default Seatbelt profile.
Ordinary files under the user's home are readable but not writable; writes are
limited to Syke's workspace and runtime area. Provider access and native session
persistence remain host-controlled. Linux does not yet provide an equivalent
OS-enforced model-tool sandbox.

Source selection controls which harnesses synthesis treats as active evidence.
It is not a general filesystem permission system.

## Distribution

Syke returns memory through three surfaces:

1. CLI commands such as `syke memex`, `syke ask`, and `syke record`
2. the local `MEMEX.md` projection
3. installed skill/capability files for supported agent harnesses

Those files are outputs, not additional memory authorities. The SQLite graph
and current MEMEX remain the local source of truth.

## Source Support

Adding a harness requires a concrete local artifact contract and a packaged
source guide. See [PLATFORMS.md](../PLATFORMS.md) for the current support matrix.

## Current Boundaries

- Syke is experimental and pre-1.0.
- Schema-v3 is the current graph format; older public databases are not migrated
  automatically.
- Pi is the only model runtime.
- The local visualizer is read-only and bound to loopback.
- macOS has the strongest model-tool filesystem boundary today.

See [Setup](SETUP.md), [Providers](PROVIDERS.md), and the
[Config Reference](CONFIG_REFERENCE.md) for operational details.
