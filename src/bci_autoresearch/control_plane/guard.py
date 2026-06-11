from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROTECTED_PROGRAM_KEYS = {"research_goal", "split_policy", "metrics", "task_type", "primary_metric"}
NEEDS_APPROVAL_ACTIONS = {"install_dependency", "add_model_family", "modify_eval_script", "internet_search"}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_path(path: str | Path | None) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def _path_is_in_scope(path: str, allowed_change_scope: list[str]) -> bool:
    normalized_scopes = [scope.replace("\\", "/").strip().strip("/") for scope in allowed_change_scope if scope.strip()]
    return any(path == scope or path.startswith(f"{scope}/") for scope in normalized_scopes)


def _protected_change_requested(requested_change: dict[str, Any]) -> bool:
    for key, value in requested_change.items():
        if key in PROTECTED_PROGRAM_KEYS:
            return True
        if key == "research_goal" and isinstance(value, dict) and "task_type" in value:
            return True
        if key == "metrics" and isinstance(value, dict) and "primary" in value:
            return True
    return False


def evaluate_guard_action(
    *,
    action_type: str,
    path: str | Path | None,
    program: dict[str, Any],
    run_id: str,
    allowed_change_scope: list[str] | None = None,
    requested_change: dict[str, Any] | None = None,
    requester_role: str = "director_executor",
) -> dict[str, Any]:
    normalized_path = _normalize_path(path)
    scopes = list(allowed_change_scope or [])
    decision = "allow"
    reason = "action is inside current Program policy"
    policy_refs = ["Program.forbidden_actions"]

    if normalized_path.startswith("data/raw/"):
        decision = "deny"
        reason = "raw data is read-only"
    elif requester_role == "judge" and "scratchpad" in normalized_path:
        decision = "deny"
        reason = "Judge cannot read Director-Executor scratchpad"
    elif action_type in NEEDS_APPROVAL_ACTIONS:
        decision = "needs_approval"
        reason = f"{action_type} requires human approval"
    elif action_type == "modify_program" and str(program.get("status") or "") == "frozen":
        if _protected_change_requested(dict(requested_change or {})):
            decision = "deny"
            reason = "frozen Program protected fields require amendment, not direct modification"
    elif action_type == "write_file" and scopes and not _path_is_in_scope(normalized_path, scopes):
        decision = "needs_approval"
        reason = "write path is outside allowed_change_scope"

    return {
        "message_type": "policy_decision",
        "created_at": utcnow(),
        "run_id": run_id,
        "action_type": action_type,
        "path": normalized_path,
        "requester_role": requester_role,
        "decision": decision,
        "reason": reason,
        "policy_refs": policy_refs,
    }
