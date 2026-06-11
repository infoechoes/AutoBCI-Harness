# AutoBCI onsite demo quickstart

Date: 2026-06-10

This is the shortest local handoff path for a controlled AutoBCI demo. It shows
AutoBCI as an auditable research-loop harness: runtime check, live model intake,
Dashboard, task stream, and result replay.

## One command

From the repo root:

```bash
autobci demo onsite
```

This will:

1. run the local doctor check,
2. read the current control-plane status,
3. open the Dashboard at `http://127.0.0.1:8878/?task=rsvp_ship_image_only_v0`,
4. run a live intake smoke with the currently configured model,
5. write live task progress to `artifacts/monitor/demo_task_stream.json`.

The Dashboard refreshes from `/api/status` and shows the live task stream in the
current execution panel.

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
autobci dashboard --task rsvp
```

## Boundary to say out loud

The first-screen demo task is `rsvp_ship_image_only_v0`, a pure image-only
classification harness example. It is useful for showing Program, queue,
runner, ledger, and Dashboard flow. It is not a BCI result and should not be
described as cross-modal decoding.

BCI/eCOG work should be discussed as historical research context unless a
separate frozen BCI Program is selected and run.
