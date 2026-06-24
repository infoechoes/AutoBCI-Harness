# Research Forest and Task Isolation

Updated: 2026-06-17

AutoBCI should help a research loop remember, but memory must not become
contamination. The default model is a **Research Forest**, not one universal
Research Tree shared by every task.

## Core Rule

A frozen **Program** is the smallest isolation boundary.

If any of these change, start a new Program or write an explicit Program
amendment before continuing:

- research objective or success condition
- dataset directory or data source
- data split, alignment rule, label definition, or target definition
- primary metric or promotion threshold
- runner, evaluator, editable file scope, or forbidden actions
- storage budget, execution sandbox, or external worker boundary

Do not treat two runs as comparable just because they live in the same Git
repository. They are comparable only when their Program contracts say so.

## Terms

| Term | Meaning |
| --- | --- |
| **Workspace** | A local checkout or deployment containing one or more projects. |
| **Project** | A human grouping, such as a lab, benchmark, paper, customer, or product effort. |
| **Program** | The frozen research contract: objective, data boundary, metric, split, runner, allowed edits, and forbidden actions. |
| **Run** | One execution attempt under one Program. It owns command traces, diffs, stdout/stderr, metrics, and artifacts. |
| **Research Tree** | The local reasoning and event projection for one active Program/control surface. |
| **Research Forest** | A collection of Program-scoped Research Trees plus an index that keeps them separate. |
| **Promoted Pattern** | A reviewed lesson that may be reused across Programs because its source evidence, scope, counterexamples, and confidence are explicit. |

## Recommended Layout

The public harness currently exposes a narrow control surface. When connecting a
real runner, use Program-scoped paths like this instead of writing every task
into one generic directory:

```text
programs/
  <program_id>/
    program.json
    program.md

artifacts/
  research_forest/
    index.json
  research_loop/
    <program_id>/
      research_tree.jsonl
      events.jsonl
      ledger.jsonl
      promoted_patterns/
        <pattern_id>.md
      runs/
        <run_id>/
          command.txt
          stdout.txt
          stderr.txt
          diff.patch
          metrics.json
          result.json
          artifacts/
```

The existing public facade may also report:

```text
.autobci/research_control.json
.autobci/research_control_events.jsonl
artifacts/research_loop/<task_id>/events.jsonl
```

Those files are useful status surfaces. For real multi-task deployments,
`<task_id>` should map to the active Program ID. Do not reuse the default
generic task ID for unrelated Programs.

## What Stays Isolated

These records are Program/run evidence and should not be read as global memory
by another task:

- raw command lines and shell output
- code diffs and worktree paths
- data split decisions and alignment assumptions
- target definitions, feature definitions, and label mappings
- metrics, checkpoints, score traces, and generated artifacts
- failed runs, blocked runs, and lucky high scores
- runner-specific workaround notes

Another Program may inspect these only as cited evidence during an explicit
review. It must not silently inherit them.

## What Can Be Shared

Cross-Program learning should happen through promoted patterns, not raw run
history. A promoted pattern should be small and falsifiable:

```yaml
pattern_id: strict-causal-normalization-check
title: Normalize using train-window statistics only
source_programs:
  - gait_phase_binary_v0
source_runs:
  - run-2026-06-17-001
scope:
  applies_to:
    - time_series
    - strict_causal_decoding
  not_valid_for:
    - random_split_leaderboards
evidence: "A prior run showed inflated metrics when normalization saw the full sequence."
counterexamples:
  - "Offline descriptive plots may use full-session statistics if marked non-training."
confidence: medium
owner_decision: promoted_for_future_program_review
```

The owner or an explicit promotion rule must approve a pattern before another
Program can use it as context.

## What Users Can Decide

Users can decide:

- which Programs belong to the same Project
- which reports or dashboards should be shown together
- which promoted patterns are visible to a new Program
- which runner, evaluator, and storage budget a Program uses

Users should not casually decide:

- to merge different primary metrics into one leaderboard
- to compare scores across different data splits without a benchmark contract
- to let one Program auto-read another Program's checkpoints or score traces
- to promote a pattern without evidence and scope
- to let a mobile gateway or dashboard become a second source of truth

## Dashboard and Gateway Behavior

Dashboard, Hermes, OpenClaw, WeChat, and webhook gateways are observation or
transport surfaces. They can show a Project-level forest index, but the links
must still point back to Program-scoped ledgers, events, and runs.

If a dashboard shows multiple Programs together, it should label every metric
with:

- Program ID
- run ID
- primary metric
- data split
- result status: smoke, candidate, selected, or verified

## Publish-Time Claim Boundary

It is accurate to say:

> AutoBCI defines a Research Forest isolation contract: each frozen Program owns
> its Research Tree, run evidence, ledger, metrics, and artifacts; cross-task
> reuse happens only through explicit promoted patterns.

Do not claim that every external runner automatically enforces this unless that
runner has been configured and tested against Program-scoped paths.
