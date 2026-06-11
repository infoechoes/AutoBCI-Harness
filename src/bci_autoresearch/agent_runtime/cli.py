from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .runtime import run_edit_turn, run_json_task


def _read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bci_autoresearch.agent_runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("json-task", "edit-turn"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--input", required=True)
        cmd.add_argument("--output", required=True)
        cmd.add_argument("--provider", default=None)
        cmd.add_argument("--model", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = _read_json(args.input)
    if args.provider:
        payload["provider"] = args.provider
    if args.model:
        payload["model"] = args.model
    if args.command == "json-task":
        result = run_json_task(payload)
    elif args.command == "edit-turn":
        result = run_edit_turn(payload)
    else:  # pragma: no cover - argparse prevents this.
        raise ValueError(args.command)
    _write_json(args.output, result)
    return 0
