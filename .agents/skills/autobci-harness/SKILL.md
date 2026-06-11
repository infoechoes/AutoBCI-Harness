---
name: autobci-harness
description: Use AutoBCI as a local research harness from an existing coding agent. Use when drafting, running, inspecting, or summarizing AutoBCI research loops.
---

# AutoBCI Harness Skill

You are using AutoBCI as a harness, not as a replacement for your coding agent.

## First Checks

1. Read `AGENTS.md`.
2. Run `autobci doctor --json` when setup or runtime health matters.
3. Run `autobci status --json` before making claims about current state.
4. Prefer JSON outputs when available.

## Working Model

Use your coding-agent abilities for:

- reading files
- editing code
- running commands
- comparing diffs
- explaining changes

Use AutoBCI for:

- Program draft and freeze
- task boundary checks
- fixed evaluation
- ledger and report artifacts
- dashboard projection
- archive, resume, fork, and rollback evidence

## Program Behavior

When the user gives a partial task, do not run a long questionnaire first.

Draft a Program with explicit assumptions:

- known task goal
- known data source
- known label or target definition
- primary metric
- default split or official split assumption
- unknowns that still need confirmation
- forbidden actions

Ask only for missing information that would make the run unsafe, impossible, or misleading.

## Commands

Use these as the stable interface:

```bash
autobci status --json
autobci doctor --json
autobci plan
autobci run
autobci report latest
autobci dashboard
```

If a command lacks `--json`, read the artifact it writes or summarize the terminal output faithfully.

## Boundaries

Never bypass AutoBCI's research boundaries:

- do not edit frozen Program state directly
- do not change primary metric silently
- do not change data split silently
- do not modify raw data
- do not claim progress from one lucky run
- do not summarize from chat memory when ledger or report artifacts exist

If the task boundary needs to change, draft an amendment or ask the user to confirm the change.

## Result Reporting

When summarizing a result, state:

- what command ran
- what artifact proves it
- whether it is the selected result, a candidate, or only a smoke result
- what risks remain
- the next action
