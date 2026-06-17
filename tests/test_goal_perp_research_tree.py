from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def _payload(output: str) -> dict[str, object]:
    parsed = json.loads(output)
    assert isinstance(parsed, dict)
    return parsed


def test_goal_perp_and_research_tree_cli_roundtrip(tmp_path: Path, capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    root_args = ["--repo-root", str(tmp_path)]

    assert main([*root_args, "goal", "start", "验证一个严格因果 BCI baseline", "--success", "status JSON 可检查", "--json"]) == 0
    goal_started = _payload(capsys.readouterr().out)
    goal = goal_started["goal"]
    assert isinstance(goal, dict)
    assert goal["status"] == "active"
    assert "BCI baseline" in str(goal["objective"])

    assert main([*root_args, "perp", "start", "持续观察 BCI 研究循环", "--json"]) == 0
    perp_started = _payload(capsys.readouterr().out)
    perp = perp_started["perp"]
    assert isinstance(perp, dict)
    assert perp["status"] == "active"
    assert "autobci status --json" in json.dumps(perp, ensure_ascii=False)

    assert main([*root_args, "research-tree", "show", "--json"]) == 0
    tree = _payload(capsys.readouterr().out)
    assert tree["kind"] == "research_tree"
    assert len(tree["nodes"]) >= 3
    assert len(tree["edges"]) >= 2

    assert main([*root_args, "status", "--json"]) == 0
    status = _payload(capsys.readouterr().out)
    control = status["research_control"]
    assert isinstance(control, dict)
    assert isinstance(control["goal"], dict)
    assert isinstance(control["perp"], dict)

    assert main([*root_args, "goal", "complete", "--evidence", "测试已检查 JSON surface", "--json"]) == 0
    completed = _payload(capsys.readouterr().out)
    completed_goal = completed["goal"]
    assert isinstance(completed_goal, dict)
    assert completed_goal["status"] == "complete"


def test_public_source_has_no_retired_task_terms() -> None:
    repo = Path(__file__).resolve().parents[1]
    listed = subprocess.run(["git", "ls-files"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    needle_one = "r" + "svp"
    needle_two = "sh" + "ip"
    retired = (
        re.compile(rf"\b{needle_one}\b", re.IGNORECASE),
        re.compile(rf"\b{needle_two}\b", re.IGNORECASE),
        re.compile(rf"\bnot[-_ ]?{needle_two}\b", re.IGNORECASE),
        re.compile(rf"\bnon[-_ ]?{needle_two}\b", re.IGNORECASE),
        re.compile("跨" + "模态"),
    )
    allowed = {
        "tests/test_goal_perp_research_tree.py",
        "tests/test_headless_cli.py",
    }
    offenders: list[str] = []
    for relative in listed.stdout.splitlines():
        if relative in allowed:
            continue
        path = repo / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in retired):
            offenders.append(relative)
    assert offenders == []
