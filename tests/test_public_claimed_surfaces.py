from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _payload(output: str) -> dict[str, object]:
    parsed = json.loads(output)
    assert isinstance(parsed, dict)
    return parsed


def test_readme_model_commands_return_structured_json(capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    assert main(["model", "list", "--json"]) == 0
    provider_list = _payload(capsys.readouterr().out)
    providers = provider_list.get("providers")
    assert isinstance(providers, list)
    provider_names = {str(item.get("name")) for item in providers if isinstance(item, dict)}
    assert {"minimax-cn", "deepseek", "glm", "qwen"} <= provider_names

    assert main(["model", "current", "--agent", "intake", "--json"]) == 0
    current = _payload(capsys.readouterr().out)
    assert current["agent"] == "intake"
    assert {"provider", "model", "live"} <= set(current)


def test_readme_data_commands_configure_local_dataset_without_copying(tmp_path: Path, capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    repo_root = tmp_path / "repo"
    dataset_root = tmp_path / "dataset"
    repo_root.mkdir()
    dataset_root.mkdir()

    assert main(["--repo-root", str(repo_root), "data", "set", str(dataset_root)]) == 0
    set_output = capsys.readouterr().out
    assert "已保存本地数据目录" in set_output
    assert str(dataset_root.resolve()) in set_output
    assert not (repo_root / "data" / "raw").exists()

    assert main(["--repo-root", str(repo_root), "data", "show"]) == 0
    show_output = capsys.readouterr().out
    assert "dataset_root" in show_output
    assert str(dataset_root.resolve()) in show_output

    assert main(["--repo-root", str(repo_root), "data", "clear"]) == 0
    assert "已清除本地数据目录配置" in capsys.readouterr().out


def test_readme_dashboard_and_onsite_demo_surfaces_are_headless(tmp_path: Path, capsys, monkeypatch) -> None:
    import bci_autoresearch.product_shell.cli as cli

    monkeypatch.setenv("AUTOBCI_TUI_TEST_MODE", "1")
    monkeypatch.setattr(
        cli,
        "build_doctor_report",
        lambda **_: {"ok": True, "provider_config": {"ok": True}, "ui": {"mode": "headless"}},
    )
    monkeypatch.setattr(
        cli,
        "run_dashboard_command",
        lambda *, host, port, task_id=None, **_: f"dashboard test-mode dry-run：http://{host}:{port}/",
    )

    assert cli.main(["--repo-root", str(tmp_path), "--port", "8899", "dashboard"]) == 0
    dashboard_output = capsys.readouterr().out
    assert "dashboard test-mode dry-run" in dashboard_output

    assert cli.main(["--repo-root", str(tmp_path), "--port", "8899", "demo", "onsite", "--skip-smoke", "--json"]) == 0
    demo = _payload(capsys.readouterr().out)
    assert demo["ok"] is True
    assert demo["smoke"] == {"ok": None, "skipped": True}
    steps = {str(item["step_id"]): item for item in demo["steps"] if isinstance(item, dict)}
    assert steps["doctor"]["status"] == "done"
    assert steps["dashboard"]["status"] == "done"
    assert steps["intake_smoke"]["status"] == "skipped"


def test_dashboard_api_status_includes_mobile_gateway_safe_surfaces() -> None:
    repo = Path(__file__).resolve().parents[1]
    module_path = repo / "scripts" / "serve_dashboard.py"
    spec = importlib.util.spec_from_file_location("autobci_public_dashboard_server", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    payload = module.build_dashboard_status()
    assert payload["ok"] is True
    assert {"status", "research_control", "research_loop", "server"} <= set(payload)
    assert payload["server"]["repo_root"] == str(repo.resolve())
    assert "execution_status" in payload["research_loop"]


def test_autobci_agent_public_facades_fail_visibly_without_user_runner(tmp_path: Path, capsys) -> None:
    from bci_autoresearch.control_plane.cli import main

    assert main(["research-loop", "status", "--repo-root", str(tmp_path), "--json"]) == 0
    loop_status = _payload(capsys.readouterr().out)
    assert loop_status["execution_status"] == "blocked_until_user_runner_configured"
    assert "runner" in str(loop_status["blocked_reason"]).lower()

    assert main(["director-plan", "--repo-root", str(tmp_path), "--web", "off", "--json"]) == 0
    plan = _payload(capsys.readouterr().out)
    assert plan["source_state_status"] == "bootstrap_missing_state"
    assert plan["task"]["target_mode"] == "generic_bci_decoding"
    assert len(plan["tracks"]) >= 10
