---
name: tui-pty-harness
description: Use when changing or debugging AutoBCI's prompt_toolkit TUI, slash commands, command menus, input behavior, or any terminal interaction that should be verified through the PTY harness instead of manual user testing.
---

1. Read `AGENTS.md` and `README.md`.
2. Add or update PTY tests before changing TUI behavior. Prefer `tests/test_product_shell_tui_pty.py` and `tests/support/tui_harness.py`.
   - Default user-facing TUI is Textual. Legacy prompt_toolkit can be forced with `AUTOBCI_TUI_ENGINE=prompt_toolkit` for regression coverage.
3. Every PTY interaction step should call `assert_no_crash()` unless the process is intentionally exiting.
4. Use `AUTOBCI_TUI_TEST_MODE=1` fixtures only. Do not call real model providers, open real Dashboard windows, start Executor, read Downloads, or touch `data/raw/`.
5. If the change touches provider, Pi runtime, Intake Agent behavior, `/model`, or `/plan`, PTY is not enough. Also run the scenario smoke with a configured real provider:

```bash
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli smoke intake-llm --provider openai --model gpt-5.5 --json
```

Use the currently configured real provider/model if OpenAI is not available. Missing keys, bad model names, incompatible JSON, or runtime errors must fail visibly; do not replace them with a local substitute.
6. When debugging a user-visible TUI scenario, prefer a visible terminal session over hidden background input. Open or reuse a terminal the user can see, start `autobci`, and send the same messages there so the user can watch the TUI response. Keep PTY/pytest for repeatable regression, not as the only manual smoke path.
7. Cover the affected surface:
   - default research-plan flow: fresh `autobci` accepts a research task without `/plan`
   - numbered next actions: `1` confirms plan, `1` freezes Program, `1` generates Director queue after freeze
   - secondary menu priority: `/model`, `/director`, and `/switch` numbering must override generic next actions
   - slash completion prefixes and Up/Down/Enter
   - root command smoke
   - secondary menu numbering and out-of-range selections
   - long paste, multiline insertion, history, and completion-key conflicts
8. Run:

```bash
PYTHONPATH=src pytest -q tests/test_product_shell_tui_pty.py
PYTHONPATH=src pytest -q tests/test_autobci_shell.py tests/test_director_plan.py
```

9. If a failure only appears in raw ANSI output, use the harness failure tail and recent action log before patching.
