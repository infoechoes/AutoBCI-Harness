from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from bci_autoresearch.control_plane.paths import (
    AUTOBCI_ROOT_ENV,
    DEFAULT_CACHE_ROOT_ENV,
    AutoBciControlPlanePaths,
)
from bci_autoresearch.control_plane.messages import append_control_message, build_control_message
from bci_autoresearch.control_plane.runtime_store import (
    append_jsonl,
    read_json,
    read_text,
    read_topics_inbox,
    write_json_atomic,
    write_text_atomic,
    write_topics_inbox,
)
from bci_autoresearch.control_plane.programs import build_program_draft_from_request, freeze_program_contract
from bci_autoresearch.platform_support import default_cache_root, detached_process_kwargs


SHELL_SESSION_PREFIX = "autobci-shell"
PROPOSAL_SCOPE_LABEL = "chat_shell_proposal"
AMENDMENT_KIND = "program_amendment_draft"
AUDIT_SCHEMA_VERSION = "autobci_audit_judgment_chain_v1"

STATUS_KEYWORDS = (
    "现在在跑什么",
    "现在在干什么",
    "现在进展",
    "进展如何",
    "研究进展",
    "当前状态",
    "当前研究态",
    "研究态",
    "最近怎么了",
)
REPORT_KEYWORDS = ("最新摘要", "最新报告", "report latest", "最新总结")
DASHBOARD_KEYWORDS = ("dashboard", "看板", "面板", "打开页面", "打开 dashboard", "打开看板")
HELP_KEYWORDS = ("help", "帮助", "你能做什么")
GREETING_KEYWORDS = ("你好", "您好", "hello", "hi", "hey", "嗨")
RUN_SMOKE_KEYWORDS = ("run smoke", "跑一个 smoke", "先跑一个 smoke", "跑个 smoke", "执行 smoke")
PROGRAM_INTAKE_KEYWORDS = (
    "programmd",
    "program md",
    "任务契约",
    "步态二分类",
    "步态脑电二分类",
    "support swing",
    "support / swing",
    "脑电",
    "BCI",
    "bci",
)
BOUNDARY_KEYWORDS = (
    "canonical",
    "canonical program",
    "canonical split",
    "数据划分",
    "随机划分",
    "primary metric",
    "raw data",
    "原始数据",
    "alignment",
    "对齐",
)
PROPOSAL_HINTS = (
    "换成",
    "改成",
    "切到",
    "切换",
    "试试",
    "尝试",
    "补",
    "对照",
    "比较",
    "路线",
    "方向",
)
BOUNDARY_CHANGE_HINTS = ("改", "换", "改成", "换成", "切成", "重设", "重新定义", "approve")
TASK_CHANGE_KEYWORDS = ("xyz", "回归", "二分类", "分类", "任务类型", "task type")
BOUNDARY_CHANGE_PATTERNS = (
    "canonical split 改",
    "改 canonical split",
    "数据划分改",
    "改数据划分",
    "随机划分",
    "primary metric",
    "改 raw data",
    "raw data 改",
    "改原始数据",
    "原始数据改",
    "改 alignment",
    "alignment 改",
    "改对齐",
    "对齐改",
    "canonical program",
)
MODEL_TOKENS = (
    "feature_gru",
    "feature_tcn",
    "feature_lstm",
    "feature_cnn_lstm",
    "feature_state_space_lite",
    "feature_conformer_lite",
    "xgboost",
    "ridge",
    "random_forest",
    "extra_trees",
    "catboost",
)
UNSAFE_SMOKE_TOKENS = ("supervise", "launch", "run campaign", "npm -C")


def ensure_shell_session(session_state: dict[str, Any] | None) -> dict[str, Any]:
    state = session_state if session_state is not None else {}
    if not str(state.get("session_id") or "").strip():
        state["session_id"] = f"{SHELL_SESSION_PREFIX}-{uuid.uuid4().hex[:12]}"
    if not isinstance(state.get("turn_counter"), int):
        state["turn_counter"] = 0
    return state


def next_turn_id(session_state: dict[str, Any]) -> str:
    counter = int(session_state.get("turn_counter") or 0) + 1
    session_state["turn_counter"] = counter
    return f"{session_state['session_id']}-turn-{counter:04d}"


def normalize_request(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def action_summary_label(intent_kind: str) -> str:
    labels = {
        "read_status": "查看当前研究态",
        "open_dashboard": "打开运行态投影",
        "report_latest": "读取最新摘要",
        "draft_program": "生成 Program 草案",
        "draft_proposal": "生成候选研究草案",
        "draft_amendment": "生成 Program Amendment 草案",
        "run_smoke": "准备受控 AutoResearch 探针",
        "plan_autoresearch": "用 AutoResearch 方法论制定计划",
        "run_bare_probe": "准备 bare run 探针",
        "intake_chat": "继续计划对话",
        "cancel_or_help": "查看帮助或取消当前动作",
    }
    return labels.get(intent_kind, intent_kind)


def compact_slug(text: str, *, max_len: int = 48) -> str:
    cleaned = []
    for char in text.lower():
        if char.isalnum():
            cleaned.append(char)
        elif char in {" ", "-", "_"}:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:max_len].strip("-") or "request"


def active_track_context(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    status = snapshot.get("autoresearch_status") if isinstance(snapshot.get("autoresearch_status"), dict) else {}
    active_track_id = str(status.get("active_track_id") or snapshot.get("current_track_id") or "").strip()
    track_states = status.get("track_states") if isinstance(status.get("track_states"), list) else []
    for item in track_states:
        if isinstance(item, dict) and str(item.get("track_id") or "").strip() == active_track_id:
            return active_track_id, item
    return active_track_id, {}


def classify_user_turn(command_text: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_request(command_text)
    lowered = normalized.lower()
    active_track_id, active_track = active_track_context(snapshot)
    track_goal = str(active_track.get("track_goal") or "").strip()
    smoke_command = str(active_track.get("smoke_command") or "").strip()

    if not normalized:
        return {
            "recognized": False,
            "user_intent_kind": "cancel_or_help",
            "normalized_request": "",
            "target_scope": active_track_id or "shell",
            "proposed_action": "help",
            "command_preview": "",
            "requires_confirmation": False,
            "result_status": "rejected",
            "summary": "请输入命令或自然语言请求。",
        }

    if lowered.startswith("propose "):
        normalized = normalize_request(normalized[8:])
        lowered = normalized.lower()
        intent_kind = "draft_proposal"
    elif lowered.startswith("program "):
        normalized = normalize_request(normalized[8:])
        lowered = normalized.lower()
        intent_kind = "draft_program"
    elif lowered.startswith("amend "):
        normalized = normalize_request(normalized[6:])
        lowered = normalized.lower()
        intent_kind = "draft_amendment"
    elif lowered == "run smoke":
        intent_kind = "run_smoke"
    elif any(keyword in lowered for keyword in HELP_KEYWORDS):
        intent_kind = "cancel_or_help"
    elif (
        any(keyword in lowered for keyword in BOUNDARY_KEYWORDS)
        and any(token in normalized for token in BOUNDARY_CHANGE_HINTS)
        and any(pattern in lowered for pattern in BOUNDARY_CHANGE_PATTERNS)
    ):
        intent_kind = "draft_amendment"
    else:
        has_model_token = any(token in lowered for token in MODEL_TOKENS)
        has_proposal_hint = any(token in normalized for token in PROPOSAL_HINTS) or has_model_token
        has_smoke_hint = any(keyword in lowered for keyword in RUN_SMOKE_KEYWORDS)
        has_program_intake_hint = any(keyword in lowered or keyword in normalized for keyword in PROGRAM_INTAKE_KEYWORDS)
        program_state = snapshot.get("program_state") if isinstance(snapshot.get("program_state"), dict) else {}
        frozen_program_active = str((program_state or {}).get("status") or "").strip() == "frozen"
        task_change_requested = (
            frozen_program_active
            and any(token in lowered or token in normalized for token in TASK_CHANGE_KEYWORDS)
            and any(token in normalized for token in PROPOSAL_HINTS + BOUNDARY_CHANGE_HINTS)
        )
        if task_change_requested:
            intent_kind = "draft_amendment"
        elif has_program_intake_hint:
            intent_kind = "draft_program"
        elif has_proposal_hint:
            intent_kind = "draft_proposal"
        elif has_smoke_hint:
            intent_kind = "run_smoke"
        elif any(keyword in normalized for keyword in STATUS_KEYWORDS):
            intent_kind = "read_status"
        elif any(keyword in lowered for keyword in DASHBOARD_KEYWORDS):
            intent_kind = "open_dashboard"
        elif any(keyword in lowered for keyword in REPORT_KEYWORDS):
            intent_kind = "report_latest"
        else:
            intent_kind = "intake_chat"

    target_scope = active_track_id or "shell"
    command_preview = ""
    requires_confirmation = intent_kind in {"draft_program", "draft_proposal", "draft_amendment", "run_smoke"}
    result_status = "awaiting_confirmation" if requires_confirmation else "ready"
    summary = action_summary_label(intent_kind)
    boundary_note = ""
    program_draft = build_program_draft_from_request(normalized) if intent_kind == "draft_program" else None

    if intent_kind == "open_dashboard":
        command_preview = "autobci dashboard"
        target_scope = "dashboard"
    elif intent_kind == "read_status":
        command_preview = "autobci status"
        target_scope = active_track_id or "runtime_status"
    elif intent_kind == "report_latest":
        command_preview = "autobci report latest"
        target_scope = active_track_id or "latest_report"
    elif intent_kind == "draft_proposal":
        command_preview = f"topics.inbox.json <= {PROPOSAL_SCOPE_LABEL}"
        boundary_note = "只写候选研究对象，不改 canonical Program。"
    elif intent_kind == "draft_program":
        program_id = str(program_draft.get("program_id") or "program") if isinstance(program_draft, dict) else "program"
        command_preview = f"programs/{program_id}/program.json <= frozen Program after approve"
        target_scope = f"Program:{program_id}"
        boundary_note = "Program 草案只在 approve 后冻结；不会直接启动实验。"
    elif intent_kind == "draft_amendment":
        command_preview = "amendments.inbox.json <= program_amendment_draft"
        target_scope = "canonical_program"
        boundary_note = "只写 amendment 草案，不直接批准。"
    elif intent_kind == "run_smoke":
        command_preview = smoke_command
        target_scope = active_track_id or "smoke"
        boundary_note = "这是兼容旧入口的受控 AutoResearch 探针，不是用户直接操作线性管线。"
        if not smoke_command:
            requires_confirmation = False
            result_status = "rejected"
            summary = "当前活动 track 没有可复用的 smoke_command。"
        elif any(token in smoke_command.lower() for token in UNSAFE_SMOKE_TOKENS):
            requires_confirmation = False
            result_status = "rejected"
            summary = "当前 smoke_command 超出聊天壳安全边界，已拒绝执行。"
    elif intent_kind == "cancel_or_help":
        command_preview = "help"
        target_scope = "shell"
        summary = "当前输入更像帮助请求，或还不足以形成受控动作。"
    elif intent_kind == "intake_chat":
        command_preview = ""
        target_scope = "intake"
        if any(keyword in lowered or keyword in normalized for keyword in GREETING_KEYWORDS):
            summary = "用户开始一段计划对话。"
        else:
            summary = "用户给了一个还不足以形成 Program 的模糊输入。"

    return {
        "recognized": intent_kind != "cancel_or_help" or any(keyword in lowered for keyword in HELP_KEYWORDS),
        "user_intent_kind": intent_kind,
        "normalized_request": normalized,
        "target_scope": target_scope,
        "proposed_action": intent_kind,
        "command_preview": command_preview,
        "requires_confirmation": requires_confirmation,
        "result_status": result_status,
        "summary": summary,
        "boundary_note": boundary_note,
        "active_track_id": active_track_id,
        "track_goal": track_goal,
        "smoke_command": smoke_command,
        "program_draft": program_draft,
    }


def build_confirmation_message(intent: dict[str, Any]) -> str:
    if intent.get("user_intent_kind") == "draft_program" and isinstance(intent.get("program_draft"), dict):
        draft = intent["program_draft"]
        goal = draft.get("research_goal") if isinstance(draft.get("research_goal"), dict) else {}
        metrics = draft.get("metrics") if isinstance(draft.get("metrics"), dict) else {}
        label = draft.get("label_definition") if isinstance(draft.get("label_definition"), dict) else {}
        return "\n".join(
            [
                "我先不启动实验。已整理出 Program 草案，等待确认。",
                f"- 研究目标：{goal.get('statement') or draft.get('program_id') or '-'}",
                f"- 任务类型：{goal.get('task_type') or '-'}",
                f"- 主指标：{metrics.get('primary') or '-'}",
                f"- 标签风险：{', '.join(str(item) for item in label.get('known_risks', [])[:3]) if isinstance(label.get('known_risks'), list) else '-'}",
                "输入 approve / /approve 冻结；输入 cancel 取消。",
            ]
        )
    lines = [
        f"我理解你要做什么：{intent.get('summary') or '-'}",
        f"这会变成什么研究动作：{intent.get('proposed_action') or '-'}",
    ]
    command_preview = str(intent.get("command_preview") or "").strip()
    boundary_note = str(intent.get("boundary_note") or "").strip()
    if command_preview:
        lines.append(f"命令预览：{command_preview}")
    if boundary_note:
        lines.append(f"边界说明：{boundary_note}")
    lines.append("现在状态：等待确认。输入 approve 执行，或输入 cancel 取消。")
    return "\n".join(lines)


def build_intake_chat_message(intent: dict[str, Any]) -> str:
    agent_message = str(intent.get("agent_message") or "").strip()
    if agent_message:
        return agent_message
    normalized = str(intent.get("normalized_request") or "").strip()
    lowered = normalized.lower()
    greeting = any(keyword in lowered or keyword in normalized for keyword in GREETING_KEYWORDS)
    if greeting:
        return (
            "你好，我是 AutoBCI 的研究计划助手。你可以先用一句话描述想研究的任务、数据和指标，"
            "不确定也可以。\n"
            "例如：我想用本地脑电和运动学数据建立严格因果的解码任务。"
        )
    return (
        "我还不能把这句话整理成可执行的 Program 草案。你可以先描述："
        "研究目标、可用数据、标签从哪里来、什么指标算成功；不确定的地方可以直接说不确定。"
    )


def build_direct_result_message(intent: dict[str, Any], result_body: str) -> str:
    lines = [
        f"我理解你要做什么：{intent.get('summary') or '-'}",
        f"这会变成什么研究动作：{intent.get('proposed_action') or '-'}",
        "现在状态：直接执行。",
    ]
    if result_body.strip():
        lines.append(result_body.strip())
    return "\n".join(lines)


def build_help_message() -> str:
    return (
        "常用命令：new | data | run | model | tasks | dashboard | status\n"
        "高级命令：program show | status | help | quit | plan show | director | snapshot | fork | archive | resume\n"
        "AutoBCI 不再维护 TUI；请通过 `autobci ask \"...\" --json` 或现有 agent 对话调用这些命令。\n"
        "也可以直接说自然语言，例如“新起一个任务”“切换任务”“打开 dashboard”“现在进展如何”。"
    )


def draft_proposal(paths: AutoBciControlPlanePaths, intent: dict[str, Any]) -> tuple[str, list[str]]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    topic_id = f"chat-proposal-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    active_track_id = str(intent.get("active_track_id") or "").strip()
    track_goal = str(intent.get("track_goal") or "").strip()
    normalized = str(intent.get("normalized_request") or "").strip()
    title_core = compact_slug(normalized or active_track_id or topic_id, max_len=32).replace("-", " ")
    payload = {
        "topic_id": topic_id,
        "title": f"聊天壳研究草案：{title_core}",
        "goal": normalized,
        "success_metric": "在不改 canonical split 和 primary metric 的前提下产出新的 smoke artifact。",
        "scope_label": PROPOSAL_SCOPE_LABEL,
        "priority": 0.61,
        "status": "draft",
        "promotable": True,
        "blocked_reason": "",
        "proposed_tracks": [active_track_id] if active_track_id else [],
        "source_evidence_ids": [],
        "created_by": "autobci-chat-shell",
        "last_decision_at": now,
        "last_decision_summary": "来自聊天优先壳的候选研究草案，等待进入 runnable 决策。",
        "normalized_request": normalized,
        "target_scope": active_track_id or "topics.inbox",
        "track_goal": track_goal,
    }
    topics = read_topics_inbox(paths.topics_inbox)
    topics.append(payload)
    topics.sort(key=lambda item: (-float(item.get("priority") or 0.0), str(item.get("topic_id") or "")))
    write_topics_inbox(paths.topics_inbox, topics)
    return topic_id, [str(paths.topics_inbox)]


def draft_amendment(paths: AutoBciControlPlanePaths, intent: dict[str, Any]) -> tuple[str, list[str]]:
    amendment_id = f"chat-amendment-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    target_path = paths.amendments_inbox
    rows = read_json(target_path, [])
    if not isinstance(rows, list):
        rows = []
    normalized = str(intent.get("normalized_request") or "").strip()
    payload = {
        "amendment_id": amendment_id,
        "kind": AMENDMENT_KIND,
        "status": "draft",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "created_by": "autobci-chat-shell",
        "target_scope": "canonical_program",
        "normalized_request": normalized,
        "active_track_id": str(intent.get("active_track_id") or "").strip(),
        "blocked_fields": [field for field in ("split", "primary_metric", "program", "raw_data", "alignment") if field in normalized.lower()],
    }
    rows.append(payload)
    write_json_atomic(target_path, rows)
    message = build_control_message(
        message_type="amendment_request",
        source_role="intake",
        target_role="director_executor",
        program_id="canonical_program",
        run_id=amendment_id,
        payload={
            "requested_by": "user",
            "reason": normalized or "用户请求修订 Program。",
            "requested_change": {
                "normalized_request": normalized,
                "blocked_fields": payload["blocked_fields"],
            },
            "evidence": [],
            "risk": "medium" if payload["blocked_fields"] else "low",
        },
    )
    append_control_message(paths.messages_ledger, message)
    return amendment_id, [str(target_path), str(paths.messages_ledger)]


def freeze_program_from_intent(paths: AutoBciControlPlanePaths, intent: dict[str, Any]) -> tuple[str, list[str]]:
    draft = intent.get("program_draft")
    if not isinstance(draft, dict):
        draft = build_program_draft_from_request(str(intent.get("normalized_request") or ""))
    run_id = f"program-freeze-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    frozen, refs = freeze_program_contract(paths, draft, run_id=run_id)
    snapshot_ref = next((ref for ref in refs if ref.endswith(f"{run_id}.json")), "")
    handoff = build_control_message(
        message_type="program_handoff",
        source_role="intake",
        target_role="director_executor",
        program_id=str(frozen["program_id"]),
        run_id=run_id,
        payload={
            "version": str(frozen["version"]),
            "program_snapshot_path": snapshot_ref,
            "frozen_at": str(frozen.get("frozen_at") or ""),
            "allowed_actions": ["read_program", "run_with_guard", "request_amendment"],
            "forbidden_actions": list(frozen.get("forbidden_actions") or []),
        },
    )
    append_control_message(paths.messages_ledger, handoff)
    refs.append(str(paths.messages_ledger))
    return str(frozen["program_id"]), refs


def launch_smoke(
    paths: AutoBciControlPlanePaths,
    intent: dict[str, Any],
    *,
    popen_factory: Callable[..., subprocess.Popen[bytes] | object] = subprocess.Popen,
) -> tuple[str, list[str]]:
    smoke_command = str(intent.get("smoke_command") or "").strip()
    if not smoke_command:
        raise ValueError("当前没有可用的 smoke_command。")
    if any(token in smoke_command.lower() for token in UNSAFE_SMOKE_TOKENS):
        raise ValueError("当前 smoke_command 超出聊天壳安全边界。")

    run_dir = paths.monitor_dir / "chat_shell_runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"chat-smoke-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    log_path = run_dir / f"{run_id}.log"
    env = os.environ.copy()
    env.setdefault(DEFAULT_CACHE_ROOT_ENV, str(default_cache_root()))
    env[AUTOBCI_ROOT_ENV] = str(paths.repo_root)
    with log_path.open("a", encoding="utf-8") as handle:
        popen_factory(
            shlex.split(smoke_command),
            cwd=str(paths.repo_root),
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            **detached_process_kwargs(),
        )
    return run_id, [str(log_path)]


def append_shell_trace(
    paths: AutoBciControlPlanePaths,
    *,
    session_id: str,
    turn_id: str,
    intent: dict[str, Any],
    command_text: str,
    ok: bool,
    message: str,
    confirmation_result: str,
    artifact_refs: list[str] | None = None,
    result_status: str,
) -> dict[str, Any]:
    row = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": str(intent.get("proposed_action") or intent.get("user_intent_kind") or "chat_turn"),
        "input": {
            "intent_kind": str(intent.get("user_intent_kind") or ""),
            "target_scope": str(intent.get("target_scope") or ""),
        },
        "ok": bool(ok),
        "message": str(message or "").strip(),
        "session_id": session_id,
        "turn_id": turn_id,
        "user_intent_kind": str(intent.get("user_intent_kind") or ""),
        "normalized_request": str(intent.get("normalized_request") or normalize_request(command_text)),
        "target_scope": str(intent.get("target_scope") or ""),
        "proposed_action": str(intent.get("proposed_action") or ""),
        "command_preview": str(intent.get("command_preview") or ""),
        "requires_confirmation": bool(intent.get("requires_confirmation")),
        "confirmation_result": confirmation_result,
        "artifact_refs": list(artifact_refs or []),
        "result_status": result_status,
    }
    audit_refs = append_judgment_chain_audit(
        paths,
        session_id=session_id,
        turn_id=turn_id,
        intent=intent,
        command_text=command_text,
        ok=ok,
        message=message,
        confirmation_result=confirmation_result,
        artifact_refs=artifact_refs,
        result_status=result_status,
    )
    if audit_refs:
        row["audit_refs"] = audit_refs
    append_jsonl(paths.control_events, row)
    return row


def _redact_audit_text(value: Any, *, limit: int = 800) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"sk-(?:api-)?[A-Za-z0-9_-]{12,}", "sk-[redacted]", text)
    text = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _audit_program_brief(intent: dict[str, Any]) -> dict[str, Any]:
    draft = intent.get("program_draft")
    if not isinstance(draft, dict):
        return {}
    return {
        "program_id": str(draft.get("program_id") or ""),
        "task_type": str(draft.get("task_type") or ""),
        "primary_metric": str(draft.get("primary_metric") or ""),
        "status": str(draft.get("status") or ""),
    }


def _safe_session_filename(session_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session_id or "session").strip()).strip(".-")
    return slug[:80] or "session"


def append_judgment_chain_audit(
    paths: AutoBciControlPlanePaths,
    *,
    session_id: str,
    turn_id: str,
    intent: dict[str, Any],
    command_text: str,
    ok: bool,
    message: str,
    confirmation_result: str,
    artifact_refs: list[str] | None = None,
    result_status: str,
) -> list[str]:
    """Persist an auditable decision chain summary, not raw model chain-of-thought."""

    recorded_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    audit_dir = paths.monitor_dir / "audit"
    jsonl_path = audit_dir / "judgment_chain.jsonl"
    session_path = audit_dir / "sessions" / f"{_safe_session_filename(session_id)}.md"
    row = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "recorded_at": recorded_at,
        "session_id": str(session_id or ""),
        "turn_id": str(turn_id or ""),
        "actor": "AutoBCI",
        "input": {
            "user_excerpt": _redact_audit_text(command_text, limit=500),
            "normalized_request": _redact_audit_text(intent.get("normalized_request") or normalize_request(command_text), limit=500),
        },
        "decision": {
            "intent_kind": str(intent.get("user_intent_kind") or ""),
            "proposed_action": str(intent.get("proposed_action") or ""),
            "target_scope": str(intent.get("target_scope") or ""),
            "decision_summary": _redact_audit_text(intent.get("summary") or intent.get("agent_message") or message, limit=500),
            "requires_confirmation": bool(intent.get("requires_confirmation")),
            "confirmation_result": confirmation_result,
            "result_status": result_status,
            "ok": bool(ok),
        },
        "evidence": {
            "command_preview": _redact_audit_text(intent.get("command_preview"), limit=500),
            "artifact_refs": list(artifact_refs or []),
            "program": _audit_program_brief(intent),
        },
        "reasoning_visibility": {
            "mode": str(intent.get("reasoning_mode") or "audit"),
            "raw_cot_requested": str(intent.get("reasoning_mode") or "audit") in {"raw", "debug"},
            "raw_cot_visible": bool(intent.get("raw_reasoning")) and str(intent.get("reasoning_mode") or "audit") in {"raw", "debug"},
            "raw_cot_saved": bool(intent.get("raw_reasoning")) and str(intent.get("reasoning_mode") or "audit") in {"raw", "debug"},
            "saved_form": "provider_raw_reasoning" if bool(intent.get("raw_reasoning")) and str(intent.get("reasoning_mode") or "audit") in {"raw", "debug"} else "audit_summary",
            "note": "AutoBCI always stores auditable decisions, evidence, tools, and artifacts; raw CoT is stored only when debug mode is enabled and the provider explicitly returns it.",
        },
        "output": {
            "message_excerpt": _redact_audit_text(message, limit=700),
        },
    }
    if row["reasoning_visibility"]["raw_cot_saved"]:
        row["reasoning_visibility"]["raw_cot_excerpt"] = _redact_audit_text(intent.get("raw_reasoning"), limit=3000)
    append_jsonl(jsonl_path, row)

    heading = "# AutoBCI 可审计判断链\n\n" if not session_path.exists() else ""
    cot_line = (
        "- CoT：已保存 provider 显式返回的 raw reasoning 摘要。"
        if row["reasoning_visibility"]["raw_cot_saved"]
        else "- CoT：未保存原始思维链；本文件保存可审计判断摘要、工具动作和 artifact 引用。"
    )
    chunk = "\n".join(
        [
            f"## {recorded_at} · {turn_id or '-'}",
            "",
            f"- 输入：{row['input']['user_excerpt'] or '-'}",
            f"- 判断：{row['decision']['decision_summary'] or row['decision']['proposed_action'] or '-'}",
            f"- 动作：{row['decision']['proposed_action'] or '-'} · 状态：{result_status or '-'} · 确认：{confirmation_result or '-'}",
            f"- 证据：{', '.join(row['evidence']['artifact_refs']) if row['evidence']['artifact_refs'] else '-'}",
            cot_line,
            "",
        ]
    )
    previous = read_text(session_path, "")
    write_text_atomic(session_path, heading + previous + chunk)
    return [str(jsonl_path), str(session_path)]
