from __future__ import annotations

import json
from pathlib import Path


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_storage_optimizer_reports_duplicates_and_compressible_text(tmp_path: Path) -> None:
    from bci_autoresearch.storage_optimizer import build_storage_optimization_report

    duplicate_payload = b"same-large-payload" * 32
    text_payload = (b'{"event":"status","message":"progress"}\n' * 64)
    _write(tmp_path / "artifacts" / "run-a" / "model.bin", duplicate_payload)
    _write(tmp_path / "output" / "run-b" / "model-copy.bin", duplicate_payload)
    _write(tmp_path / "artifacts" / "events.jsonl", text_payload)
    _write(tmp_path / "tmp" / "notes.md", b"# small note\n")

    report = build_storage_optimization_report(
        tmp_path,
        min_duplicate_bytes=1,
        min_compressible_bytes=1,
    )

    assert report["ok"] is True
    assert report["summary"]["duplicate_groups"] == 1
    assert report["summary"]["duplicate_waste_bytes"] == len(duplicate_payload)
    assert report["summary"]["compressible_files"] >= 1
    assert report["summary"]["compressible_candidate_bytes"] >= len(text_payload)
    assert report["duplicate_groups"][0]["copies"] == 2
    assert any(item["path"].endswith("events.jsonl") for item in report["compressible_files"])


def test_storage_audit_cli_outputs_json_report(tmp_path: Path, capsys) -> None:
    from bci_autoresearch.product_shell.cli import main

    _write(tmp_path / "artifacts" / "events.jsonl", b'{"event":"status"}\n' * 64)

    assert main(["--repo-root", str(tmp_path), "storage", "audit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["repo_root"] == str(tmp_path.resolve())
    assert payload["summary"]["scanned_roots"] >= 1
    assert payload["recommendations"]
