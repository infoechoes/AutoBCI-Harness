# Mobile Gateway Setup

Updated: 2026-06-17

This guide covers only the AutoBCI research-reporting path: a phone or chat
gateway sends a small set of whitelisted requests to the local AutoBCI CLI, and
AutoBCI replies with status, reports, and audit pointers.

AutoBCI does not implement WeChat protocol logic. Hermes, OpenClaw, ClawBot,
Telegram, Feishu, Webhook, or a custom gateway can be used as transport. The
research source of truth stays local: Program, ledger, events, reports, and
artifacts.

## 1. Check Local AutoBCI

Run these in the repository root:

```bash
autobci doctor --json
autobci status --json
autobci model list --json
autobci model current --agent intake --json
```

Acceptance:

- `doctor.ok` is `true`.
- `status` returns the real task, Program, dashboard, ledger, and artifact
  paths.
- Provider failures are visible. Missing keys, incompatible models, or network
  errors must not fall back to fake local responses.

## 2. Configure A Model Provider

MiniMax China example:

```bash
autobci model key minimax-cn
autobci model set --agent intake --provider minimax-cn --model MiniMax-M3
autobci model test minimax-cn --model MiniMax-M3 --json
autobci smoke intake-llm --provider minimax-cn --model MiniMax-M3 --json
```

Supported provider presets:

| Provider | Protocol | Key |
| --- | --- | --- |
| `minimax-cn` | Anthropic Messages compatible | `MINIMAX_CN_API_KEY` |
| `minimax` | Anthropic Messages compatible | `MINIMAX_API_KEY` |
| `deepseek` | OpenAI Chat Completions compatible | `DEEPSEEK_API_KEY` |
| `glm` / `zhipu` | OpenAI Chat Completions compatible | `ZAI_API_KEY` |
| `qwen` / `dashscope` | OpenAI Chat Completions compatible | `DASHSCOPE_API_KEY` |
| `xiaomi` / `mimo` | pi-ai runtime | `XIAOMI_API_KEY` |
| `openai` | pi-ai runtime | `OPENAI_API_KEY` |

The model names above are examples. If a provider changes its model catalog,
configure a currently available model and rerun `model test` plus the intake
smoke.

## 3. Connect WeChat Or Another Gateway

For Hermes / WeChat, run the gateway setup in a terminal:

```bash
hermes gateway setup
```

Choose the Weixin / WeChat channel in the wizard. If it prints a QR code or
login URL, scan it with the phone WeChat account that should talk to the bot.
The QR code appears in the terminal running `hermes gateway setup`, not inside
AutoBCI.

After pairing:

```bash
hermes gateway restart
hermes gateway status
hermes send --list weixin
```

If WeChat replies with a pairing code such as:

```text
I don't recognize you yet!
Here's your pairing code: XXXXXX
Ask the bot owner to run:
hermes pairing approve weixin XXXXXX
```

run exactly that approval command on the computer.

## 4. Disable Reasoning Leakage

Phone replies must not include chain-of-thought, internal language-selection
notes, or raw reasoning. For Hermes:

```bash
hermes config set display.show_reasoning false
hermes config set display.platforms.weixin.show_reasoning false
hermes gateway restart
hermes config show
```

Acceptance:

- The config shows reasoning display as off.
- A phone-side test reply contains only the user-facing answer.
- It does not include `Reasoning`, internal prompts, or text such as "I need to
  use Chinese".

## 5. Route Messages To Headless AutoBCI CLI

Do not open the retired AutoBCI TUI or the old `/remote` current-session bridge.
Map gateway messages to CLI commands:

```text
AutoBCI status  -> autobci status --json
AutoBCI ask     -> autobci ask "<original message>" --json
AutoBCI digest  -> autobci-agent research-loop status --json
AutoBCI demo    -> autobci demo onsite --skip-smoke --json
```

For the common "what is happening now?" request:

```bash
autobci ask "现在进展如何？" --json
```

By default, `autobci ask` does not call a live model. It uses deterministic
routing for status, dashboard, report, and help style requests. If you want the
configured intake model to interpret a more complex request, opt in:

```bash
autobci ask "帮我整理当前开放问题" --use-model-agent --json
```

## 6. Recommended Onboarding Prompt For Agents

When a user opens this repository in Claude Code, Codex, Cursor, Workbody, or
another agent, ask the agent to run:

```text
Read README.md, AGENTS.md, DEMO_QUICKSTART.md, and .agents/skills/autobci-harness/SKILL.md.
Then run:

autobci doctor --json
autobci status --json
autobci model list --json
autobci ask "现在进展如何？" --json

If any command fails, report the exact failure and do not invent a fallback.
If provider keys are missing, ask me which provider I want to configure:
minimax-cn, minimax, deepseek, glm/zhipu, qwen/dashscope, xiaomi/mimo, or openai.
Do not ask me to open a TUI.
```

## 7. Security Boundary

The phone gateway is an observation and authorization surface, not a remote
desktop.

Allowed by default:

- status queries
- report summaries
- dashboard links
- current research-loop state
- dataset path configuration when the user provides an explicit local path

Require explicit confirmation:

- pause, resume, stop, archive, fork, rollback
- any command that changes Program, data split, primary metric, or runner

Never map phone text directly to arbitrary shell execution.

## 8. Phone-Side Acceptance Test

Send from the paired phone:

```text
AutoBCI status
```

or:

```text
现在进展如何？
```

The reply should include:

- current task or Program
- current stage or research-loop phase
- current track or recent direction
- whether the result is a smoke result, candidate, fixed-evaluator result, or
  only a status summary
- report / ledger / dashboard paths
- a boundary note that Program, ledger, events, and artifacts are the audit
  source of truth

The reply must not include reasoning text or internal prompts.
