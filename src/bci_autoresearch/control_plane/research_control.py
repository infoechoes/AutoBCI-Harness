from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime_store import append_jsonl, read_json, read_jsonl, write_json_atomic


CONTROL_SCHEMA_VERSION = "autobci_research_control_v1"
STATE_RELATIVE_PATH = Path(".autobci") / "research_control.json"
EVENTS_RELATIVE_PATH = Path(".autobci") / "research_control_events.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_path(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve() / STATE_RELATIVE_PATH


def _events_path(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve() / EVENTS_RELATIVE_PATH


def _new_id(prefix: str, text: str) -> str:
    seed = f"{prefix}:{text}:{_utc_now()}".encode("utf-8")
    return f"{prefix}-{hashlib.sha1(seed).hexdigest()[:12]}"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def load_research_control_state(repo_root: str | Path) -> dict[str, Any]:
    payload = read_json(_state_path(repo_root), {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", CONTROL_SCHEMA_VERSION)
    payload.setdefault("goal", {})
    payload.setdefault("perp", {})
    payload.setdefault("updated_at", "")
    return payload


def save_research_control_state(repo_root: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    payload = dict(state)
    payload["schema_version"] = CONTROL_SCHEMA_VERSION
    payload["updated_at"] = _utc_now()
    write_json_atomic(_state_path(repo_root), payload)
    return payload


def append_research_control_event(
    repo_root: str | Path,
    *,
    event_type: str,
    actor: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "event_id": _new_id("event", f"{event_type}:{summary}"),
        "event_type": str(event_type),
        "actor": str(actor),
        "summary": str(summary),
        "payload": payload or {},
        "recorded_at": _utc_now(),
    }
    append_jsonl(_events_path(repo_root), event)
    return event


def list_research_control_events(repo_root: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = read_jsonl(_events_path(repo_root))
    return rows[-max(0, int(limit)) :]


def start_goal(
    repo_root: str | Path,
    *,
    objective: str,
    success_check: str = "",
    constraints: list[str] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    text = str(objective or "").strip()
    if not text:
        raise ValueError("goal objective 不能为空。")
    state = load_research_control_state(repo_root)
    existing = state.get("goal") if isinstance(state.get("goal"), dict) else {}
    if existing.get("status") == "active" and not replace:
        raise RuntimeError("已有 active goal。请先 complete/clear，或加 --replace。")
    now = _utc_now()
    goal = {
        "goal_id": _new_id("goal", text),
        "status": "active",
        "objective": text,
        "success_check": str(success_check or "").strip(),
        "constraints": _as_list(constraints),
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "evidence": "",
    }
    state["goal"] = goal
    save_research_control_state(repo_root, state)
    append_research_control_event(
        repo_root,
        event_type="goal_started",
        actor="owner",
        summary=text,
        payload={"goal_id": goal["goal_id"], "success_check": goal["success_check"]},
    )
    return {"ok": True, "goal": goal, "state_path": str(_state_path(repo_root))}


def complete_goal(repo_root: str | Path, *, evidence: str) -> dict[str, Any]:
    evidence_text = str(evidence or "").strip()
    if not evidence_text:
        raise ValueError("complete goal 必须提供 evidence。")
    state = load_research_control_state(repo_root)
    goal = state.get("goal") if isinstance(state.get("goal"), dict) else {}
    if goal.get("status") != "active":
        raise RuntimeError("当前没有 active goal。")
    now = _utc_now()
    goal = dict(goal)
    goal.update({"status": "complete", "completed_at": now, "updated_at": now, "evidence": evidence_text})
    state["goal"] = goal
    save_research_control_state(repo_root, state)
    append_research_control_event(
        repo_root,
        event_type="goal_completed",
        actor="owner",
        summary=evidence_text,
        payload={"goal_id": goal.get("goal_id"), "objective": goal.get("objective")},
    )
    return {"ok": True, "goal": goal}


def clear_goal(repo_root: str | Path) -> dict[str, Any]:
    state = load_research_control_state(repo_root)
    previous = state.get("goal") if isinstance(state.get("goal"), dict) else {}
    state["goal"] = {}
    save_research_control_state(repo_root, state)
    append_research_control_event(
        repo_root,
        event_type="goal_cleared",
        actor="owner",
        summary=str(previous.get("objective") or "goal cleared"),
        payload={"previous_goal_id": previous.get("goal_id")},
    )
    return {"ok": True, "previous_goal": previous}


def start_perp(
    repo_root: str | Path,
    *,
    objective: str,
    cadence: str = "owner_or_gateway_tick",
    scope: str = "local_harness",
    replace: bool = False,
) -> dict[str, Any]:
    text = str(objective or "").strip()
    if not text:
        raise ValueError("perp objective 不能为空。")
    state = load_research_control_state(repo_root)
    existing = state.get("perp") if isinstance(state.get("perp"), dict) else {}
    if existing.get("status") == "active" and not replace:
        raise RuntimeError("已有 active perp。请先 stop，或加 --replace。")
    now = _utc_now()
    perp = {
        "perp_id": _new_id("perp", text),
        "status": "active",
        "objective": text,
        "cadence": str(cadence or "owner_or_gateway_tick").strip(),
        "scope": str(scope or "local_harness").strip(),
        "created_at": now,
        "updated_at": now,
        "stopped_at": "",
        "execution_model": "headless_cli_state_surface",
        "allowed_remote_commands": [
            "autobci status --json",
            "autobci ask \"现在进展如何？\" --json",
            "autobci research-tree show --json",
            "autobci goal status --json",
            "autobci perp status --json",
        ],
    }
    state["perp"] = perp
    save_research_control_state(repo_root, state)
    append_research_control_event(
        repo_root,
        event_type="perp_started",
        actor="owner",
        summary=text,
        payload={"perp_id": perp["perp_id"], "cadence": perp["cadence"], "scope": perp["scope"]},
    )
    return {"ok": True, "perp": perp, "state_path": str(_state_path(repo_root))}


def stop_perp(repo_root: str | Path, *, reason: str = "") -> dict[str, Any]:
    state = load_research_control_state(repo_root)
    perp = state.get("perp") if isinstance(state.get("perp"), dict) else {}
    if not perp:
        return {"ok": True, "perp": {}, "status": "not_started"}
    now = _utc_now()
    perp = dict(perp)
    perp.update({"status": "stopped", "stopped_at": now, "updated_at": now, "stop_reason": str(reason or "").strip()})
    state["perp"] = perp
    save_research_control_state(repo_root, state)
    append_research_control_event(
        repo_root,
        event_type="perp_stopped",
        actor="owner",
        summary=str(reason or "perp stopped"),
        payload={"perp_id": perp.get("perp_id")},
    )
    return {"ok": True, "perp": perp, "status": "stopped"}


def build_research_tree(repo_root: str | Path) -> dict[str, Any]:
    state = load_research_control_state(repo_root)
    events = list_research_control_events(repo_root, limit=200)
    nodes: list[dict[str, Any]] = [
        {
            "id": "root",
            "kind": "research_control_root",
            "title": "AutoBCI Research Control",
            "status": "active",
        }
    ]
    edges: list[dict[str, str]] = []
    goal = state.get("goal") if isinstance(state.get("goal"), dict) else {}
    if goal:
        nodes.append(
            {
                "id": str(goal.get("goal_id") or "goal"),
                "kind": "goal",
                "title": str(goal.get("objective") or "Goal"),
                "status": str(goal.get("status") or ""),
                "updated_at": str(goal.get("updated_at") or ""),
            }
        )
        edges.append({"from": "root", "to": str(goal.get("goal_id") or "goal"), "relation": "scoped_goal"})
    perp = state.get("perp") if isinstance(state.get("perp"), dict) else {}
    if perp:
        nodes.append(
            {
                "id": str(perp.get("perp_id") or "perp"),
                "kind": "perp",
                "title": str(perp.get("objective") or "Perp"),
                "status": str(perp.get("status") or ""),
                "updated_at": str(perp.get("updated_at") or ""),
            }
        )
        edges.append({"from": "root", "to": str(perp.get("perp_id") or "perp"), "relation": "continuous_loop"})
    for index, event in enumerate(events[-25:], start=1):
        event_id = str(event.get("event_id") or f"event-{index}")
        nodes.append(
            {
                "id": event_id,
                "kind": "event",
                "title": str(event.get("summary") or event.get("event_type") or "event"),
                "status": str(event.get("event_type") or ""),
                "updated_at": str(event.get("recorded_at") or ""),
            }
        )
        parent = "root"
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        goal_id = str(payload.get("goal_id") or "")
        perp_id = str(payload.get("perp_id") or "")
        if goal_id:
            parent = goal_id
        elif perp_id:
            parent = perp_id
        edges.append({"from": parent, "to": event_id, "relation": "recorded_event"})
    return {
        "ok": True,
        "kind": "research_tree",
        "schema_version": CONTROL_SCHEMA_VERSION,
        "state_path": str(_state_path(repo_root)),
        "events_path": str(_events_path(repo_root)),
        "goal": goal,
        "perp": perp,
        "nodes": nodes,
        "edges": edges,
        "recent_events": events[-20:],
    }


def build_research_control_snapshot(repo_root: str | Path) -> dict[str, Any]:
    state = load_research_control_state(repo_root)
    events = list_research_control_events(repo_root, limit=20)
    return {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "state_path": str(_state_path(repo_root)),
        "events_path": str(_events_path(repo_root)),
        "goal": state.get("goal") if isinstance(state.get("goal"), dict) else {},
        "perp": state.get("perp") if isinstance(state.get("perp"), dict) else {},
        "research_tree": build_research_tree(repo_root),
        "recent_events": events,
        "updated_at": str(state.get("updated_at") or ""),
    }
