---
name: self-learn
description: Use when current work, self-observation, feedback, pressure, success, failure, surprise, or existing learned language gives Syke a reason to examine and improve how its memory system works. Syke may use current context or inspect other evidence, revise the exact `syke-learned` memory for later Ask and wake cycles, or make no change.
---

# Self Learn

Use this skill inside the current Ask or synthesis operation. It is not another
mode or operation, and it is not a required detour. Syke decides whether
anything is worth understanding and whether any durable change follows.

## Purpose

Self-learning should improve how Syke retains, organizes, connects, retrieves,
revises, and presents memory. It may improve the memory itself, the process
used to maintain it, or the language that guides later operations.

Self-awareness describes how Syke works and what it can inspect or change.
Self-observation presents a bounded current view of its condition, pressure,
recent work, and routes to deeper evidence. They provide bearings, not a
trigger or conclusion.

## Follow The Question

Start from whatever made the current situation worth examining. Current work
may be enough. When more evidence is useful, follow the routes that fit the
question: current graph memories, native Pi sessions, receipts, runtime files,
external harness traces, repository history, or other computer evidence.

Use the ordinary tools and the language descriptions already available. There
is no required search order, recency window, failure taxonomy, trace parser, or
minimum amount of evidence. Read progressively and let evidence change the
original question. A slow but correct result, repeated effort, a missing route,
an unexpectedly useful approach, or a change in the kind of memory needed can
all matter. So can deciding that no learning is warranted.

Sessions preserve available messages, reasoning, tool actions, and outcomes.
They may be partial and do not guarantee a complete explanation of why
something happened. Treat them as evidence to interpret, not automatic truth.

## Start From The Current Operation

When reflection is warranted, start with the current `# Self-observation` and
`# Operation` blocks already supplied to this operation. Use their host facts
and evidence routes before beginning a broad search.

Use the facts available there, such as:

- operation and reference time;
- status and duration when recorded;
- receipts, recovery, and native-session paths that route to relevant tool actions;
- newly admitted evidence and explicit source-change limits; and
- relevant graph, MEMEX, workspace, or runtime pressure.

Treat these as mechanical observations, not failure categories, scores, health
verdicts, or proof that a change helped. Open the exact receipt, native session,
graph memory, source, or runtime evidence when the question requires it. Do not
copy a whole transcript into learned language or create a second reflection
store merely to retain this view.

## Current Learned Memory

The only language selected into later prompts by this first loop is the memory
whose exact ID is `syke-learned` in the current `syke.db` named by
self-observation.

- It is one current free-language memory, not a file, log, schema, or history.
- Revise the same row in place. Do not append a chronology of changes.
- Missing or empty content means no learned language is active.
- Its complete `# Learned` prompt section has a hard 1,000-token
  `o200k_base` limit.
- If the prompt reports that it is inactive and over budget, inspect the exact
  row and consolidate it before relying on it.
- Keep durable operating language. Leave episode detail and evidence in their
  native sessions, receipts, graph memories, and source locations.
- When earlier context matters, search protected native sessions for the exact
  memory ID, distinctive current wording, or related tool calls. This is a route
  to available evidence, not a separate change log or guaranteed provenance.

Use `sqlite3` through `bash` and the graph contract in self-observation to read
or revise the exact memory. Preserve its ID and `created_at`. If it does not
exist and durable language is justified, create it as an ordinary memory for
the bound Syke identity. Do not add a memory type, table, tool, or second store.

## What A Change Means

Learned language is the smallest durable change to future behavior that is not
already expressed in the active self-model, self-observation, MEMEX, operation,
or existing learned language. Treat the current invocation as the comparison
surface. Do not summarize or repeat its instructions, state, evidence, or
episode details. Prefer one precise sentence when it is sufficient. If no
distinct guidance remains, make no change or remove redundant learned language.

Prefer language that can guide more than the immediate case without pretending
to be universal. Keep scope and uncertainty when they matter. Consolidate or
remove language that later experience contradicts, makes redundant, or shows
to be unhelpful.

A stored change is not proof of improvement. Later operations still have to
activate it, follow it when relevant, produce a different effect, and show that
the effect was useful. Later evidence may reinforce, narrow, revise, or empty
the memory.

A committed valid learned row is kept when synthesis restores other graph state,
including recovery from an interrupted cycle. Survival proves only that the
language was committed and remained within its prompt budget; it does not prove
that reflection was complete, deliberate, or beneficial.

Do not modify the installed Syke prompt, skill, adapters, tools, scripts, or
code through this loop. Those are separate future authority decisions.
