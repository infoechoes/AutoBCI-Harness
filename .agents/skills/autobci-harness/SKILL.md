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
4. Run `autobci ask "现在进展如何？" --json` when the user asks a natural-language status question.
5. Prefer JSON outputs when available.

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
autobci doctor --json
autobci status --json
autobci ask "现在进展如何？" --json
autobci data set /absolute/path/to/dataset
autobci model list --json
autobci model current --agent intake --json
autobci dashboard
autobci demo onsite --skip-smoke --json
```

If a command lacks `--json`, read the artifact it writes or summarize the terminal output faithfully.

Do not ask the user to open a TUI. AutoBCI is driven through headless CLI/JSON
from the existing coding agent or from a mobile gateway such as Hermes / WeChat
/ ClawBot.

## Boundaries

Never bypass AutoBCI's research boundaries:

- do not edit frozen Program state directly
- do not change primary metric silently
- do not change data split silently
- do not modify raw data
- do not download datasets, enable profiler traces, or write unbounded artifacts by default
- respect storage budgets such as `AUTOBCI_MAX_DATASET_BYTES` and `AUTOBCI_MAX_ARTIFACT_BYTES`
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
