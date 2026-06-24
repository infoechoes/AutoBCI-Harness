# AutoBCI onsite demo quickstart

Date: 2026-06-10

This is the shortest local handoff path for a controlled AutoBCI Harness smoke
demo. It shows AutoBCI as a 24/7 research operator for coding agents: runtime
check, live model intake, Dashboard, task stream, and result replay.

## One command

From the repo root:

```bash
autobci demo onsite
```

This will:

1. run the local doctor check,
2. read the current control-plane status,
3. open the Dashboard at `http://127.0.0.1:8878/`,
4. run a live intake smoke with the currently configured model,
5. write live task progress to `artifacts/monitor/demo_task_stream.json`.

The Dashboard refreshes from `/api/status` and shows the live task stream in the
current execution panel.

This demo does not download data. Runtime evidence is written under ignored
local `artifacts/` paths, and runner adapters must enforce storage-budget guards
for dataset and artifact directories. See `docs/storage_budget.md` before
pointing the harness at a large local dataset.

## If they have OpenAI API access

This path needs an OpenAI API key. A ChatGPT Plus web subscription alone is not
the same thing as API access.

Save the key locally:

```bash
autobci model key openai --api-key "$OPENAI_API_KEY"
```

Then run the demo against GPT:

```bash
autobci demo onsite --provider openai --model gpt-5.5
```

If the key or model is not available, the smoke should fail visibly. Do not use
a silent local fallback for this demo.

## Useful checks

```bash
autobci status --json
autobci doctor --json
autobci dashboard
```

## Boundary to say out loud

The public smoke path proves the control plane, not a specific scientific
result. BCI/eCOG work is only one origin domain and should be discussed only
after a concrete frozen Program, dataset boundary, runner, and fixed evaluator
are selected locally.
