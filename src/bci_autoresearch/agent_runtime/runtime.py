from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from bci_autoresearch.providers import generate_json_task

from .ledger import append_provider_trace, append_runtime_event
from .safety import check_runtime_safety


DEFAULT_PROPOSAL: dict[str, Any] = {
    "hypothesis": "No proposal was generated because the configured provider did not return a usable response.",
    "why_this_change": "Runtime failures must be visible instead of being replaced by a local substitute.",
    "changes_summary": "Provider call failed; inspect provider configuration, model name, API key, and runtime logs.",
    "change_bucket": "runtime_provider",
    "track_comparison_note": "No AutoResearch track comparison is claimed by this provider/runtime turn.",
    "files_touched": [],
    "next_step": "Run PYTHONPATH=src pytest -q tests/test_providers_runtime.py.",
    "search_queries": [],
    "research_evidence": [],
}


def resolve_repo_root(repo_root: str | Path | None = None) -> Path:
    return Path(repo_root or os.environ.get("AUTOBCI_ROOT") or Path(__file__).resolve().parents[3]).expanduser().resolve()


def _session_id(payload: dict[str, Any]) -> str:
    return str(payload.get("sessionId") or payload.get("session_id") or uuid.uuid4().hex[:16])


def _provider_from(payload: dict[str, Any]) -> str | None:
    value = payload.get("provider")
    return str(value).strip().lower() if value else None


def run_json_task(payload: dict[str, Any], *, repo_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_repo_root(repo_root)
    session_id = _session_id(payload)
    provider = _provider_from(payload)
    result = generate_json_task(payload, provider_name=provider)
    append_provider_trace(
        root,
        session_id,
        {
            "provider": result.get("provider") or provider,
            "model": result.get("model"),
            "ok": bool(result.get("ok")),
            "error_code": result.get("error_code"),
        },
    )
    append_runtime_event(
        root,
        session_id,
        "json_task",
        {
            "provider": result.get("provider") or provider,
            "model": result.get("model"),
            "ok": bool(result.get("ok")),
            "error_code": result.get("error_code"),
        },
    )
    if result.get("ok"):
        return {
            "ok": True,
            "sessionId": session_id,
            "provider": result["provider"],
            "model": result["model"],
            "json": result["response"],
        }
    return {"ok": False, "sessionId": session_id, **result}


def _proposal_from_json(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = payload.get("proposal")
    if not isinstance(candidate, dict):
        candidate = {}
    proposal = dict(DEFAULT_PROPOSAL)
    for key in proposal:
        if key in candidate:
            proposal[key] = candidate[key]
    proposal["files_touched"] = list(proposal.get("files_touched") or [])
    proposal["search_queries"] = list(proposal.get("search_queries") or [])
    proposal["research_evidence"] = list(proposal.get("research_evidence") or [])
    safety = check_runtime_safety(proposal)
    proposal["safety"] = safety
    return proposal


def run_edit_turn(payload: dict[str, Any], *, repo_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_repo_root(repo_root)
    session_id = _session_id(payload)
    thread_id = str(payload.get("threadId") or payload.get("thread_id") or session_id)
    provider = _provider_from(payload)
    task = {
        "provider": provider,
        "model": payload.get("model"),
        "prompt": payload.get("message") or payload.get("prompt") or "Draft an AutoBci runtime edit proposal as JSON.",
    }
    result = generate_json_task(task, provider_name=provider)
    response_json = result.get("response") if isinstance(result.get("response"), dict) else {}
    proposal = _proposal_from_json(response_json if isinstance(response_json, dict) else {})
    summary = proposal["changes_summary"] if result.get("ok") else str(result.get("message") or result.get("error_code") or proposal["changes_summary"])
    item = {
        "type": "proposal",
        "provider": result.get("provider"),
        "model": result.get("model"),
        "ok": bool(result.get("ok")),
        "summary": summary,
    }
    append_provider_trace(
        root,
        session_id,
        {
            "provider": result.get("provider") or provider,
            "model": result.get("model"),
            "ok": bool(result.get("ok")),
            "error_code": result.get("error_code"),
        },
    )
    append_runtime_event(root, session_id, "edit_turn", {"threadId": thread_id, **item})
    return {"threadId": thread_id, "proposal": proposal, "items": [item]}
