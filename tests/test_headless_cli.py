from __future__ import annotations

import json


def test_provider_presets_prioritize_china_friendly_options() -> None:
    from bci_autoresearch.product_shell.cli import MODEL_PROVIDER_ORDER, _sort_provider_rows
    from bci_autoresearch.providers.presets import get_provider_preset

    assert MODEL_PROVIDER_ORDER[:6] == ("minimax-cn", "minimax", "xiaomi", "deepseek", "glm", "qwen")
    rows = _sort_provider_rows([{"name": "openai"}, {"name": "deepseek"}, {"name": "minimax-cn"}])
    assert [item["name"] for item in rows] == ["minimax-cn", "deepseek", "openai"]
    assert get_provider_preset("minimax-cn").protocol == "anthropic_compatible"
    assert get_provider_preset("minimaxi").name == "minimax-cn"
    assert get_provider_preset("deepseek").protocol == "openai_compatible"
    assert get_provider_preset("zhipu").name == "glm"
    assert get_provider_preset("dashscope").name == "qwen"


def test_main_without_subcommand_prints_headless_help(capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "headless" in output
    assert "autobci status --json" in output
    assert "TUI remote bridge" in output


def test_doctor_json_reports_headless_ui(capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ui"]["ok"] is True
    assert payload["ui"]["mode"] == "headless"
    assert "textual" not in payload
    assert "prompt_toolkit" not in payload


def test_ask_cli_runs_single_headless_turn(capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    assert main(["ask", "现在进展如何？", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["quit"] is False
    assert "查看当前研究态" in payload["message"]
