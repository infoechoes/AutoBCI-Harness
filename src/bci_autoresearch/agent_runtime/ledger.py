from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_MARKERS = ("api_key", "authorization", "token", "secret", "password", "bearer")


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def monitor_dir(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve() / "artifacts" / "monitor"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_MARKERS):
                clean[key] = "[redacted]"
            else:
                clean[key] = redact(item)
        return clean
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if value.startswith("sk-") or any(marker in lowered for marker in ("bearer ", "api_key=")):
            return "[redacted]"
    return value


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = redact(payload)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def append_runtime_event(repo_root: str | Path, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
    base = monitor_dir(repo_root)
    record = {"event_type": event_type, "recorded_at": utcnow(), "session_id": session_id, **payload}
    append_jsonl(base / "agent_tool_ledger.jsonl", record)
    append_jsonl(base / "agent_sessions" / f"{session_id}.jsonl", record)


def append_provider_trace(repo_root: str | Path, session_id: str, payload: dict[str, Any]) -> None:
    base = monitor_dir(repo_root)
    append_jsonl(
        base / "provider_trace.jsonl",
        {"event_type": "provider_call", "recorded_at": utcnow(), "session_id": session_id, **payload},
    )
