from __future__ import annotations

from functools import lru_cache
from typing import Any

from .paths import AutoBciControlPlanePaths
from .runtime_store import read_json


MODEL_FAMILY_LABELS = {
    "linear_logistic": "Linear Logistic",
    "feature_lstm": "Feature LSTM",
    "feature_gru": "Feature GRU",
    "feature_tcn": "Feature TCN",
    "gait_phase_rule": "步态规则",
    "feature_cnn_lstm": "Feature CNN-LSTM",
    "feature_state_space_lite": "Feature State-Space Lite",
    "feature_conformer_lite": "Feature Conformer Lite",
    "xgboost": "XGBoost",
    "tree_xgboost": "XGBoost",
    "ridge": "Ridge",
    "extra_trees": "Extra Trees",
    "catboost": "CatBoost",
    "kinematics_only": "运动学历史",
    "hybrid_input": "混合输入",
}

SERIES_CLASS_LABELS = {
    "mainline_brain": "主线脑电",
    "structure": "结构化研究线",
    "same_session_reference": "同试次参考线",
    "control": "控制实验",
}

DIRECTION_FOCUS_LABELS = {
    "pure_brain_breakthrough": "优先：纯脑电突破",
    "structure_probe": "辅助：结构解释",
    "same_session_reference": "辅助：同试次参考",
    "baseline_guard": "护栏：主线守线",
    "control_reference": "护栏：控制对照",
}


def normalize_algorithm_family(value: Any, *, track_id: str = "", input_mode_label: str = "") -> str:
    track = str(track_id or "").strip()
    input_mode = str(input_mode_label or "").strip()
    lowered = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    track_lower = track.lower()
    if track == "kinematics_only_baseline" or "只用运动学历史" in input_mode:
        return "kinematics_only"
    if "linear_logistic" in track_lower or track_lower.endswith("_logistic"):
        return "linear_logistic"
    if "xgboost" in track_lower or "tree_xgboost" in track_lower or track_lower.endswith("_xgb"):
        return "xgboost"
    if track.startswith("tree_calibration_"):
        return "extra_trees"
    if track == "hybrid_brain_plus_kinematics" or "脑电 + 运动学历史" in input_mode:
        return "hybrid_input"
    if "feature_gru" in track_lower or track_lower.endswith("_gru"):
        return "feature_gru"
    if "feature_tcn" in track_lower or track_lower.endswith("_tcn"):
        return "feature_tcn"
    if "feature_cnn_lstm" in track_lower or "cnn_lstm" in track_lower:
        return "feature_cnn_lstm"
    if "feature_state_space_lite" in track_lower or "state_space_lite" in track_lower:
        return "feature_state_space_lite"
    if "feature_conformer_lite" in track_lower or "conformer_lite" in track_lower:
        return "feature_conformer_lite"
    if "feature_lstm" in track_lower or track_lower.endswith("_lstm") or "lstm" in track_lower:
        return "feature_lstm"
    if "gait_phase" in track_lower:
        return "gait_phase_rule"
    if "ridge" in track_lower:
        return "ridge"
    if lowered in {"tree_xgboost", "xgboost"}:
        return "xgboost"
    if lowered in {"linear_logistic", "logistic_regression", "logistic"}:
        return "linear_logistic"
    if lowered in {"gait_phase_rule", "gait_phase_rule_based", "gait_phase_label_engineering"}:
        return "gait_phase_rule"
    if lowered in MODEL_FAMILY_LABELS:
        return lowered
    if "gru" in lowered:
        return "feature_gru"
    if "tcn" in lowered:
        return "feature_tcn"
    if "cnn_lstm" in lowered:
        return "feature_cnn_lstm"
    if "state_space_lite" in lowered or "statespacelite" in lowered:
        return "feature_state_space_lite"
    if "conformer_lite" in lowered or "conformerlite" in lowered:
        return "feature_conformer_lite"
    if "lstm" in lowered:
        return "feature_lstm"
    if "ridge" in lowered:
        return "ridge"
    if "logistic" in lowered:
        return "linear_logistic"
    return lowered or "unmapped"


def humanize_algorithm_family(value: Any) -> str:
    normalized = normalize_algorithm_family(value)
    return MODEL_FAMILY_LABELS.get(normalized, str(value or normalized or "-"))


def humanize_series_class(value: Any) -> str:
    key = str(value or "").strip().lower()
    return SERIES_CLASS_LABELS.get(key, key or "-")


@lru_cache(maxsize=16)
def load_direction_specs(direction_tags_path: str) -> dict[str, Any]:
    payload = read_json(__import__("pathlib").Path(direction_tags_path), {})
    directions = []
    for raw in payload.get("directions", []) if isinstance(payload, dict) else []:
        if not isinstance(raw, dict):
            continue
        tag = str(raw.get("tag") or "").strip().upper()
        if not tag:
            continue
        directions.append(
            {
                "tag": tag,
                "label": str(raw.get("label") or tag).strip() or tag,
                "summary": str(raw.get("summary") or raw.get("label") or tag).strip() or tag,
                "focus": str(raw.get("focus") or "").strip() or "pure_brain_breakthrough",
                "priority": int(raw.get("priority") or 999),
                "topic_ids": [str(item).strip() for item in raw.get("topic_ids", []) if str(item).strip()],
                "track_ids": [str(item).strip() for item in raw.get("track_ids", []) if str(item).strip()],
                "track_prefixes": [str(item).strip() for item in raw.get("track_prefixes", []) if str(item).strip()],
                "algorithm_families": [
                    normalize_algorithm_family(item)
                    for item in raw.get("algorithm_families", [])
                    if str(item).strip()
                ],
            }
        )
    directions.sort(key=lambda item: (item["priority"], item["tag"]))
    return {
        "priority_statement": str(payload.get("priority_statement") or "").strip() if isinstance(payload, dict) else "",
        "flow_note": str(payload.get("flow_note") or "").strip() if isinstance(payload, dict) else "",
        "directions": directions,
    }


def resolve_direction_spec(
    paths: AutoBciControlPlanePaths,
    *,
    track_id: str,
    topic_id: str = "",
    algorithm_family: str = "",
) -> dict[str, Any] | None:
    specs = load_direction_specs(str(paths.direction_tags)).get("directions", [])
    matches: list[dict[str, Any]] = []
    for spec in specs:
        if track_id and track_id in spec.get("track_ids", []):
            matches.append(spec)
            continue
        if topic_id and topic_id in spec.get("topic_ids", []):
            matches.append(spec)
            continue
        if track_id and any(track_id.startswith(prefix) for prefix in spec.get("track_prefixes", [])):
            matches.append(spec)
            continue
        if algorithm_family and algorithm_family in spec.get("algorithm_families", []):
            matches.append(spec)
    if not matches:
        return None
    matches.sort(key=lambda item: (item["priority"], item["tag"]))
    return matches[0]


def humanize_direction_focus(value: Any) -> str:
    key = str(value or "").strip().lower()
    return DIRECTION_FOCUS_LABELS.get(key, key or "-")
