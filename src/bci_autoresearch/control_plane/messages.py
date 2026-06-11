from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime_store import append_jsonl, read_jsonl


class ControlMessageError(ValueError):
    pass


MESSAGE_REQUIRED_FIELDS = {
    "program_handoff": {"version", "program_snapshot_path", "frozen_at", "allowed_actions", "forbidden_actions"},
    "amendment_request": {"requested_by", "reason", "requested_change", "evidence", "risk"},
    "judge_request": {"program_snapshot_path", "result_artifacts", "logs", "metrics", "guard_decisions"},
    "policy_decision": {"action_type", "decision", "reason", "policy_refs"},
    "external_result_submission": {"submitter", "result_artifacts", "metrics"},
    "judge_report": {"verdict", "recommended_next_action"},
}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def build_control_message(
    *,
    message_type: str,
    source_role: str,
    target_role: str,
    program_id: str,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if message_type not in MESSAGE_REQUIRED_FIELDS:
        raise ControlMessageError(f"未知 message_type：{message_type}")
    if not _as_text(program_id):
        raise ControlMessageError("program_id 不能为空")
    if not _as_text(run_id):
        raise ControlMessageError("run_id 不能为空")
    if message_type == "judge_request" and "director_scratchpad" in payload:
        raise ControlMessageError("judge_request 不能包含 director_scratchpad")
    missing = sorted(field for field in MESSAGE_REQUIRED_FIELDS[message_type] if field not in payload)
    if missing:
        raise ControlMessageError(f"{message_type} 缺少字段：{', '.join(missing)}")
    return {
        "message_id": f"msg-{uuid.uuid4().hex}",
        "message_type": message_type,
        "created_at": utcnow(),
        "source_role": source_role,
        "target_role": target_role,
        "program_id": program_id,
        "run_id": run_id,
        **dict(payload),
    }


def append_control_message(path: Path, message: dict[str, Any]) -> None:
    append_jsonl(path, message)


def read_recent_messages(path: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    if limit <= 0:
        return []
    return list(reversed(rows[-limit:]))
