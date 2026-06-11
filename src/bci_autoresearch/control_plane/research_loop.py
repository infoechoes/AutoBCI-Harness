from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bci_autoresearch.control_plane.builtin_patch_worker import run_builtin_patch_worker as _run_builtin_patch_worker
from bci_autoresearch.data_paths import require_dataset_root


TASK_ID = "rsvp_ship_image_only_v0"
LOOP_ROOT = Path("artifacts/research_loop")
LATEST_IMAGE_RESULT = Path("artifacts/monitor/rsvp_ship_image_autoresearch_latest.json")
STRUCTURE_SANDBOX_RUNNER_ENV = "AUTOBCI_STRUCTURE_SANDBOX_RUNNER"
STRUCTURE_SANDBOX_TIMEOUT_ENV = "AUTOBCI_STRUCTURE_SANDBOX_TIMEOUT_SECONDS"
DEFAULT_STRUCTURE_EDITABLE_FILES = ["experiments/rsvp_ship_image_structure/structure_runner.py"]
DEFAULT_STRUCTURE_SMOKE_COMMAND = f"{sys.executable} -m py_compile experiments/rsvp_ship_image_structure/structure_runner.py"
STRUCTURE_RELEASE_BASELINE_FILES = [
    "scripts/run_rsvp_ship_image_autoresearch.py",
    "experiments/rsvp_ship_image_structure/structure_runner.py",
    "programs/rsvp_ship_image_only_v0/ProgramMD.md",
    "programs/rsvp_ship_image_only_v0/program.json",
    "tests/test_rsvp_ship_image_autoresearch.py",
]
TRACE_SCHEMA_VERSION = "research_trace_v1"

ACTION_DISPLAY_LABELS = {
    "select_active_track": "选择研究方向",
    "analyze_latest_artifact": "分析最新结果",
    "run_existing_runner": "运行现有 runner",
    "write_result_artifact": "写入结果产物",
    "summarize_fixed_eval": "汇总固定评估",
    "create_structure_worktree": "创建结构沙盒",
    "run_structure_researcher": "调用结构研究员",
    "structure_diff_created": "记录结构改动",
    "run_structure_smoke": "运行结构 smoke",
    "run_fixed_eval": "运行固定评估器",
    "judge_track_result": "判断保留或拒绝",
    "step_pre": "等待大步骤确认",
    "edit_code_pre": "等待结构沙盒确认",
    "post_step_review": "等待结果复核",
    "edit_code_review": "等待结构候选复核",
    "promote_review": "等待晋级复核",
    "risk_review": "等待风险复核",
    "human_paused": "人工暂停",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _normalize_repo_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _slug(value: Any) -> str:
    raw = str(value or "track").strip().lower()
    chars = [ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw]
    return "-".join("".join(chars).split("-"))[:64] or "track"


def _run_subprocess(
    args: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout: int = 600,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
        check=False,
    )


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = _run_subprocess(["git", *args], cwd=cwd, timeout=120)
    if check and completed.returncode != 0:
        raise RuntimeError(_tail(completed.stderr or completed.stdout or f"git {' '.join(args)} failed"))
    return completed


def _git_stdout(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def _status_touched_files(cwd: Path) -> list[str]:
    completed = _git(cwd, "status", "--porcelain", check=False)
    files: list[str] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        payload = line[3:] if len(line) > 3 else line
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1]
        files.append(_normalize_repo_path(payload.strip()))
    return sorted(set(files))


def _diff_touched_files(cwd: Path, before: str, after: str) -> list[str]:
    if before == after:
        return []
    completed = _git(cwd, "diff", "--name-only", f"{before}..{after}", check=False)
    return sorted({_normalize_repo_path(line.strip()) for line in completed.stdout.splitlines() if line.strip()})


def _diff_summary(cwd: Path, before: str, after: str) -> str:
    if before == after:
        return ""
    completed = _git(cwd, "diff", "--stat", f"{before}..{after}", check=False)
    return completed.stdout.strip()


def _cleanup_python_bytecode(cwd: Path, touched_files: list[str]) -> None:
    roots = sorted({(cwd / path).parent for path in touched_files})
    for root in roots:
        if not root.exists():
            continue
        for pycache in sorted(root.rglob("__pycache__")):
            relative = _normalize_repo_path(pycache.relative_to(cwd))
            if relative.startswith("data/raw/"):
                continue
            shutil.rmtree(pycache, ignore_errors=True)
        for pyc in sorted(root.rglob("*.pyc")):
            if not pyc.exists():
                continue
            relative = _normalize_repo_path(pyc.relative_to(cwd))
            if relative.startswith("data/raw/"):
                continue
            pyc.unlink()


def _is_allowed_edit(path: str, editable_files: list[str]) -> bool:
    normalized = _normalize_repo_path(path)
    return normalized in {_normalize_repo_path(item) for item in editable_files}


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def write_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _redact_trace_text(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"sk-(?:api-)?[A-Za-z0-9_-]{12,}", "sk-[redacted]", text)
    text = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)
    if len(text) > limit:
        return text[-limit:]
    return text


def _redact_trace_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_trace_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_redact_trace_payload(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if "key" in str(key).lower() or "secret" in str(key).lower() or "token" in str(key).lower():
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_trace_payload(item)
        return redacted
    return value


def _compact_trace_value(value: Any, *, limit: int = 120) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (int, bool)) or value is None:
        return str(value)
    if isinstance(value, list):
        inner = ", ".join(_compact_trace_value(item, limit=40) for item in value[:4])
        suffix = ", ..." if len(value) > 4 else ""
        return f"[{inner}{suffix}]"
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in list(value.items())[:5]:
            parts.append(f"{key}={_compact_trace_value(item, limit=40)}")
        suffix = ", ..." if len(value) > 5 else ""
        return "{" + ", ".join(parts) + suffix + "}"
    text = str(value or "")
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _compact_trace_map(payload: dict[str, Any], *, keys: list[str] | None = None) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    selected = keys or list(payload.keys())
    parts: list[str] = []
    for key in selected:
        if key not in payload:
            continue
        value = payload.get(key)
        if value in ("", None, [], {}):
            continue
        parts.append(f"{key}={_compact_trace_value(value)}")
    return ", ".join(parts)


def _decision_summary(decision: dict[str, Any] | str | None) -> list[str]:
    if decision in (None, "", {}):
        return []
    if isinstance(decision, str):
        return [f"结果：{_compact_trace_value(decision)}"]
    if not isinstance(decision, dict):
        return [f"结果：{_compact_trace_value(decision)}"]
    lines: list[str] = []
    score_keys = [
        "selected_test_balanced_accuracy",
        "per_run_best_test_balanced_accuracy",
        "best_test_balanced_accuracy_mean",
        "selected_test_balanced_accuracy_mean",
        "best_test_balanced_accuracy_std",
    ]
    scores = _compact_trace_map(decision, keys=score_keys)
    if scores:
        lines.append(f"分数：{scores}")
    if decision.get("decision"):
        lines.append(f"判断：{decision.get('decision')}")
    if decision.get("human_gate_required") is not None:
        lines.append(f"人工确认：{bool(decision.get('human_gate_required'))}")
    if decision.get("diff_summary"):
        lines.append(f"结构改动：{_compact_trace_value(decision.get('diff_summary'), limit=180)}")
    rules = decision.get("rules_checked")
    if isinstance(rules, list) and rules:
        failed = [
            str(item.get("rule") or "-")
            for item in rules
            if isinstance(item, dict) and item.get("passed") is False
        ]
        if failed:
            lines.append("规则未通过：" + ", ".join(failed[:4]))
        else:
            lines.append(f"规则：{len(rules)} 项已检查")
    counter = decision.get("counter_evidence")
    if isinstance(counter, list) and counter:
        lines.append("反证：" + _compact_trace_value(counter[0], limit=180))
    return lines


def _trace_event_display(
    *,
    actor: str,
    event_type: str,
    action: str,
    track: dict[str, Any] | None,
    reason: str,
    inputs: dict[str, Any] | None,
    command: str | list[str] | None,
    exit_code: int | None,
    stdout_tail: str,
    stderr_tail: str,
    artifact_refs: list[str] | None,
    decision: dict[str, Any] | str | None,
    risk_flags: list[str] | None,
) -> dict[str, Any]:
    current_track = track or {}
    action_label = ACTION_DISPLAY_LABELS.get(action, action.replace("_", " ") or event_type)
    title = f"{actor} · {action_label}"
    track_title = str(current_track.get("title") or "").strip()
    if track_title and action == "select_active_track":
        title = f"{actor} · {action_label}：{track_title}"
    summary = _redact_trace_text(reason or str(current_track.get("hypothesis") or action_label), limit=220)
    details: list[str] = []
    if track_title and track_title not in title:
        details.append(f"研究方向：{track_title}")
    if current_track.get("direction"):
        details.append(f"算法方向：{current_track.get('direction')}")
    if current_track.get("action_type") or current_track.get("runner"):
        details.append(f"执行方式：{current_track.get('action_type') or '-'} / {current_track.get('runner') or '-'}")
    params = current_track.get("params") if isinstance(current_track.get("params"), dict) else {}
    if params:
        details.append("参数：" + _compact_trace_map(params))
    if current_track.get("hypothesis"):
        details.append("假设：" + _compact_trace_value(current_track.get("hypothesis"), limit=180))
    if current_track.get("expected_signal"):
        details.append("预期信号：" + _compact_trace_value(current_track.get("expected_signal"), limit=180))
    if current_track.get("novelty_signature"):
        details.append(f"novelty_signature：{current_track.get('novelty_signature')}")
    selected_inputs = _compact_trace_map(
        inputs or {},
        keys=[
            "ledger_count",
            "queued_count",
            "split_salt",
            "split_salts",
            "logistic_epochs",
            "editable_files",
            "worktree",
            "branch",
            "rollback_ref",
            "touched_files",
            "commit",
            "candidate_count",
            "artifact",
        ],
    )
    if selected_inputs:
        details.append("输入：" + selected_inputs)
    if command:
        details.append("命令：" + _compact_trace_value(command, limit=180))
    if exit_code is not None:
        details.append(f"退出码：{exit_code}")
    if stdout_tail:
        details.append("stdout：" + _compact_trace_value(stdout_tail, limit=180))
    if stderr_tail:
        details.append("stderr：" + _compact_trace_value(stderr_tail, limit=180))
    details.extend(_decision_summary(decision))
    if artifact_refs:
        details.append("产物：" + ", ".join(str(item) for item in artifact_refs[:3]))
    if risk_flags:
        details.append("风险：" + ", ".join(str(item) for item in risk_flags))
    return {"title": title, "summary": summary, "details": details[:10]}


def _trace_event(
    *,
    actor: str,
    event_type: str,
    action: str,
    track: dict[str, Any] | None = None,
    reason: str = "",
    inputs: dict[str, Any] | None = None,
    command: str | list[str] | None = None,
    exit_code: int | None = None,
    stdout_tail: str = "",
    stderr_tail: str = "",
    artifact_refs: list[str] | None = None,
    decision: dict[str, Any] | str | None = None,
    risk_flags: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    redacted_inputs = _redact_trace_payload(inputs or {})
    redacted_command = _redact_trace_payload(command or "")
    redacted_decision = _redact_trace_payload(decision or {})
    event = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "created_at": _utc_now(),
        "actor": actor,
        "event_type": event_type,
        "action": action,
        "track_id": str((track or {}).get("track_id") or ""),
        "direction": str((track or {}).get("direction") or ""),
        "reason": _redact_trace_text(reason, limit=1200),
        "inputs": redacted_inputs,
        "command": redacted_command,
        "exit_code": exit_code,
        "stdout_tail": _redact_trace_text(stdout_tail),
        "stderr_tail": _redact_trace_text(stderr_tail),
        "artifact_refs": [str(item) for item in (artifact_refs or [])],
        "decision": redacted_decision,
        "risk_flags": [str(item) for item in (risk_flags or [])],
        "run_id": str(run_id or ""),
    }
    event["display"] = _trace_event_display(
        actor=actor,
        event_type=event_type,
        action=action,
        track=track,
        reason=reason,
        inputs=redacted_inputs if isinstance(redacted_inputs, dict) else {},
        command=redacted_command,
        exit_code=exit_code,
        stdout_tail=_redact_trace_text(stdout_tail),
        stderr_tail=_redact_trace_text(stderr_tail),
        artifact_refs=[str(item) for item in (artifact_refs or [])],
        decision=redacted_decision,
        risk_flags=[str(item) for item in (risk_flags or [])],
    )
    return event


def _write_trace_events(root: Path, events: list[dict[str, Any]]) -> list[str]:
    event_ids: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_ids.append(str(event.get("event_id") or ""))
        write_jsonl_row(_events_path(root), event)
        run_id = str(event.get("run_id") or "").strip()
        if run_id:
            write_jsonl_row(_run_trace_path(root, run_id), event)
    return [item for item in event_ids if item]


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
    event = _trace_event(
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
    _write_trace_events(root, [event])
    return event


def loop_root(repo_root: str | Path, task_id: str = TASK_ID) -> Path:
    return Path(repo_root).expanduser().resolve() / LOOP_ROOT / task_id


def _latest_state_path(repo_root: Path) -> Path:
    return repo_root / LATEST_IMAGE_RESULT


def _load_latest_state(repo_root: Path) -> dict[str, Any]:
    state = read_json(_latest_state_path(repo_root), {})
    if not isinstance(state, dict) or not state:
        raise FileNotFoundError(f"Missing RSVP image latest state: {_latest_state_path(repo_root)}")
    return state


def _best_candidate(state: dict[str, Any]) -> dict[str, Any]:
    best: dict[str, Any] = {}
    best_score: float | None = None
    for candidate in state.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        metrics = candidate.get("test_metrics") if isinstance(candidate.get("test_metrics"), dict) else {}
        score = metrics.get("balanced_accuracy")
        if not isinstance(score, (int, float)):
            continue
        if best_score is None or float(score) > best_score:
            best_score = float(score)
            best = candidate
    return best


def _metric_from_result(result: dict[str, Any], key: str = "balanced_accuracy") -> float | None:
    metrics = result.get("test_metrics") if isinstance(result.get("test_metrics"), dict) else {}
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _best_score_from_result(result: dict[str, Any]) -> float | None:
    best = _best_candidate(result)
    metrics = best.get("test_metrics") if isinstance(best.get("test_metrics"), dict) else {}
    value = metrics.get("balanced_accuracy")
    return float(value) if isinstance(value, (int, float)) else _metric_from_result(result)


def _default_tracks(state: dict[str, Any]) -> list[dict[str, Any]]:
    best = _best_candidate(state)
    selected = state.get("selected_model") if isinstance(state.get("selected_model"), dict) else {}
    return [
        {
            "track_id": "threshold_calibration_overfit_diagnosis",
            "title": "阈值校准过拟合诊断",
            "hypothesis": "验证集阈值扫描把 selected model 推高，但测试集掉分，说明当前选择策略可能过拟合验证集。",
            "direction": "calibration_overfit_diagnosis",
            "action_type": "analysis_only",
            "runner": "latest_artifact_analysis",
            "params": {},
            "novelty_signature": "diagnose:selected-vs-best-gap",
            "expected_signal": "解释 selected=0.8472 与 per-run best=0.9767 的差距，禁止继续盲扫 epoch。",
            "risk": "只做诊断，不提升模型分数。",
            "stop_condition": "写出 selected/test-best 差距和下一步是否需要多划分复核。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "multi_split_robustness_lbp_color",
            "title": "LBP / 颜色候选多划分稳健复核",
            "hypothesis": "单划分高分可能来自偶然划分；多 split mean/std 才能决定是否进入 robust accepted best。",
            "direction": "multi_split_robustness",
            "action_type": "run_existing",
            "runner": "scripts.run_rsvp_ship_image_autoresearch",
            "params": {"logistic_epochs": 180, "split_salts": ["robust-a", "robust-b", "robust-c"]},
            "novelty_signature": "robustness:multi-split:lbp-color",
            "expected_signal": "输出 balanced accuracy mean/std，并标记单划分最高候选是否稳健。",
            "risk": "小数据集下方差可能很大，不能只看最高分。",
            "stop_condition": "完成至少 3 个 deterministic split salt。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "color_background_shortcut_check",
            "title": "颜色 / 背景捷径检查",
            "hypothesis": "颜色直方图高分可能说明模型在吃背景、亮度或采集条件，而不是真正识别船体。",
            "direction": "background_shortcut_check",
            "action_type": "run_existing",
            "runner": "scripts.run_rsvp_ship_image_autoresearch",
            "params": {"logistic_epochs": 180, "split_salt": "color-shortcut"},
            "novelty_signature": "diagnose:color-background-shortcut",
            "expected_signal": "复跑颜色/纹理/边缘候选，报告颜色模型相对 LBP/HOG 的风险。",
            "risk": "当前 runner 还没有真实遮挡消融，只能做候选证据层诊断。",
            "stop_condition": "写出颜色候选分数、风险和下一步遮挡消融建议。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "structure_fusion_probe",
            "title": "多特征结构融合探针",
            "hypothesis": "如果单一纹理或颜色特征不稳，把纹理、边缘、颜色、轮廓和边缘密度融合后应能检验结构组合是否带来更稳信号。",
            "direction": "structure_fusion_probe",
            "action_type": "run_existing",
            "runner": "scripts.run_rsvp_ship_image_autoresearch",
            "params": {"logistic_epochs": 180, "split_salt": "structure-fusion"},
            "novelty_signature": "structure:feature-fusion:lbp-hog-color-projection-edge",
            "expected_signal": "新增并评估 image_structure_fusion_logistic，不再只是改变训练 epoch。",
            "risk": "融合特征可能把颜色捷径一起带入，需要与单特征候选和多 split 结果对照。",
            "stop_condition": "生成包含 structure_fusion 候选的 result artifact，并写入 ledger。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "hard_negative_confusion_review",
            "title": "Hard negative 与错分桶分析",
            "hypothesis": "当前模型错误集中在非船误报或船漏检；下一步算法应围绕错误桶，而不是继续全局 sweep。",
            "direction": "hard_negative_error_analysis",
            "action_type": "analysis_only",
            "runner": "candidate_confusion_analysis",
            "params": {},
            "novelty_signature": "analysis:confusion-buckets",
            "expected_signal": "把 confusion matrix 转成船漏检/非船误报，给出下一个数据或算法动作。",
            "risk": "没有逐图预测明细时只能做类别级错误分析。",
            "stop_condition": "输出每个强候选的漏船数和非船误报数。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "edge_hog_recheck",
            "title": "HOG / 边缘方向复核",
            "hypothesis": "如果船体轮廓是真信号，边缘方向和边缘密度应在多划分下保持中高分。",
            "direction": "hog_edge_recheck",
            "action_type": "run_existing",
            "runner": "scripts.run_rsvp_ship_image_autoresearch",
            "params": {"logistic_epochs": 180, "split_salt": "hog-edge"},
            "novelty_signature": "feature:hog-edge-recheck",
            "expected_signal": "对比 HOG、edge density、LBP 和颜色候选。",
            "risk": "HOG 对低分辨率和裁剪位置敏感。",
            "stop_condition": "生成一次边缘方向相关候选结果。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "near_duplicate_label_audit",
            "title": "近重复 / 标签审计",
            "hypothesis": "过高的单划分测试分可能来自近重复、标签偏差或 split 泄漏。",
            "direction": "near_duplicate_label_audit",
            "action_type": "analysis_only",
            "runner": "dataset_audit_summary",
            "params": {},
            "novelty_signature": "audit:near-duplicate-label",
            "expected_signal": "读取 dataset audit，确认重复图没有跨 split，并建议 perceptual hash 近重复审计。",
            "risk": "当前只做内容 hash 审计，还不是感知哈希审计。",
            "stop_condition": "输出重复数量、冲突数量、近重复后续实现要求。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
        {
            "track_id": "tiny_cnn_isolated_runner",
            "title": "小型 CNN 隔离 runner",
            "hypothesis": "如果手工特征接近天花板，小 CNN 应只作为多划分复核后的后续方向。",
            "direction": "small_cnn",
            "action_type": "edit_code",
            "runner": "isolated_worktree_required",
            "params": {"worktree_required": True},
            "novelty_signature": "edit-code:tiny-cnn-runner",
            "expected_signal": "在隔离 worktree 新增 runner、smoke、result artifact。",
            "risk": "依赖和训练耗时可能污染当前环境。",
            "stop_condition": "只允许在隔离 worktree 中落地。",
            "evidence_ids": ["local_state_image_baseline"],
            "status": "queued",
        },
    ]


def _queue_path(root: Path) -> Path:
    return root / "queue.json"


def _ledger_path(root: Path) -> Path:
    return root / "ledger.jsonl"


def _events_path(root: Path) -> Path:
    return root / "events.jsonl"


def _run_trace_path(root: Path, run_id: str) -> Path:
    return root / "runs" / str(run_id) / "trace.jsonl"


def _active_track_path(root: Path) -> Path:
    return root / "active_track.json"


def _status_path(root: Path) -> Path:
    return root / "status.json"


def _load_queue(root: Path) -> list[dict[str, Any]]:
    payload = read_json(_queue_path(root), {})
    queue = payload.get("tracks") if isinstance(payload, dict) else None
    return [item for item in (queue or []) if isinstance(item, dict)]


def _write_queue(root: Path, tracks: list[dict[str, Any]]) -> None:
    write_json(_queue_path(root), {"updated_at": _utc_now(), "tracks": tracks})


def ensure_queue(repo_root: str | Path, task_id: str = TASK_ID) -> list[dict[str, Any]]:
    root = loop_root(repo_root, task_id)
    queue = _load_queue(root)
    state = _load_latest_state(Path(repo_root).expanduser().resolve())
    default_queue = _default_tracks(state)
    if queue:
        existing = {str(item.get("track_id")): item for item in queue}
        merged: list[dict[str, Any]] = []
        for default_track in default_queue:
            track_id = str(default_track.get("track_id"))
            merged.append(existing.pop(track_id, default_track))
        merged.extend(existing.values())
        if [item.get("track_id") for item in merged] != [item.get("track_id") for item in queue]:
            _write_queue(root, merged)
        return merged
    _write_queue(root, default_queue)
    return default_queue


def _queue_for_step(
    repo_root: Path,
    root: Path,
    state: dict[str, Any],
    *,
    task_id: str,
    only_track_id: str | None = None,
) -> list[dict[str, Any]]:
    if not only_track_id:
        return ensure_queue(repo_root, task_id)
    queue = _load_queue(root)
    if queue:
        return queue
    default_queue = _default_tracks(state)
    _write_queue(root, default_queue)
    return default_queue


def _last_repeated_signature(rows: list[dict[str, Any]], max_repeated_signature: int) -> str | None:
    if max_repeated_signature <= 1 or len(rows) < max_repeated_signature:
        return None
    tail = rows[-max_repeated_signature:]
    signatures = [str(row.get("novelty_signature") or "").strip() for row in tail]
    if signatures[0] and all(item == signatures[0] for item in signatures):
        return signatures[0]
    return None


def _candidate_error_labels(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = candidate.get("test_metrics") if isinstance(candidate.get("test_metrics"), dict) else {}
    cm = metrics.get("confusion_matrix")
    ship_misses = None
    nonship_false_alarms = None
    if isinstance(cm, list) and len(cm) >= 2 and all(isinstance(row, list) and len(row) >= 2 for row in cm[:2]):
        nonship_false_alarms = int(cm[0][1])
        ship_misses = int(cm[1][0])
    return {
        "model_family": candidate.get("model_family"),
        "feature_family": (candidate.get("config") or {}).get("feature_family") if isinstance(candidate.get("config"), dict) else None,
        "test_balanced_accuracy": metrics.get("balanced_accuracy"),
        "ship_misses": ship_misses,
        "nonship_false_alarms": nonship_false_alarms,
    }


def _analysis_result(track: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    best = _best_candidate(state)
    selected_score = _metric_from_result(state)
    best_score = _best_score_from_result(state)
    candidates = [
        _candidate_error_labels(item)
        for item in (state.get("candidates") or [])
        if isinstance(item, dict)
    ]
    candidates.sort(key=lambda item: item.get("test_balanced_accuracy") if isinstance(item.get("test_balanced_accuracy"), (int, float)) else -1, reverse=True)
    warning = ""
    if selected_score is not None and best_score is not None and best_score - selected_score > 0.05:
        warning = "selected model 明显低于 per-run best，疑似验证集选择或阈值校准过拟合。"
    trace_events = [
        _trace_event(
            actor="Executor",
            event_type="tool_call_start",
            action="analyze_latest_artifact",
            track=track,
            reason="读取最新 RSVP 纯图像结果，诊断 selected model 与 per-run best 的差距。",
            inputs={"artifact": str(LATEST_IMAGE_RESULT), "candidate_count": len(candidates)},
        ),
        _trace_event(
            actor="Evaluator",
            event_type="tool_call_end",
            action="analyze_latest_artifact",
            track=track,
            reason=warning or "完成最新结果诊断。",
            decision={
                "selected_test_balanced_accuracy": selected_score,
                "per_run_best_test_balanced_accuracy": best_score,
                "per_run_best_model_family": best.get("model_family"),
            },
            artifact_refs=[str(LATEST_IMAGE_RESULT)],
            risk_flags=["selected_vs_best_gap"] if warning else [],
        ),
    ]
    return {
        "status": "analysis_completed",
        "track_id": track["track_id"],
        "direction": track["direction"],
        "selected_test_balanced_accuracy": selected_score,
        "per_run_best_test_balanced_accuracy": best_score,
        "per_run_best_model_family": best.get("model_family"),
        "candidate_errors": candidates[:8],
        "warning": warning,
        "trace_events": trace_events,
        "safety": {
            "executor_started": True,
            "raw_data_touched": False,
            "formal_manifest_written": False,
            "campaign_started": False,
        },
    }


def _import_image_runner(repo_root: Path) -> Any:
    source_root = Path(__file__).resolve().parents[3]
    for candidate in (source_root, repo_root):
        root_text = str(candidate)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    from scripts.run_rsvp_ship_image_autoresearch import run_image_autoresearch

    return run_image_autoresearch


def _run_existing_track(repo_root: Path, root: Path, track: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    params = track.get("params") if isinstance(track.get("params"), dict) else {}
    dataset_root = require_dataset_root(repo_root, task_id=TASK_ID, state=state)
    run_image_autoresearch = _import_image_runner(repo_root)
    run_root = root / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    split_salts = [str(item) for item in params.get("split_salts", [])] if isinstance(params.get("split_salts"), list) else []
    if not split_salts:
        split_salts = [str(params.get("split_salt") or track["track_id"])]
    results: list[dict[str, Any]] = []
    trace_events: list[dict[str, Any]] = []
    for index, split_salt in enumerate(split_salts, start=1):
        run_id = f"{track['track_id']}-{int(time.time())}-{index}"
        trace_events.append(
            _trace_event(
                actor="Executor",
                event_type="tool_call_start",
                action="run_existing_runner",
                track=track,
                reason="运行现有 RSVP 纯图像 runner。",
                inputs={"split_salt": split_salt, "logistic_epochs": int(params.get("logistic_epochs") or 180)},
                command=f"{track.get('runner')}(... split_salt={split_salt})",
                run_id=run_id,
            )
        )
        result = run_image_autoresearch(
            dataset_root=dataset_root,
            output_dir=run_root,
            run_id=run_id,
            logistic_epochs=int(params.get("logistic_epochs") or 180),
            split_salt=split_salt,
            monitor_latest_path=None,
        )
        result["research_track"] = {
            "track_id": track["track_id"],
            "direction": track["direction"],
            "split_salt": split_salt,
        }
        write_json(root / "runs" / run_id / "result.json", result)
        result_path = str(root / "runs" / run_id / "result.json")
        trace_events.append(
            _trace_event(
                actor="Executor",
                event_type="tool_call_end",
                action="run_existing_runner",
                track=track,
                reason="现有 runner 执行完成。",
                command=f"{track.get('runner')}(... split_salt={split_salt})",
                exit_code=0,
                artifact_refs=[result_path],
                run_id=run_id,
            )
        )
        trace_events.append(
            _trace_event(
                actor="Evaluator",
                event_type="artifact_written",
                action="write_result_artifact",
                track=track,
                reason="写入固定评估结果 artifact。",
                artifact_refs=[result_path],
                decision={
                    "selected_test_balanced_accuracy": _metric_from_result(result),
                    "per_run_best_test_balanced_accuracy": _best_score_from_result(result),
                },
                run_id=run_id,
            )
        )
        results.append(result)

    best_scores = [score for score in (_best_score_from_result(item) for item in results) if score is not None]
    selected_scores = [score for score in (_metric_from_result(item) for item in results) if score is not None]
    robust_summary = {
        "split_count": len(results),
        "best_test_balanced_accuracy_mean": statistics.fmean(best_scores) if best_scores else None,
        "best_test_balanced_accuracy_std": statistics.pstdev(best_scores) if len(best_scores) > 1 else 0.0 if best_scores else None,
        "selected_test_balanced_accuracy_mean": statistics.fmean(selected_scores) if selected_scores else None,
        "selected_test_balanced_accuracy_std": statistics.pstdev(selected_scores) if len(selected_scores) > 1 else 0.0 if selected_scores else None,
    }
    return {
        "status": "completed_existing_runner",
        "track_id": track["track_id"],
        "direction": track["direction"],
        "run_ids": [item.get("run_id") for item in results],
        "result_paths": [str(root / "runs" / str(item.get("run_id")) / "result.json") for item in results],
        "robust_summary": robust_summary,
        "latest_result": results[-1] if results else {},
        "trace_events": [
            *trace_events,
            _trace_event(
                actor="Evaluator",
                event_type="evaluator_result",
                action="summarize_fixed_eval",
                track=track,
                reason="汇总 selected model、per-run best 和多划分稳定性。",
                decision=robust_summary,
                artifact_refs=[str(root / "runs" / str(item.get("run_id")) / "result.json") for item in results],
                risk_flags=["single_split"] if len(results) == 1 else [],
            ),
        ],
        "safety": {
            "executor_started": True,
            "raw_data_touched": False,
            "formal_manifest_written": False,
            "campaign_started": False,
        },
    }


def _structure_sandbox_prompt(track: dict[str, Any], editable_files: list[str], smoke_command: str) -> str:
    return "\n".join(
        [
            "You are running an AutoBCI RSVP image structure-sandbox experiment.",
            "Your job is to improve the image-only ship/not-ship classifier structure in one small edit.",
            "",
            "Hard rules:",
            f"- Only edit these files: {', '.join(editable_files)}",
            "- Do not edit fixed evaluators, dashboard files, Program files, lifecycle state, or data/raw.",
            "- Do not install dependencies.",
            "- Do not touch the RSVP dataset; it is read-only.",
            "- Commit your change before finishing.",
            f"- Run this smoke command before the commit if possible: {smoke_command}",
            "",
            "Track contract:",
            json.dumps(
                {
                    "track_id": track.get("track_id"),
                    "title": track.get("title"),
                    "hypothesis": track.get("hypothesis"),
                    "direction": track.get("direction"),
                    "expected_signal": track.get("expected_signal"),
                    "stop_condition": track.get("stop_condition"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "The fixed AutoBCI evaluator will run after you finish. Keep the final answer short.",
        ]
    )


def _create_structure_worktree(repo_root: Path, track: dict[str, Any]) -> dict[str, Any]:
    token = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    track_slug = _slug(track.get("track_id"))
    branch = f"autoresearch/rsvp-structure-{track_slug}-{token}"
    worktree = repo_root / ".autobci" / "worktrees" / "rsvp-structure" / f"{track_slug}-{token}"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "worktree", "add", "-b", branch, str(worktree), "HEAD")
    return {"worktree": worktree, "branch": branch, "rollback_ref": _git_stdout(worktree, "rev-parse", "HEAD")}


def _release_baseline_preflight(repo_root: Path, editable_files: list[str]) -> list[str]:
    required = sorted(set(STRUCTURE_RELEASE_BASELINE_FILES + editable_files))
    missing: list[str] = []
    for relative in required:
        path = _normalize_repo_path(relative)
        if not (repo_root / path).exists():
            missing.append(path)
            continue
        tracked = _git(repo_root, "ls-files", "--error-unmatch", path, check=False)
        if tracked.returncode != 0:
            missing.append(path)
    return missing


def _run_structure_researcher(worktree: Path, track: dict[str, Any], editable_files: list[str], smoke_command: str) -> dict[str, Any]:
    timeout = int(os.environ.get(STRUCTURE_SANDBOX_TIMEOUT_ENV) or "600")
    payload = {
        "track": track,
        "editable_files": editable_files,
        "smoke_command": smoke_command,
        "mode": os.environ.get("AUTOBCI_STRUCTURE_SANDBOX_MOCK_MODE"),
    }
    custom_runner = os.environ.get(STRUCTURE_SANDBOX_RUNNER_ENV)
    if custom_runner:
        command = [custom_runner]
        try:
            completed = _run_subprocess(command, cwd=worktree, input_text=json.dumps(payload, ensure_ascii=False), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command,
                "exit_code": 124,
                "stdout_tail": _tail((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
                "stderr_tail": f"structure researcher timed out after {timeout} seconds",
            }
        return {
            "command": command,
            "exit_code": completed.returncode,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
        }

    opencode = shutil.which("opencode")
    if opencode:
        prompt = _structure_sandbox_prompt(track, editable_files, smoke_command)
        command = [opencode, "run", prompt]
        try:
            completed = _run_subprocess(command, cwd=worktree, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return {
                "command": [opencode, "run", "<prompt>"],
                "exit_code": 124,
                "stdout_tail": _tail((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
                "stderr_tail": f"opencode run timed out after {timeout} seconds",
            }
        return {
            "command": [opencode, "run", "<prompt>"],
            "exit_code": completed.returncode,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
        }

    codex = shutil.which("codex")
    if not codex:
        builtin_result = _run_builtin_patch_worker(worktree, track, editable_files, smoke_command, timeout=timeout)
        if int(builtin_result.get("exit_code") or 0) != 0:
            builtin_result["stderr_tail"] = _tail(
                "opencode or codex CLI not found; " + str(builtin_result.get("stderr_tail") or "builtin patch worker failed")
            )
        return builtin_result
    prompt = _structure_sandbox_prompt(track, editable_files, smoke_command)
    command = [codex, "exec", "--full-auto", "--ephemeral", "--cd", str(worktree), prompt]
    try:
        completed = _run_subprocess(command, cwd=worktree, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": [codex, "exec", "--full-auto", "--ephemeral", "--cd", str(worktree), "<prompt>"],
            "exit_code": 124,
            "stdout_tail": _tail((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr_tail": f"codex exec timed out after {timeout} seconds",
        }
    return {
        "command": [codex, "exec", "--full-auto", "--ephemeral", "--cd", str(worktree), "<prompt>"],
        "exit_code": completed.returncode,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def _run_structure_smoke(worktree: Path, smoke_command: str) -> dict[str, Any]:
    completed = subprocess.run(
        smoke_command,
        cwd=worktree,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    return {
        "command": smoke_command,
        "exit_code": completed.returncode,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def _run_structure_fixed_eval(repo_root: Path, root: Path, worktree: Path, track: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    params = track.get("params") if isinstance(track.get("params"), dict) else {}
    dataset_root = require_dataset_root(repo_root, task_id=TASK_ID, state=state)
    run_id = f"{track['track_id']}-{int(time.time())}-edit"
    output_dir = root / "runs"
    payload = {
        "dataset_root": str(dataset_root),
        "output_dir": str(output_dir),
        "run_id": run_id,
        "logistic_epochs": int(params.get("logistic_epochs") or 180),
        "split_salt": str(params.get("split_salt") or track["track_id"]),
    }
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from scripts.run_rsvp_ship_image_autoresearch import run_image_autoresearch\n"
        "payload = json.loads(sys.stdin.read())\n"
        "result = run_image_autoresearch(\n"
        "    dataset_root=Path(payload['dataset_root']),\n"
        "    output_dir=Path(payload['output_dir']),\n"
        "    run_id=payload['run_id'],\n"
        "    logistic_epochs=int(payload['logistic_epochs']),\n"
        "    split_salt=payload['split_salt'],\n"
        "    monitor_latest_path=None,\n"
        ")\n"
        "print(json.dumps(result, ensure_ascii=False))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(worktree), str(worktree / "src"), env.get("PYTHONPATH", "")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = _run_subprocess(
        [sys.executable, "-c", code],
        cwd=worktree,
        input_text=json.dumps(payload, ensure_ascii=False),
        timeout=900,
        env=env,
    )
    command = f"{sys.executable} -c <fixed rsvp image eval>"
    if completed.returncode != 0:
        raise RuntimeError(_tail(completed.stderr or completed.stdout or "fixed eval failed"))
    result = json.loads(completed.stdout)
    result["research_track"] = {
        "track_id": track["track_id"],
        "direction": track["direction"],
        "split_salt": payload["split_salt"],
        "worktree": str(worktree),
    }
    write_json(root / "runs" / run_id / "result.json", result)
    best_score = _best_score_from_result(result)
    selected_score = _metric_from_result(result)
    robust_summary = {
        "split_count": 1,
        "best_test_balanced_accuracy_mean": best_score,
        "best_test_balanced_accuracy_std": 0.0 if best_score is not None else None,
        "selected_test_balanced_accuracy_mean": selected_score,
        "selected_test_balanced_accuracy_std": 0.0 if selected_score is not None else None,
    }
    return {
        "command": command,
        "run_id": run_id,
        "result_path": str(root / "runs" / run_id / "result.json"),
        "latest_result": result,
        "robust_summary": robust_summary,
        "stdout_tail": _tail(completed.stdout),
    }


def _rejected_edit_code_result(
    *,
    track: dict[str, Any],
    status: str,
    message: str,
    worktree: Path | None = None,
    branch: str | None = None,
    rollback_ref: str | None = None,
    touched_files: list[str] | None = None,
    researcher: dict[str, Any] | None = None,
    smoke: dict[str, Any] | None = None,
    trace_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "track_id": track.get("track_id"),
        "direction": track.get("direction"),
        "message": message,
        "worktree_path": str(worktree) if worktree else None,
        "worktree_branch": branch,
        "rollback_ref": rollback_ref,
        "commit": None,
        "diff_summary": "",
        "touched_files": touched_files or [],
        "install_commands": [],
        "smoke_command": (smoke or {}).get("command"),
        "smoke": smoke,
        "researcher": researcher,
        "trace_events": trace_events or [],
        "safety": {
            "executor_started": False,
            "raw_data_touched": any(_normalize_repo_path(path).startswith("data/raw/") for path in (touched_files or [])),
            "formal_manifest_written": False,
            "campaign_started": False,
        },
    }


def _run_edit_code_track(repo_root: Path, root: Path, track: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    editable_files = [
        _normalize_repo_path(item)
        for item in (track.get("editable_files") if isinstance(track.get("editable_files"), list) else DEFAULT_STRUCTURE_EDITABLE_FILES)
        if str(item).strip()
    ] or DEFAULT_STRUCTURE_EDITABLE_FILES
    smoke_command = str(track.get("smoke_command") or DEFAULT_STRUCTURE_SMOKE_COMMAND)
    trace_events: list[dict[str, Any]] = [
        _trace_event(
            actor="Executor",
            event_type="tool_call_start",
            action="create_structure_worktree",
            track=track,
            reason="为结构沙盒创建隔离 git worktree 和分支。",
            inputs={"editable_files": editable_files, "smoke_command": smoke_command},
            risk_flags=["code_change_candidate"],
        )
    ]
    missing_baseline = _release_baseline_preflight(repo_root, editable_files)
    if missing_baseline:
        message = "release baseline 缺少 git-tracked 文件：" + ", ".join(missing_baseline)
        trace_events.append(
            _trace_event(
                actor="Executor",
                event_type="tool_call_end",
                action="create_structure_worktree",
                track=track,
                reason=message,
                exit_code=1,
                stderr_tail=message,
                risk_flags=["code_change_candidate", "release_baseline_incomplete"],
            )
        )
        return _rejected_edit_code_result(
            track=track,
            status="rejected_release_baseline_preflight_failed",
            message=message,
            trace_events=trace_events,
        )
    try:
        worktree_info = _create_structure_worktree(repo_root, track)
    except Exception as exc:
        trace_events.append(
            _trace_event(
                actor="Executor",
                event_type="tool_call_end",
                action="create_structure_worktree",
                track=track,
                reason="结构沙盒 worktree 创建失败。",
                exit_code=1,
                stderr_tail=str(exc),
                risk_flags=["code_change_candidate"],
            )
        )
        return _rejected_edit_code_result(track=track, status="rejected_worktree_setup_failed", message=str(exc), trace_events=trace_events)

    worktree = Path(worktree_info["worktree"])
    branch = str(worktree_info["branch"])
    rollback_ref = str(worktree_info["rollback_ref"])
    trace_events.append(
        _trace_event(
            actor="Executor",
            event_type="tool_call_end",
            action="create_structure_worktree",
            track=track,
            reason="结构沙盒 worktree 已创建。",
            inputs={"worktree": str(worktree), "branch": branch, "rollback_ref": rollback_ref},
            exit_code=0,
            risk_flags=["code_change_candidate"],
        )
    )
    trace_events.append(
        _trace_event(
            actor="Executor",
            event_type="tool_call_start",
            action="run_structure_researcher",
            track=track,
            reason="调用结构研究员修改允许的结构文件；优先外部 coding agent，缺失时使用内置 patch worker。",
            command="AUTOBCI_STRUCTURE_SANDBOX_RUNNER, opencode run, codex exec, or builtin_patch_worker",
            inputs={"editable_files": editable_files},
            risk_flags=["code_change_candidate"],
        )
    )
    researcher = _run_structure_researcher(worktree, track, editable_files, smoke_command)
    trace_events.append(
        _trace_event(
            actor="Executor",
            event_type="tool_call_end",
            action="run_structure_researcher",
            track=track,
            reason="结构研究员调用结束。",
            command=researcher.get("command"),
            exit_code=int(researcher.get("exit_code") or 0),
            stdout_tail=str(researcher.get("stdout_tail") or ""),
            stderr_tail=str(researcher.get("stderr_tail") or ""),
            risk_flags=["code_change_candidate"],
        )
    )
    if int(researcher.get("exit_code") or 0) != 0:
        return _rejected_edit_code_result(
            track=track,
            status="rejected_researcher_failed",
            message=str(researcher.get("stderr_tail") or researcher.get("stdout_tail") or "structure researcher failed"),
            worktree=worktree,
            branch=branch,
            rollback_ref=rollback_ref,
            touched_files=_status_touched_files(worktree),
            researcher=researcher,
            trace_events=trace_events,
        )

    post_head = _git_stdout(worktree, "rev-parse", "HEAD")
    committed_files = _diff_touched_files(worktree, rollback_ref, post_head)
    dirty_files = _status_touched_files(worktree)
    touched_files = sorted(set(committed_files + dirty_files))
    if post_head == rollback_ref:
        return _rejected_edit_code_result(
            track=track,
            status="rejected_no_commit",
            message="structure researcher did not create a commit",
            worktree=worktree,
            branch=branch,
            rollback_ref=rollback_ref,
            touched_files=touched_files,
            researcher=researcher,
            trace_events=trace_events,
        )
    forbidden = [path for path in touched_files if not _is_allowed_edit(path, editable_files)]
    if forbidden:
        return _rejected_edit_code_result(
            track=track,
            status="rejected_forbidden_files",
            message="structure researcher touched files outside editable_files",
            worktree=worktree,
            branch=branch,
            rollback_ref=rollback_ref,
            touched_files=touched_files,
            researcher=researcher,
            trace_events=trace_events,
        )
    if dirty_files:
        return _rejected_edit_code_result(
            track=track,
            status="rejected_dirty_worktree",
            message="structure researcher left uncommitted changes",
            worktree=worktree,
            branch=branch,
            rollback_ref=rollback_ref,
            touched_files=touched_files,
            researcher=researcher,
            trace_events=trace_events,
        )

    trace_events.append(
        _trace_event(
            actor="Executor",
            event_type="artifact_written",
            action="structure_diff_created",
            track=track,
            reason="结构沙盒产生隔离 commit 和 diff。",
            inputs={"touched_files": touched_files, "commit": post_head, "branch": branch},
            artifact_refs=[str(worktree)],
            decision={"diff_summary": _diff_summary(worktree, rollback_ref, post_head)},
            risk_flags=["code_change_candidate"],
        )
    )
    trace_events.append(
        _trace_event(
            actor="Executor",
            event_type="tool_call_start",
            action="run_structure_smoke",
            track=track,
            reason="运行结构文件 smoke 检查。",
            command=smoke_command,
            risk_flags=["code_change_candidate"],
        )
    )
    smoke = _run_structure_smoke(worktree, smoke_command)
    trace_events.append(
        _trace_event(
            actor="Executor",
            event_type="tool_call_end",
            action="run_structure_smoke",
            track=track,
            reason="结构文件 smoke 检查结束。",
            command=smoke_command,
            exit_code=int(smoke.get("exit_code") or 0),
            stdout_tail=str(smoke.get("stdout_tail") or ""),
            stderr_tail=str(smoke.get("stderr_tail") or ""),
            risk_flags=["code_change_candidate"],
        )
    )
    if int(smoke.get("exit_code") or 0) != 0:
        return _rejected_edit_code_result(
            track=track,
            status="rejected_smoke_failed",
            message=str(smoke.get("stderr_tail") or smoke.get("stdout_tail") or "structure smoke failed"),
            worktree=worktree,
            branch=branch,
            rollback_ref=rollback_ref,
            touched_files=touched_files,
            researcher=researcher,
            smoke=smoke,
            trace_events=trace_events,
        )
    _cleanup_python_bytecode(worktree, touched_files)

    trace_events.append(
        _trace_event(
            actor="Evaluator",
            event_type="tool_call_start",
            action="run_fixed_eval",
            track=track,
            reason="用固定评估器评估结构沙盒候选。",
            command="python -c <fixed rsvp image eval>",
            risk_flags=["code_change_candidate"],
        )
    )
    try:
        fixed_eval = _run_structure_fixed_eval(repo_root, root, worktree, track, state)
    except Exception as exc:
        trace_events.append(
            _trace_event(
                actor="Evaluator",
                event_type="tool_call_end",
                action="run_fixed_eval",
                track=track,
                reason="固定评估失败。",
                command="python -c <fixed rsvp image eval>",
                exit_code=1,
                stderr_tail=str(exc),
                risk_flags=["code_change_candidate"],
            )
        )
        return _rejected_edit_code_result(
            track=track,
            status="rejected_fixed_eval_failed",
            message=str(exc),
            worktree=worktree,
            branch=branch,
            rollback_ref=rollback_ref,
            touched_files=touched_files,
            researcher=researcher,
            smoke=smoke,
            trace_events=trace_events,
        )
    trace_events.append(
        _trace_event(
            actor="Evaluator",
            event_type="tool_call_end",
            action="run_fixed_eval",
            track=track,
            reason="固定评估完成。",
            command=fixed_eval["command"],
            exit_code=0,
            stdout_tail=str(fixed_eval.get("stdout_tail") or ""),
            artifact_refs=[fixed_eval["result_path"]],
            decision=fixed_eval["robust_summary"],
            run_id=fixed_eval["run_id"],
            risk_flags=["code_change_candidate", "single_split"],
        )
    )

    return {
        "status": "completed_edit_code_runner",
        "track_id": track["track_id"],
        "direction": track["direction"],
        "worktree_path": str(worktree),
        "worktree_branch": branch,
        "rollback_ref": rollback_ref,
        "commit": post_head,
        "diff_summary": _diff_summary(worktree, rollback_ref, post_head),
        "touched_files": touched_files,
        "install_commands": [],
        "smoke_command": smoke_command,
        "smoke": smoke,
        "researcher": researcher,
        "fixed_eval_command": fixed_eval["command"],
        "run_ids": [fixed_eval["run_id"]],
        "result_paths": [fixed_eval["result_path"]],
        "robust_summary": fixed_eval["robust_summary"],
        "latest_result": fixed_eval["latest_result"],
        "trace_events": trace_events,
        "safety": {
            "executor_started": True,
            "raw_data_touched": False,
            "formal_manifest_written": False,
            "campaign_started": False,
        },
    }


def _execute_track(repo_root: Path, root: Path, track: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    action_type = str(track.get("action_type") or "")
    if action_type == "analysis_only":
        return _analysis_result(track, state)
    if action_type == "run_existing":
        return _run_existing_track(repo_root, root, track, state)
    if action_type == "edit_code":
        return _run_edit_code_track(repo_root, root, track, state)
    return {
        "status": "blocked_requires_isolated_worktree",
        "track_id": track.get("track_id"),
        "direction": track.get("direction"),
        "message": "此 track 需要隔离 worktree/分支执行代码或依赖改动；当前闭环先跳过，等待自治编辑器接入。",
        "safety": {
            "executor_started": False,
            "raw_data_touched": False,
            "formal_manifest_written": False,
            "campaign_started": False,
        },
    }


def _judge(track: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    robust = result.get("robust_summary") if isinstance(result.get("robust_summary"), dict) else {}
    robust_mean = robust.get("best_test_balanced_accuracy_mean")
    split_count = robust.get("split_count")
    best_score = result.get("per_run_best_test_balanced_accuracy")
    selected = result.get("selected_test_balanced_accuracy")
    if not isinstance(best_score, (int, float)):
        best_score = robust.get("best_test_balanced_accuracy_mean")
    if not isinstance(selected, (int, float)):
        selected = robust.get("selected_test_balanced_accuracy_mean")
    action_type = str(track.get("action_type") or "")
    direction = str(track.get("direction") or "")
    safety = result.get("safety") if isinstance(result.get("safety"), dict) else {}
    result_paths = [str(item) for item in (result.get("result_paths") if isinstance(result.get("result_paths"), list) else [])]
    risk_flags: list[str] = []
    rules_checked: list[dict[str, Any]] = []
    counter_evidence: list[str] = []
    if isinstance(split_count, int) and split_count < 3:
        risk_flags.append("single_split")
        counter_evidence.append("当前结果不是多划分稳健复核，不能作为当前最可信结果。")
    if isinstance(best_score, (int, float)) and isinstance(selected, (int, float)):
        gap = float(best_score) - float(selected)
        rules_checked.append({"rule": "selected_vs_per_run_best_gap", "passed": gap <= 0.05, "value": round(gap, 6)})
        if gap > 0.05:
            risk_flags.append("selected_vs_best_gap")
            counter_evidence.append("系统实际选择分数明显低于事后最高候选。")
    if "shortcut" in direction or "color" in direction:
        risk_flags.append("shortcut_risk_under_review")
        rules_checked.append({"rule": "shortcut_risk_reviewed", "passed": "shortcut" not in direction, "value": direction})
    if action_type == "edit_code":
        risk_flags.append("code_change_candidate")
        rules_checked.append({"rule": "code_change_isolated", "passed": bool(result.get("worktree_path")) and not bool(safety.get("raw_data_touched"))})
    if bool(safety.get("raw_data_touched")):
        risk_flags.append("raw_data_touched")
        counter_evidence.append("检测到 data/raw 风险，不能保留。")
    if str(result.get("status") or "").startswith(("rejected", "failed", "blocked")):
        risk_flags.append("execution_failed_or_blocked")
    if isinstance(split_count, int):
        rules_checked.append({"rule": "multi_split_minimum", "passed": split_count >= 3, "value": split_count})
    if isinstance(robust_mean, (int, float)):
        rules_checked.append({"rule": "robust_mean_threshold", "passed": float(robust_mean) >= 0.9, "value": round(float(robust_mean), 6)})
    rules_checked.append({"rule": "raw_data_read_only", "passed": not bool(safety.get("raw_data_touched"))})
    rules_checked.append({"rule": "formal_manifest_not_written", "passed": not bool(safety.get("formal_manifest_written"))})
    decision = "succeeded"
    reason = "track 已执行并写入 ledger。"
    if isinstance(robust_mean, (int, float)) and isinstance(split_count, int) and split_count >= 3 and robust_mean >= 0.9:
        decision = "promoted"
        reason = "多划分均值达到可提升候选，进入 robust accepted best 候选。"
    elif isinstance(best_score, (int, float)) and isinstance(selected, (int, float)) and best_score - selected > 0.05:
        decision = "rejected"
        reason = "系统 selected 明显低于 per-run best，当前选择策略不应推广。"
    elif str(result.get("status") or "").startswith("blocked"):
        decision = "rejected"
        reason = "需要隔离 worktree 的代码/依赖改动，当前执行器未直接落地。"
    elif str(result.get("status") or "").startswith(("rejected", "failed")):
        decision = "rejected"
        reason = str(result.get("message") or "结构沙盒执行失败或未通过安全检查。")
    return {
        "decision": decision,
        "reason": reason,
        "rules_checked": rules_checked,
        "risk_flags": sorted(set(risk_flags)),
        "evidence_refs": result_paths,
        "counter_evidence": counter_evidence,
        "human_gate_required": decision == "promoted" or action_type == "edit_code" or bool(risk_flags),
    }


def _ledger_row(track: dict[str, Any], result: dict[str, Any], judgment: dict[str, Any]) -> dict[str, Any]:
    return {
        "recorded_at": _utc_now(),
        "task_id": TASK_ID,
        "run_id": result.get("run_ids", [result.get("track_id")])[0] if isinstance(result.get("run_ids"), list) and result.get("run_ids") else result.get("track_id"),
        "track_id": track.get("track_id"),
        "title": track.get("title"),
        "hypothesis": track.get("hypothesis"),
        "direction": track.get("direction"),
        "action_type": track.get("action_type"),
        "runner": track.get("runner"),
        "novelty_signature": track.get("novelty_signature"),
        "expected_signal": track.get("expected_signal"),
        "stop_condition": track.get("stop_condition"),
        "result": result,
        "decision": judgment.get("decision"),
        "judgment_reason": judgment.get("reason"),
        "rules_checked": judgment.get("rules_checked") or [],
        "risk_flags": judgment.get("risk_flags") or [],
        "evidence_refs": judgment.get("evidence_refs") or [],
        "counter_evidence": judgment.get("counter_evidence") or [],
        "judgment_chain": [
            f"证据：{track.get('hypothesis')}",
            f"执行：{track.get('action_type')} / {track.get('runner')}",
            f"结果：{result.get('status')}",
            f"风险：{', '.join(judgment.get('risk_flags') or []) or '-'}",
            f"判断：{judgment.get('decision')} - {judgment.get('reason')}",
        ],
        "safety": result.get("safety"),
    }


def _balanced_accuracy_from_result_payload(result: dict[str, Any]) -> float | None:
    metrics = result.get("test_metrics") if isinstance(result.get("test_metrics"), dict) else {}
    value = metrics.get("balanced_accuracy")
    return float(value) if isinstance(value, (int, float)) else None


def _trajectory_row_from_ledger(row: dict[str, Any], index: int) -> dict[str, Any]:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    robust = result.get("robust_summary") if isinstance(result.get("robust_summary"), dict) else {}
    latest = result.get("latest_result") if isinstance(result.get("latest_result"), dict) else {}
    selected_score = robust.get("selected_test_balanced_accuracy_mean")
    if not isinstance(selected_score, (int, float)):
        selected_score = result.get("selected_test_balanced_accuracy")
    if not isinstance(selected_score, (int, float)):
        selected_score = _balanced_accuracy_from_result_payload(latest)

    best_score = robust.get("best_test_balanced_accuracy_mean")
    if not isinstance(best_score, (int, float)):
        best_score = result.get("per_run_best_test_balanced_accuracy")
    if not isinstance(best_score, (int, float)):
        best_score = _best_score_from_result(latest) if latest else None

    return {
        "index": index,
        "track_id": row.get("track_id"),
        "title": row.get("title"),
        "direction": row.get("direction"),
        "decision": row.get("decision"),
        "recorded_at": row.get("recorded_at"),
        "selected_test_balanced_accuracy": float(selected_score) if isinstance(selected_score, (int, float)) else None,
        "per_run_best_test_balanced_accuracy": float(best_score) if isinstance(best_score, (int, float)) else None,
        "split_count": robust.get("split_count") if isinstance(robust.get("split_count"), int) else None,
        "status": result.get("status"),
    }


def status_research_loop(repo_root: str | Path, *, task_id: str = TASK_ID) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    queue = _load_queue(root)
    ledger = read_jsonl(_ledger_path(root))
    events = read_jsonl(_events_path(root))
    active = read_json(_active_track_path(root), {})
    status = read_json(_status_path(root), {})
    active_summary = {}
    if isinstance(active, dict) and active:
        active_result = active.get("result") if isinstance(active.get("result"), dict) else {}
        active_summary = {
            "track_id": active.get("track_id"),
            "title": active.get("title"),
            "direction": active.get("direction"),
            "decision": active.get("decision"),
            "judgment_reason": active.get("judgment_reason"),
            "recorded_at": active.get("recorded_at"),
            "action_type": active.get("action_type"),
            "runner": active.get("runner"),
            "worktree_branch": active_result.get("worktree_branch"),
            "commit": active_result.get("commit"),
            "diff_summary": active_result.get("diff_summary"),
            "touched_files": active_result.get("touched_files") if isinstance(active_result.get("touched_files"), list) else [],
            "rollback_ref": active_result.get("rollback_ref"),
        }
    robust_candidates = []
    for row in ledger:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        robust = result.get("robust_summary") if isinstance(result.get("robust_summary"), dict) else {}
        mean = robust.get("best_test_balanced_accuracy_mean")
        if row.get("decision") == "promoted" and isinstance(mean, (int, float)):
            robust_candidates.append((float(mean), row))
    robust_best = max(robust_candidates, key=lambda item: item[0])[1] if robust_candidates else None
    return {
        "task_id": task_id,
        "available": root.exists(),
        "root": str(root),
        "phase": status.get("phase") if isinstance(status, dict) else None,
        "queue_count": len(queue),
        "queued_count": sum(1 for item in queue if item.get("status") == "queued"),
        "ledger_count": len(ledger),
        "trajectory": [_trajectory_row_from_ledger(row, index) for index, row in enumerate(ledger, start=1)],
        "active_track": active_summary,
        "last_ledger": ledger[-1] if ledger else None,
        "recent_events": events[-20:],
        "robust_accepted_best": {
            "track_id": robust_best.get("track_id"),
            "score": (robust_best.get("result") or {}).get("robust_summary", {}).get("best_test_balanced_accuracy_mean"),
            "direction": robust_best.get("direction"),
        }
        if robust_best
        else None,
        "stopped": bool(status.get("stopped")) if isinstance(status, dict) else False,
    }


def preview_research_step(
    repo_root: str | Path,
    *,
    task_id: str = TASK_ID,
    max_repeated_signature: int = 3,
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    root = loop_root(repo, task_id)
    _load_latest_state(repo)
    queue = ensure_queue(repo, task_id)
    ledger = read_jsonl(_ledger_path(root))
    repeated = _last_repeated_signature(ledger, max_repeated_signature)
    if repeated:
        return {"task_id": task_id, "status": "stalled", "reason": f"repeated novelty_signature detected: {repeated}"}
    track = next((item for item in queue if item.get("status") == "queued"), None)
    if track is None:
        return {"task_id": task_id, "status": "empty", "reason": "no queued tracks"}
    action_type = str(track.get("action_type") or "")
    return {
        "task_id": task_id,
        "status": "ready",
        "track": deepcopy(track),
        "ledger_count": len(ledger),
        "queued_count": sum(1 for item in queue if item.get("status") == "queued"),
        "gate_type": "edit_code_pre" if action_type == "edit_code" else "step_pre",
        "requires_confirmation": True,
    }


def step_research_loop(
    repo_root: str | Path,
    *,
    task_id: str = TASK_ID,
    max_repeated_signature: int = 3,
    only_track_id: str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    root = loop_root(repo, task_id)
    state = _load_latest_state(repo)
    only_track_id = str(only_track_id or "").strip() or None
    queue = _queue_for_step(repo, root, state, task_id=task_id, only_track_id=only_track_id)
    ledger = read_jsonl(_ledger_path(root))
    repeated = _last_repeated_signature(ledger, max_repeated_signature)
    if repeated:
        payload = {
            "task_id": task_id,
            "status": "stalled",
            "reason": f"repeated novelty_signature detected: {repeated}",
            "updated_at": _utc_now(),
        }
        write_json(_status_path(root), {"phase": "stalled", "updated_at": payload["updated_at"], "reason": payload["reason"]})
        return payload
    if only_track_id:
        track = next((item for item in queue if str(item.get("track_id") or "") == only_track_id), None)
        if track is None:
            payload = {
                "task_id": task_id,
                "status": "not_found",
                "reason": f"track not found: {only_track_id}",
                "updated_at": _utc_now(),
            }
            write_json(_status_path(root), {"phase": "not_found", "updated_at": payload["updated_at"], "reason": payload["reason"]})
            return payload
        if str(track.get("status") or "queued") != "queued":
            payload = {
                "task_id": task_id,
                "status": "not_queued",
                "reason": f"track is {track.get('status') or '-'}: {only_track_id}",
                "track": track,
                "updated_at": _utc_now(),
            }
            write_json(_status_path(root), {"phase": "not_queued", "updated_at": payload["updated_at"], "reason": payload["reason"]})
            return payload
    else:
        track = next((item for item in queue if item.get("status") == "queued"), None)
    if track is None:
        payload = {"task_id": task_id, "status": "empty", "reason": "no queued tracks", "updated_at": _utc_now()}
        write_json(_status_path(root), {"phase": "empty", "updated_at": payload["updated_at"]})
        return payload
    track = deepcopy(track)
    director_event = _trace_event(
        actor="Director",
        event_type="director_decision",
        action="select_active_track",
        track=track,
        reason="读取 Program、ledger 和研究队列后，选择下一条 active track。",
        inputs={
            "ledger_count": len(ledger),
            "queued_count": sum(1 for item in queue if item.get("status") == "queued"),
            "hypothesis": track.get("hypothesis"),
            "expected_signal": track.get("expected_signal"),
            "stop_condition": track.get("stop_condition"),
        },
        decision={"track_id": track.get("track_id"), "title": track.get("title"), "action_type": track.get("action_type")},
        risk_flags=["code_change_candidate"] if str(track.get("action_type") or "") == "edit_code" else [],
    )
    for item in queue:
        if item.get("track_id") == track.get("track_id"):
            item["status"] = "running"
            item["started_at"] = _utc_now()
    _write_queue(root, queue)
    write_json(_active_track_path(root), track)
    result = _execute_track(repo, root, track, state)
    judgment = _judge(track, result)
    trace_events = [director_event]
    trace_events.extend([item for item in (result.get("trace_events") if isinstance(result.get("trace_events"), list) else []) if isinstance(item, dict)])
    judge_event = _trace_event(
        actor="Judge",
        event_type="judge_decision",
        action="judge_track_result",
        track=track,
        reason=str(judgment.get("reason") or ""),
        decision={
            "decision": judgment.get("decision"),
            "rules_checked": judgment.get("rules_checked") or [],
            "counter_evidence": judgment.get("counter_evidence") or [],
            "human_gate_required": bool(judgment.get("human_gate_required")),
        },
        artifact_refs=[str(item) for item in (judgment.get("evidence_refs") or [])],
        risk_flags=[str(item) for item in (judgment.get("risk_flags") or [])],
    )
    trace_events.append(judge_event)
    trace_event_ids = _write_trace_events(root, trace_events)
    result["trace_event_ids"] = trace_event_ids
    judgment["trace_event_ids"] = trace_event_ids
    row = _ledger_row(track, result, judgment)
    write_jsonl_row(_ledger_path(root), row)
    for item in queue:
        if item.get("track_id") == track.get("track_id"):
            item["status"] = str(judgment.get("decision") or "succeeded")
            item["finished_at"] = row["recorded_at"]
            item["last_result_status"] = result.get("status")
    _write_queue(root, queue)
    write_json(_active_track_path(root), row)
    write_json(_status_path(root), {"phase": "idle", "updated_at": row["recorded_at"], "last_track_id": track.get("track_id")})
    return {
        "task_id": task_id,
        "status": "stepped",
        "track": track,
        "result": result,
        "judgment": judgment,
        "ledger_row": row,
        "events": trace_events,
    }


def run_research_loop(repo_root: str | Path, *, task_id: str = TASK_ID, max_steps: int = 1) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for _ in range(max(0, int(max_steps))):
        payload = step_research_loop(repo_root, task_id=task_id)
        steps.append(payload)
        if payload.get("status") in {"stalled", "empty", "stopped"}:
            break
    return {"task_id": task_id, "steps": steps, "status": status_research_loop(repo_root, task_id=task_id)}


def stop_research_loop(repo_root: str | Path, *, task_id: str = TASK_ID) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    payload = {"phase": "stopped", "stopped": True, "updated_at": _utc_now()}
    write_json(_status_path(root), payload)
    return {"task_id": task_id, "status": "stopped", "root": str(root)}


def explain_research_track(repo_root: str | Path, *, task_id: str = TASK_ID, track_id: str) -> dict[str, Any]:
    root = loop_root(repo_root, task_id)
    events = [item for item in read_jsonl(_events_path(root)) if str(item.get("track_id") or "") == str(track_id)]
    for row in reversed(read_jsonl(_ledger_path(root))):
        if str(row.get("track_id") or "") == str(track_id):
            return {
                "task_id": task_id,
                "track_id": track_id,
                "title": row.get("title"),
                "direction": row.get("direction"),
                "decision": row.get("decision"),
                "judgment_reason": row.get("judgment_reason"),
                "rules_checked": row.get("rules_checked") or [],
                "risk_flags": row.get("risk_flags") or [],
                "evidence_refs": row.get("evidence_refs") or [],
                "counter_evidence": row.get("counter_evidence") or [],
                "judgment_chain": row.get("judgment_chain") or [],
                "events": events,
                "result": row.get("result"),
            }
    return {"task_id": task_id, "track_id": track_id, "judgment_chain": [], "events": events, "error": "track not found in ledger"}
