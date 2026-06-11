from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_topics_inbox(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, [])
    if isinstance(payload, dict):
        raw_topics = payload.get("topics", [])
    else:
        raw_topics = payload
    topics: list[dict[str, Any]] = []
    if not isinstance(raw_topics, list):
        return topics
    for item in raw_topics:
        if isinstance(item, dict):
            topics.append(item)
    return topics


def write_topics_inbox(path: Path, topics: list[dict[str, Any]]) -> None:
    write_json_atomic(path, topics)


def append_hypothesis_log(path: Path, payload: dict[str, Any]) -> None:
    append_jsonl(path, payload)


def append_judgment_update(path: Path, payload: dict[str, Any]) -> None:
    append_jsonl(path, payload)


def _packet_filename(recorded_at: str | None = None) -> str:
    stamp = (recorded_at or "").strip() or time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    return f"{stamp.replace(':', '-')}.json"


def write_retrieval_packet(directory: Path, payload: dict[str, Any], *, recorded_at: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _packet_filename(recorded_at)
    write_json_atomic(target, payload)
    return target


def write_decision_packet(directory: Path, payload: dict[str, Any], *, recorded_at: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _packet_filename(recorded_at)
    write_json_atomic(target, payload)
    return target


def read_latest_packet(directory: Path) -> dict[str, Any]:
    if not directory.exists():
        return {}
    candidates = sorted(
        [path for path in directory.glob("*.json") if path.is_file()],
        key=lambda path: (path.name, path.stat().st_mtime_ns),
    )
    if not candidates:
        return {}
    return read_json(candidates[-1], {})
