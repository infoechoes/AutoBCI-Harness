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

RSVP_SHIP_TRAIN_ITEMS = ["rsvp_ship_crossmodal_train_manifest"]
RSVP_SHIP_VAL_ITEMS = ["rsvp_ship_crossmodal_val_manifest"]
RSVP_SHIP_TEST_ITEMS = ["rsvp_ship_crossmodal_test_manifest"]
RSVP_SHIP_IMAGE_TRAIN_ITEMS = ["rsvp_ship_image_train_manifest"]
RSVP_SHIP_IMAGE_VAL_ITEMS = ["rsvp_ship_image_val_manifest"]
RSVP_SHIP_IMAGE_TEST_ITEMS = ["rsvp_ship_image_test_manifest"]


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


def looks_like_rsvp_ship_request(user_request: str) -> bool:
    normalized = _as_text(user_request).lower()
    raw = _as_text(user_request)
    has_ship_target = any(token in raw for token in ("船", "船只", "ship", "target"))
    has_cross_modal_context = any(
        token in raw or token in normalized
        for token in ("图像", "图片", "脑电", "EEG", "eeg", "RSVP", "rsvp", "跨模态")
    )
    return has_ship_target and has_cross_modal_context


def looks_like_rsvp_ship_image_only_request(user_request: str) -> bool:
    normalized = _as_text(user_request).lower()
    raw = _as_text(user_request)
    has_ship_target = any(token in raw for token in ("船", "船只", "ship", "target"))
    has_image_context = any(token in raw or token in normalized for token in ("图像", "图片", "image", "rsvp"))
    has_binary_label_context = any(
        token in raw or token in normalized
        for token in (
            "非船",
            "不是船",
            "not-ship",
            "not ship",
            "non-ship",
            "nontarget",
            "二分类",
            "分类",
            "是不是船",
        )
    )
    has_cross_modal_or_eeg_context = any(
        token in raw or token in normalized
        for token in (
            "脑电",
            "eeg",
            "跨模态",
            "cross-modal",
            "cross modal",
            "用脑电",
            "脑电识别",
        )
    )
    has_image_only_boundary = any(
        token in raw or token in normalized
        for token in (
            "纯图像",
            "只用图像",
            "仅用图像",
            "只看图像",
            "不用脑电",
            "先不做脑电",
            "不使用脑电",
            "image-only",
            "image only",
        )
    )
    explicit_image_only = has_ship_target and has_image_context and has_image_only_boundary
    implied_image_only = has_ship_target and has_image_context and has_binary_label_context and not has_cross_modal_or_eeg_context
    return explicit_image_only or implied_image_only


def build_rsvp_ship_image_only_program_draft(user_request: str) -> dict[str, Any]:
    normalized_request = _as_text(user_request)
    return {
        "program_id": "rsvp_ship_image_only_v0",
        "version": "0.1",
        "status": "draft",
        "created_at": utcnow(),
        "created_by": "autobci-intake",
        "source_request_summary": normalized_request,
        "research_goal": {
            "statement": "先做纯图像 ship / not-ship 二分类，不使用脑电",
            "task_type": "image_binary_classification",
            "scientific_question": "在当前 RSVP 船只图片数据里，图像本身能否稳定区分 ship / not-ship，并为后续脑电路线提供同条件参考线。",
        },
        "data_boundary": {
            "dataset_name": "Downloads/RSVP跨模态数据",
            "raw_data_policy": "read_only",
            "allowed_sessions": {
                "train": list(RSVP_SHIP_IMAGE_TRAIN_ITEMS),
                "val": list(RSVP_SHIP_IMAGE_VAL_ITEMS),
                "test": list(RSVP_SHIP_IMAGE_TEST_ITEMS),
            },
            "forbidden_data_access": [
                "modify_downloaded_images",
                "read_eeg_files_for_this_image_only_program",
                "tune_on_test_set",
            ],
        },
        "label_definition": {
            "source": "folder_labels: target=ship, nontarget=non-ship/background",
            "known_risks": [
                "当前只冻结纯图像任务，不能据此声称脑电路线效果",
                "图像分类可能利用低层亮度、颜色或构图线索，不能直接等价于人眼语义识别",
                "需要先做数据审计，确认图片标签、重复样本和数据划分没有泄漏",
            ],
            "acceptance_note": "本契约只允许纯图像 ship / not-ship 二分类；脑电比较必须另开或修订 Program。",
        },
        "split_policy": {
            "unit": "image_or_stimulus_manifest",
            "frozen_train_sessions": list(RSVP_SHIP_IMAGE_TRAIN_ITEMS),
            "frozen_val_sessions": list(RSVP_SHIP_IMAGE_VAL_ITEMS),
            "frozen_test_sessions": list(RSVP_SHIP_IMAGE_TEST_ITEMS),
            "amendment_required_for": [
                "changing_label_source",
                "changing_split",
                "adding_eeg_input",
                "selecting_params_on_test",
            ],
        },
        "metrics": {
            "primary": "test_balanced_accuracy",
            "secondary": [
                "val_balanced_accuracy",
                "macro_f1",
                "per_class_recall",
                "confusion_matrix",
            ],
            "minimum_report_fields": [
                "dataset_audit",
                "per_split_counts",
                "image_metrics",
                "artifact_paths",
            ],
        },
        "search_space": {
            "windows_seconds": [0],
            "lags_ms": [0],
            "allowed_model_families": [
                "image_logistic_baseline",
                "image_hog_linear_probe",
                "image_color_histogram_logistic",
                "image_tiny_cnn_probe",
                "image_transfer_embedding",
            ],
            "allowed_feature_families": ["image_pixels_or_embeddings"],
        },
        "forbidden_actions": [
            "change_task_type",
            "change_primary_metric",
            "change_split_without_amendment",
            "modify_downloads_source_data",
            "read_eeg_for_image_only_program",
            "claim_eeg_vs_image_comparison",
            "overwrite_existing_result",
        ],
        "artifact_contract": {
            "required_outputs": [
                "program_snapshot",
                "dataset_audit_json",
                "split_manifest",
                "image_result_json",
                "confusion_matrix",
                "guard_decisions",
            ],
        },
        "amendment_policy": {
            "requires_human_approval": [
                "task_type",
                "split_policy",
                "metrics.primary",
                "label_definition.source",
                "search_space",
                "adding_eeg_input",
            ],
            "request_message_type": "amendment_request",
        },
        "uncertainties": [
            "下载目录仍需审计：纯图像任务只确认图片和标签，不读取脑电文件。",
            "如果后续要比较脑电，需要有同一刺激序列或可追溯事件表，不能直接拿不同来源结果相减。",
        ],
    }


def build_rsvp_ship_program_draft(user_request: str) -> dict[str, Any]:
    normalized_request = _as_text(user_request)
    return {
        "program_id": "rsvp_ship_crossmodal_v0",
        "version": "0.1",
        "status": "draft",
        "created_at": utcnow(),
        "created_by": "autobci-intake",
        "source_request_summary": normalized_request,
        "research_goal": {
            "statement": "比较图像识别船与脑电识别船的二分类效果",
            "task_type": "cross_modal_binary_classification",
            "scientific_question": "同一 RSVP 船只目标任务里，图像本身和脑电响应各自能否稳定区分 target / nontarget，以及脑电路线落后多少。",
        },
        "data_boundary": {
            "dataset_name": "Downloads/RSVP跨模态数据",
            "raw_data_policy": "read_only",
            "allowed_sessions": {
                "train": list(RSVP_SHIP_TRAIN_ITEMS),
                "val": list(RSVP_SHIP_VAL_ITEMS),
                "test": list(RSVP_SHIP_TEST_ITEMS),
            },
            "forbidden_data_access": [
                "modify_downloaded_images",
                "invent_eeg_trials",
                "compare_modalities_without_matched_trials",
                "tune_on_test_set",
            ],
        },
        "label_definition": {
            "source": "folder_labels: target=ship, nontarget=non-ship/background",
            "known_risks": [
                "当前下载包只确认到图片，尚未确认脑电文件和刺激事件表",
                "图像分类可能利用低层亮度或构图线索，不能直接等价于人眼语义识别",
                "图像和脑电必须使用同一刺激序列或可追溯事件表才可公平比较",
            ],
            "acceptance_note": "本契约先冻结任务边界；正式运行前必须生成数据审计和固定数据划分。",
        },
        "split_policy": {
            "unit": "stimulus_or_trial_manifest",
            "frozen_train_sessions": list(RSVP_SHIP_TRAIN_ITEMS),
            "frozen_val_sessions": list(RSVP_SHIP_VAL_ITEMS),
            "frozen_test_sessions": list(RSVP_SHIP_TEST_ITEMS),
            "amendment_required_for": [
                "changing_label_source",
                "changing_split",
                "using_unmatched_image_and_eeg_trials",
                "selecting_params_on_test",
            ],
        },
        "metrics": {
            "primary": "test_balanced_accuracy",
            "secondary": [
                "image_balanced_accuracy",
                "eeg_balanced_accuracy",
                "macro_f1",
                "per_class_recall",
                "confusion_matrix",
                "missing_modality_report",
            ],
            "minimum_report_fields": [
                "dataset_audit",
                "per_split_counts",
                "per_modality_metrics",
                "matched_trial_policy",
                "artifact_paths",
            ],
        },
        "search_space": {
            "windows_seconds": [0.2, 0.4, 0.6, 0.8],
            "lags_ms": [0],
            "allowed_model_families": [
                "image_logistic_baseline",
                "image_transfer_embedding",
                "eeg_erp_logistic",
                "eeg_shallow_convnet",
            ],
            "allowed_feature_families": [
                "image_pixels_or_embeddings",
                "eeg_epoch_mean",
                "eeg_epoch_bandpower",
            ],
        },
        "forbidden_actions": [
            "change_task_type",
            "change_primary_metric",
            "change_split_without_amendment",
            "modify_downloads_source_data",
            "invent_missing_eeg_data",
            "claim_eeg_vs_image_comparison_without_matched_trials",
            "overwrite_existing_result",
        ],
        "artifact_contract": {
            "required_outputs": [
                "program_snapshot",
                "dataset_audit_json",
                "split_manifest",
                "image_result_json",
                "eeg_result_json_or_missing_data_report",
                "comparison_report",
                "guard_decisions",
            ],
        },
        "amendment_policy": {
            "requires_human_approval": [
                "task_type",
                "split_policy",
                "metrics.primary",
                "label_definition.source",
                "search_space",
            ],
            "request_message_type": "amendment_request",
        },
        "uncertainties": [
            "下载目录当前需要先审计：若没有 EEG 文件和事件表，脑电路线只能标记为缺数据阻塞。",
            "图像基线和脑电基线必须共享刺激编号或事件映射，不能用两个来源不一致的数据集直接比高低。",
        ],
    }


def build_program_draft_from_request(user_request: str) -> dict[str, Any]:
    if looks_like_rsvp_ship_image_only_request(user_request):
        return build_rsvp_ship_image_only_program_draft(user_request)
    if looks_like_rsvp_ship_request(user_request):
        return build_rsvp_ship_program_draft(user_request)
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
