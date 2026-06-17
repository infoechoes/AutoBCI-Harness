from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .research_control import append_research_control_event, build_research_control_snapshot, build_research_tree
from .runtime_store import read_jsonl, write_json_atomic


TASK_ID = "generic_bci_research"
LOOP_ROOT = Path("artifacts/research_loop")
STRUCTURE_SANDBOX_RUNNER_ENV = "AUTOBCI_STRUCTURE_SANDBOX_RUNNER"
STRUCTURE_SANDBOX_TIMEOUT_ENV = "AUTOBCI_STRUCTURE_SANDBOX_TIMEOUT_SECONDS"
TRACE_SCHEMA_VERSION = "research_trace_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def loop_root(repo_root: str | Path, task_id: str = TASK_ID) -> Path:
    return Path(repo_root).expanduser().resolve() / LOOP_ROOT / str(task_id or TASK_ID)


def _events_path(root: Path) -> Path:
    return root / "events.jsonl"


def _status_path(root: Path) -> Path:
    return root / "status.json"


def _event(
    *,
    actor: str,
    event_type: str,
    action: str,
    track: dict[str, Any] | None = None,
    reason: str = "",
    inputs: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
    decision: dict[str, Any] | str | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    track_payload = track if isinstance(track, dict) else {}
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "actor": actor,
        "event_type": event_type,
        "action": action,
        "track_id": str(track_payload.get("track_id") or ""),
        "track": track_payload,
        "reason": reason,
        "inputs": inputs or {},
        "artifact_refs": artifact_refs or [],
        "decision": decision if decision is not None else {},
        "risk_flags": risk_flags or [],
    }


def append_research_trace_event(
    repo_root: str | Path,
    *,
    task_id: str = TASK_ID,
    actor: str,
    event_type: str,
    action: str,
    track: dict[str, Any] | None = None,
    reason: str = "",
    inputs: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
    decision: dict[str, Any] | str | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    event = _event(
        actor=actor,
        event_type=event_type,
        action=action,
        track=track,
        reason=reason,
        inputs=inputs,
        artifact_refs=artifact_refs,
        decision=decision,
        risk_flags=risk_flags,
    )
    from .runtime_store import append_jsonl

    append_jsonl(_events_path(root), event)
    append_research_control_event(
        repo_root,
        event_type=f"research_loop_{event_type}",
        actor=actor,
        summary=reason or action,
        payload={"task_id": task_id, "action": action, "track_id": event.get("track_id")},
    )
    return event


def status_research_loop(repo_root: str | Path, *, task_id: str = TASK_ID) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    events = read_jsonl(_events_path(root))
    control = build_research_control_snapshot(repo_root)
    tree = control.get("research_tree") if isinstance(control.get("research_tree"), dict) else build_research_tree(repo_root)
    goal = control.get("goal") if isinstance(control.get("goal"), dict) else {}
    perp = control.get("perp") if isinstance(control.get("perp"), dict) else {}
    phase = "perp_active" if perp.get("status") == "active" else "goal_active" if goal.get("status") == "active" else "idle"
    return {
        "task_id": task_id,
        "available": True,
        "root": str(root),
        "phase": phase,
        "queue_count": 0,
        "queued_count": 0,
        "ledger_count": len(events),
        "trajectory": [],
        "active_track": {},
        "last_ledger": events[-1] if events else None,
        "recent_events": events[-20:],
        "research_control": control,
        "research_tree": tree,
        "robust_accepted_best": None,
        "stopped": phase == "idle",
        "execution_status": "blocked_until_user_runner_configured",
        "blocked_reason": "Public harness 不绑定具体任务 runner。请先冻结自己的 Program，并配置评估器、runner 和数据目录。",
    }


def preview_research_step(
    repo_root: str | Path,
    *,
    task_id: str = TASK_ID,
    max_repeated_signature: int = 3,
) -> dict[str, Any]:
    payload = {
        "task_id": task_id,
        "status": "blocked",
        "reason": "Public harness 不自带具体任务 runner；请先配置自己的 Program、runner 和固定评估器。",
        "requires_confirmation": False,
        "research_tree": build_research_tree(repo_root),
    }
    append_research_trace_event(
        repo_root,
        task_id=task_id,
        actor="ControlPlane",
        event_type="blocked",
        action="preview_step",
        reason=payload["reason"],
        risk_flags=["runner_not_configured"],
    )
    return payload


def step_research_loop(
    repo_root: str | Path,
    *,
    task_id: str = TASK_ID,
    max_repeated_signature: int = 3,
    only_track_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "task_id": task_id,
        "status": "blocked",
        "reason": "没有配置用户自己的 runner 和固定评估器，不能启动实验。",
        "updated_at": _utc_now(),
        "only_track_id": only_track_id or "",
    }
    root = loop_root(repo_root, task_id)
    write_json_atomic(_status_path(root), {"phase": "blocked", "updated_at": payload["updated_at"], "reason": payload["reason"]})
    append_research_trace_event(
        repo_root,
        task_id=task_id,
        actor="ControlPlane",
        event_type="blocked",
        action="step",
        reason=payload["reason"],
        inputs={"only_track_id": only_track_id or ""},
        risk_flags=["runner_not_configured"],
    )
    return payload


def run_research_loop(repo_root: str | Path, *, task_id: str = TASK_ID, max_steps: int = 1) -> dict[str, Any]:
    step = step_research_loop(repo_root, task_id=task_id)
    return {"task_id": task_id, "steps": [step], "status": status_research_loop(repo_root, task_id=task_id)}


def stop_research_loop(repo_root: str | Path, *, task_id: str = TASK_ID) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    payload = {"phase": "stopped", "stopped": True, "updated_at": _utc_now()}
    write_json_atomic(_status_path(root), payload)
    append_research_trace_event(
        repo_root,
        task_id=task_id,
        actor="ControlPlane",
        event_type="control",
        action="stop",
        reason="Owner stopped the generic research loop facade.",
    )
    return {"task_id": task_id, "status": "stopped", "root": str(root)}


def explain_research_track(repo_root: str | Path, *, task_id: str = TASK_ID, track_id: str) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    events = [item for item in read_jsonl(_events_path(root)) if str(item.get("track_id") or "") == str(track_id)]
    return {
        "task_id": task_id,
        "track_id": track_id,
        "judgment_chain": [],
        "events": events,
        "error": "No task-specific track ledger is configured in the public harness.",
    }
