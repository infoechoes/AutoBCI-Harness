---
name: autobci-harness
description: Use AutoBCI as a local 24/7 research operator from an existing coding agent. Use when drafting, running, inspecting, or summarizing bounded AutoBCI research loops.
---

# AutoBCI Harness Skill

You are using AutoBCI as a 24/7 research operator, not as a replacement for your coding agent.
Do not describe it as the UFPA AutoBCI MI-BCI project or as Andrej Karpathy's
`autoresearch` project.

## First Checks

1. Read `AGENTS.md`.
2. Read `docs/research_forest.md` before discussing task isolation, Research Tree reuse, or run artifacts.
3. Run `autobci doctor --json` when setup or runtime health matters.
4. Run `autobci status --json` before making claims about current state.
5. Run `autobci ask "现在进展如何？" --json` when the user asks a natural-language status question.
6. Prefer JSON outputs when available.

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
- Program-scoped Research Tree / Research Forest boundaries
- dashboard projection
- archive, resume, fork, and rollback evidence

Treat Codex, Claude Code, Cursor, or local runners as possible workers. Treat
Hermes, OpenClaw, WeChat, Feishu, Telegram, or webhooks as gateways for status
and approval, not as sources of research truth.

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
- do not mix Research Tree nodes, run traces, metrics, checkpoints, or artifacts across frozen Programs
- do not promote a cross-Program pattern unless it records source Programs, source runs, scope, counterexamples, and confidence
- do not claim progress from one lucky run
- do not summarize from chat memory when ledger or report artifacts exist

If the task boundary needs to change, draft an amendment or ask the user to confirm the change.

## Result Reporting

When summarizing a result, state:

- what command ran
- what artifact proves it
- which Program and run own the artifact
- whether it is the selected result, a candidate, or only a smoke result
- what risks remain
- the next action
