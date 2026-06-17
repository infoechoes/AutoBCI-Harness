from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import AutoBciControlPlanePaths
from .runtime_store import write_json_atomic, write_text_atomic


class ProgramContractError(ValueError):
    pass


PROGRAM_STATUSES = {"draft", "frozen", "amended", "archived"}

DEFAULT_TRAIN_SESSIONS = [
    "walk_20240717_01",
    "walk_20240717_03",
    "walk_20240717_04",
    "walk_20240717_05",
    "walk_20240717_06",
    "walk_20240717_07",
    "walk_20240717_08",
    "walk_20240717_09",
    "walk_20240717_10",
    "walk_20240717_14",
    "walk_20240719_01",
    "walk_20240719_02",
    "walk_20240719_03",
    "walk_20240719_04",
    "walk_20240719_05",
    "walk_20240719_06",
    "walk_20240719_08",
    "walk_20240719_09",
]
DEFAULT_VAL_SESSIONS = ["walk_20240717_12", "walk_20240719_07"]
DEFAULT_TEST_SESSIONS = ["walk_20240717_16", "walk_20240719_10"]

def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ProgramContractError(f"{key} 必须是对象")
    return value


def _require_nonempty_text(payload: dict[str, Any], key: str, *, label: str | None = None) -> str:
    text = _as_text(payload.get(key))
    if not text:
        raise ProgramContractError(f"{label or key} 不能为空")
    return text


def _require_nonempty_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ProgramContractError(f"{key} 必须是非空列表")
    return value


def validate_program_contract(payload: dict[str, Any]) -> dict[str, Any]:
    program = copy.deepcopy(payload)
    _require_nonempty_text(program, "program_id")
    _require_nonempty_text(program, "version")
    status = _require_nonempty_text(program, "status")
    if status not in PROGRAM_STATUSES:
        raise ProgramContractError(f"status 不合法：{status}")

    research_goal = _require_dict(program, "research_goal")
    _require_nonempty_text(research_goal, "statement")
    _require_nonempty_text(research_goal, "task_type", label="research_goal.task_type")
    _require_nonempty_text(research_goal, "scientific_question")

    data_boundary = _require_dict(program, "data_boundary")
    _require_nonempty_text(data_boundary, "dataset_name")
    _require_nonempty_text(data_boundary, "raw_data_policy")
    allowed_sessions = _require_dict(data_boundary, "allowed_sessions")
    _require_nonempty_list(allowed_sessions, "train")
    _require_nonempty_list(allowed_sessions, "val")
    _require_nonempty_list(allowed_sessions, "test")
    _require_nonempty_list(data_boundary, "forbidden_data_access")

    label_definition = _require_dict(program, "label_definition")
    _require_nonempty_text(label_definition, "source")
    _require_nonempty_list(label_definition, "known_risks")
    _require_nonempty_text(label_definition, "acceptance_note")

    split_policy = _require_dict(program, "split_policy")
    _require_nonempty_text(split_policy, "unit", label="split_policy.unit")
    _require_nonempty_list(split_policy, "frozen_train_sessions")
    _require_nonempty_list(split_policy, "frozen_val_sessions")
    _require_nonempty_list(split_policy, "frozen_test_sessions")
    _require_nonempty_list(split_policy, "amendment_required_for")

    metrics = _require_dict(program, "metrics")
    _require_nonempty_text(metrics, "primary")
    _require_nonempty_list(metrics, "secondary")
    _require_nonempty_list(metrics, "minimum_report_fields")

    search_space = _require_dict(program, "search_space")
    _require_nonempty_list(search_space, "windows_seconds")
    _require_nonempty_list(search_space, "lags_ms")
    _require_nonempty_list(search_space, "allowed_model_families")
    _require_nonempty_list(search_space, "allowed_feature_families")

    _require_nonempty_list(program, "forbidden_actions")
    artifact_contract = _require_dict(program, "artifact_contract")
    _require_nonempty_list(artifact_contract, "required_outputs")
    return program


def build_gait_phase_program_draft(user_request: str) -> dict[str, Any]:
    normalized_request = _as_text(user_request)
    return {
        "program_id": "gait_phase_binary_v0",
        "version": "0.1",
        "status": "draft",
        "created_at": utcnow(),
        "created_by": "autobci-intake",
        "source_request_summary": normalized_request,
        "research_goal": {
            "statement": "用脑电判断步态 support / swing 二分类是否稳定可解",
            "task_type": "binary_classification",
            "scientific_question": "当前标签定义下，EEG 是否含有可复现的步态相位信息",
        },
        "data_boundary": {
            "dataset_name": "gait_phase_clean64",
            "raw_data_policy": "read_only",
            "allowed_sessions": {
                "train": list(DEFAULT_TRAIN_SESSIONS),
                "val": list(DEFAULT_VAL_SESSIONS),
                "test": list(DEFAULT_TEST_SESSIONS),
            },
            "forbidden_data_access": ["modify_raw_data", "tune_on_test_set"],
        },
        "label_definition": {
            "source": "gait_phase_reference_provisional_v1_0717_0719",
            "known_risks": [
                "short_intervals",
                "pending_manual_review",
                "historical_safe_band_filtering_dependency",
            ],
            "acceptance_note": "本任务先验证当前 operational label，不声明真实生理标签已可靠。",
        },
        "split_policy": {
            "unit": "session",
            "frozen_train_sessions": list(DEFAULT_TRAIN_SESSIONS),
            "frozen_val_sessions": list(DEFAULT_VAL_SESSIONS),
            "frozen_test_sessions": list(DEFAULT_TEST_SESSIONS),
            "amendment_required_for": ["changing_split", "merging_train_val_test", "selecting_params_on_test"],
        },
        "metrics": {
            "primary": "test_balanced_accuracy",
            "secondary": ["val_balanced_accuracy", "support_recall", "swing_recall", "confusion_matrix"],
            "minimum_report_fields": ["per_split_counts", "per_class_recall", "artifact_paths"],
        },
        "search_space": {
            "windows_seconds": [0.5, 1.0, 2.0, 3.0],
            "lags_ms": [0, 100, 250, 500],
            "allowed_model_families": ["baseline_logistic", "feature_tcn", "feature_gru"],
            "allowed_feature_families": ["lmp+hg_power"],
        },
        "forbidden_actions": [
            "change_task_type",
            "change_primary_metric",
            "change_split_without_amendment",
            "modify_raw_data",
            "overwrite_existing_result",
            "read_director_scratchpad_from_judge",
        ],
        "artifact_contract": {
            "required_outputs": [
                "result_json",
                "run_log",
                "metrics_summary",
                "confusion_matrix",
                "program_snapshot",
                "guard_decisions",
            ],
        },
        "amendment_policy": {
            "requires_human_approval": ["task_type", "split_policy", "metrics.primary", "search_space"],
            "request_message_type": "amendment_request",
        },
        "uncertainties": [
            "v1 标签包含大量极短 swing interval，需要 Judge 在复评中标注风险。",
            "历史 0.7375 高分依赖 historical safe-band filtering，不能直接当作宽口径稳定最好结果。",
        ],
    }


def build_program_draft_from_request(user_request: str) -> dict[str, Any]:
    return build_gait_phase_program_draft(user_request)


def render_program_markdown(program: dict[str, Any]) -> str:
    validated = validate_program_contract(program)
    goal = validated["research_goal"]
    metrics = validated["metrics"]
    label = validated["label_definition"]
    split = validated["split_policy"]
    search = validated["search_space"]
    uncertainties = validated.get("uncertainties") if isinstance(validated.get("uncertainties"), list) else []
    lines = [
        f"# Program: {validated['program_id']}",
        "",
        f"- version: {validated['version']}",
        f"- status: {validated['status']}",
        f"- task_type: {goal['task_type']}",
        f"- primary_metric: {metrics['primary']}",
        "",
        "## 研究目标",
        str(goal["statement"]),
        "",
        "## 数据划分",
        f"- unit: {split['unit']}",
        f"- train sessions: {len(split['frozen_train_sessions'])}",
        f"- val sessions: {', '.join(split['frozen_val_sessions'])}",
        f"- test sessions: {', '.join(split['frozen_test_sessions'])}",
        "",
        "## 标签定义与风险",
        f"- source: {label['source']}",
        f"- known risks: {', '.join(label['known_risks'])}",
        f"- acceptance note: {label['acceptance_note']}",
        "- 注意：当前 v1 标签存在大量短 interval 风险，历史高分依赖 safe-band filtering。",
        "",
        "## 搜索空间",
        f"- windows_seconds: {search['windows_seconds']}",
        f"- lags_ms: {search['lags_ms']}",
        f"- model_families: {', '.join(search['allowed_model_families'])}",
        "",
        "## 禁区",
        *[f"- {item}" for item in validated["forbidden_actions"]],
    ]
    if uncertainties:
        lines.extend(["", "## 不确定性", *[f"- {item}" for item in uncertainties]])
    return "\n".join(lines) + "\n"


def freeze_program_contract(
    paths: AutoBciControlPlanePaths,
    draft: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    frozen = validate_program_contract({**copy.deepcopy(draft), "status": "frozen"})
    frozen["frozen_at"] = utcnow()
    program_dir = paths.programs_dir / str(frozen["program_id"])
    json_path = program_dir / "program.json"
    markdown_path = program_dir / "Program.md"
    refs = [str(json_path), str(markdown_path)]
    write_json_atomic(json_path, frozen)
    write_text_atomic(markdown_path, render_program_markdown(frozen))
    if run_id:
        snapshot_path = paths.program_snapshots_dir / f"{run_id}.json"
        write_json_atomic(snapshot_path, frozen)
        refs.append(str(snapshot_path))
    return frozen, refs
