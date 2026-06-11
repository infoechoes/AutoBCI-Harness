from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paths import AutoBciControlPlanePaths, get_control_plane_paths
from .registry import (
    humanize_algorithm_family,
    humanize_direction_focus,
    humanize_series_class,
    normalize_algorithm_family,
    resolve_direction_spec,
)
from .messages import read_recent_messages
from .runtime_store import read_json, read_jsonl, read_latest_packet, read_topics_inbox


_framework_benchmark_cache: dict[str, Any] | None = None
_framework_benchmark_mtime: float = 0.0


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _format_metric(value: Any, digits: int = 4) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def _normalize_candidate_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for item in values:
        if isinstance(item, dict):
            family = item.get("candidate_model_family") or item.get("family") or item.get("algorithm_family")
            label = _as_text(family)
            if label:
                normalized.append(label)
            continue
        label = _as_text(item)
        if label:
            normalized.append(label)
    return normalized


def _latest_program_state(paths: AutoBciControlPlanePaths) -> dict[str, Any]:
    if not paths.programs_dir.exists():
        return {}
    candidates = sorted(
        [path for path in paths.programs_dir.glob("*/program.json") if path.is_file()],
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        return {}
    program = read_json(candidates[0], {})
    if not isinstance(program, dict):
        return {}
    return {
        "program_id": _as_text(program.get("program_id")),
        "version": _as_text(program.get("version")),
        "status": _as_text(program.get("status")),
        "task_type": _as_text((program.get("research_goal") or {}).get("task_type") if isinstance(program.get("research_goal"), dict) else ""),
        "primary_metric": _as_text((program.get("metrics") or {}).get("primary") if isinstance(program.get("metrics"), dict) else ""),
        "path": str(candidates[0]),
        "frozen_at": _as_text(program.get("frozen_at")),
    }


def infer_method_variant_label(track_state: dict[str, Any]) -> str:
    explicit = _as_text(track_state.get("method_variant_label"))
    track_id = _as_text(track_state.get("track_id"))
    topic_id = _as_text(track_state.get("topic_id"))
    if explicit and explicit != track_id and not explicit.endswith("_mainline"):
        return explicit
    if topic_id == "gait_phase_eeg_classification" or "gait_phase_eeg" in track_id:
        return "步态二分类"
    if topic_id == "gait_phase_label_engineering" or "gait_phase" in track_id:
        return "步态标签工程"
    if track_id == "feature_gru_mainline":
        return "标准主线"
    if track_id == "feature_tcn_mainline":
        return "标准主线"
    if track_id == "canonical_mainline_tree_xgboost":
        return "标准主线"
    if track_id.startswith("phase_conditioned_"):
        return "phase 条件版"
    if track_id.startswith("phase_aware_"):
        return "phase-aware 特征"
    if track_id.startswith("dmd_sdm_"):
        return "DMD/sDM 特征"
    if track_id == "kinematics_only_baseline":
        return "只用运动学历史，不用脑电"
    if track_id == "hybrid_brain_plus_kinematics":
        return "脑电 + 运动学历史"
    if track_id.startswith("tree_calibration_"):
        return "树模型校准（Extra Trees）"
    return "标准主线"


def infer_input_mode_label(track_state: dict[str, Any]) -> str:
    explicit = _as_text(track_state.get("input_mode_label"))
    if explicit:
        return explicit
    track_id = _as_text(track_state.get("track_id"))
    topic_id = _as_text(track_state.get("topic_id"))
    if topic_id == "gait_phase_eeg_classification" or "gait_phase_eeg" in track_id:
        return "只用脑电"
    if topic_id == "gait_phase_label_engineering" or "gait_phase" in track_id:
        return "只用运动学标记"
    if track_id == "kinematics_only_baseline":
        return "只用运动学历史，不用脑电"
    if track_id == "hybrid_brain_plus_kinematics":
        return "脑电 + 运动学历史"
    return "只用脑电"


def infer_series_class(track_state: dict[str, Any]) -> str:
    explicit = _as_text(track_state.get("series_class")).lower()
    if explicit:
        return explicit
    track_id = _as_text(track_state.get("track_id"))
    topic_id = _as_text(track_state.get("topic_id"))
    if topic_id == "gait_phase_eeg_classification" or "gait_phase_eeg" in track_id:
        return "mainline_brain"
    if topic_id == "gait_phase_label_engineering" or "gait_phase" in track_id:
        return "structure"
    if track_id.startswith("relative_origin_xyz_upper_bound"):
        return "same_session_reference"
    if track_id.startswith("relative_origin_xyz"):
        return "structure"
    if track_id in {"kinematics_only_baseline", "hybrid_brain_plus_kinematics"} or track_id.startswith("tree_calibration_"):
        return "control"
    return "mainline_brain"


def infer_stage_label(track_state: dict[str, Any]) -> str:
    explicit = _as_text(track_state.get("campaign_stage_label") or track_state.get("stage_label"))
    if explicit:
        return explicit
    track_id = _as_text(track_state.get("track_id"))
    if track_id.endswith("_scout"):
        return "scout"
    if "_formal" in track_id:
        return "formal"
    return _as_text(track_state.get("status_label")) or "-"


def method_short_label(item: dict[str, Any]) -> str:
    family = _as_text(item.get("algorithm_family"))
    variant = _as_text(item.get("method_variant_label"))
    if family == "feature_gru":
        return "GRU主线"
    if family == "feature_tcn":
        return "TCN主线"
    if family == "feature_cnn_lstm":
        return "CNN-LSTM"
    if family == "feature_state_space_lite":
        return "SSM-Lite"
    if family == "feature_conformer_lite":
        return "Conf-Lite"
    if family == "feature_lstm" and "phase" in variant.lower():
        return "LSTM-Phase"
    if family == "xgboost" and "dmd" in variant.lower():
        return "XGB-DMD"
    if family == "ridge" and "dmd" in variant.lower():
        return "Ridge-DMD"
    if family == "kinematics_only":
        return "运动学"
    if family == "hybrid_input":
        return "混合"
    if family == "extra_trees":
        return "ET-校准"
    return f"{humanize_algorithm_family(family).split()[0]}主线"


def build_method_summary(track_state: dict[str, Any], *, paths: AutoBciControlPlanePaths) -> dict[str, Any]:
    track_id = _as_text(track_state.get("track_id"))
    topic_id = _as_text(track_state.get("topic_id"))
    method_variant = infer_method_variant_label(track_state)
    input_mode = infer_input_mode_label(track_state)
    algorithm_family = normalize_algorithm_family(
        track_state.get("algorithm_family") or track_state.get("runner_family") or track_state.get("model_family"),
        track_id=track_id,
        input_mode_label=input_mode,
    )
    series_class = infer_series_class(track_state)
    promotable_raw = track_state.get("promotable")
    promotable = bool(promotable_raw) if promotable_raw is not None else series_class != "control"
    direction_spec = resolve_direction_spec(
        paths,
        track_id=track_id,
        topic_id=topic_id,
        algorithm_family=algorithm_family,
    )
    val_r = _as_float(track_state.get("latest_val_primary_metric"))
    test_r = _as_float(track_state.get("latest_test_primary_metric"))
    val_rmse = _as_float(track_state.get("latest_val_rmse"))
    updated_at = _as_text(track_state.get("updated_at"))
    return {
        "track_id": track_id,
        "topic_id": topic_id,
        "algorithm_family": algorithm_family,
        "algorithm_label": humanize_algorithm_family(algorithm_family),
        "method_variant_label": method_variant,
        "input_mode_label": input_mode,
        "series_class": series_class,
        "series_class_label": humanize_series_class(series_class),
        "promotable": promotable,
        "method_display_label": f"{humanize_algorithm_family(algorithm_family)} · {method_variant}",
        "method_short_label": method_short_label(
            {
                "algorithm_family": algorithm_family,
                "method_variant_label": method_variant,
            }
        ),
        "status_label": "控制实验，不进入主线晋升" if not promotable else "可进入主线晋升",
        "stage_label": infer_stage_label(track_state),
        "latest_val_r": val_r,
        "latest_val_r_label": _format_metric(val_r),
        "latest_test_r": test_r,
        "latest_test_r_label": _format_metric(test_r),
        "latest_val_rmse": val_rmse,
        "latest_val_rmse_label": _format_metric(val_rmse, 3),
        "latest_recorded_at": updated_at,
        "direction_tag": direction_spec.get("tag") if direction_spec else None,
        "direction_label": direction_spec.get("label") if direction_spec else None,
        "direction_focus_label": humanize_direction_focus(direction_spec.get("focus")) if direction_spec else None,
        "is_control": series_class == "control",
    }


def _is_moonshot_item(item: dict[str, Any]) -> bool:
    if _as_text(item.get("topic_id")) == "same_session_pure_brain_moonshot":
        return True
    return _as_text(item.get("track_id")).startswith("moonshot_upper_bound_")


def build_moonshot_scoreboard(
    method_summaries: list[dict[str, Any]],
    *,
    target: float | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scoreboard = [
        item
        for item in method_summaries
        if _is_moonshot_item(item) and not item.get("is_control") and _as_text(item.get("input_mode_label")) == "只用脑电"
    ]
    scoreboard.sort(
        key=lambda item: (
            -(_as_float(item.get("latest_val_r")) or -1e9),
            -(_as_float(item.get("latest_test_r")) or -1e9),
            _as_float(item.get("latest_val_rmse")) or 1e9,
        )
    )
    best = scoreboard[0] if scoreboard else None
    best_val = _as_float(best.get("latest_val_r")) if best else None
    gap = None if target is None or best_val is None else max(target - best_val, 0.0)
    return scoreboard[:10], {
        "moonshot_best_val_r": best_val,
        "moonshot_best_val_r_label": _format_metric(best_val),
        "moonshot_best_track_id": best.get("track_id") if best else "",
        "moonshot_best_method_display_label": best.get("method_display_label") if best else "",
        "moonshot_gap_to_target": gap,
        "moonshot_gap_to_target_label": _format_metric(gap),
    }


def _sort_method_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        stamp = _as_text(item.get("latest_recorded_at"))
        return (0 if item.get("promotable") else 1, f"{'9999' if not stamp else ''}{stamp}")

    return sorted(items, key=sort_key)


def build_algorithm_family_bests(method_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in method_summaries:
        grouped[_as_text(item.get("algorithm_family"))].append(item)

    bests: list[dict[str, Any]] = []
    for family, items in grouped.items():
        ranked = sorted(
            items,
            key=lambda item: (
                -(_as_float(item.get("latest_val_r")) or -1e9),
                0 if item.get("promotable") else 1,
                _as_text(item.get("latest_recorded_at")),
            ),
        )
        best = ranked[0]
        bests.append(
            {
                "algorithm_family": family,
                "algorithm_label": best.get("algorithm_label"),
                "best_val_r": best.get("latest_val_r"),
                "best_val_r_label": best.get("latest_val_r_label"),
                "best_test_r": best.get("latest_test_r"),
                "best_test_r_label": best.get("latest_test_r_label"),
                "best_val_rmse": best.get("latest_val_rmse"),
                "best_val_rmse_label": best.get("latest_val_rmse_label"),
                "best_track_id": best.get("track_id"),
                "best_method_variant_label": best.get("method_variant_label"),
                "best_input_mode_label": best.get("input_mode_label"),
                "best_series_class_label": best.get("series_class_label"),
                "best_promotable": bool(best.get("promotable")),
                "is_control_best": bool(best.get("is_control")),
                "best_method_display_label": best.get("method_display_label"),
            }
        )
    return sorted(bests, key=lambda item: _as_text(item.get("algorithm_label")))


def _manifest_track_map(paths: AutoBciControlPlanePaths) -> dict[str, dict[str, Any]]:
    manifest = read_json(paths.track_manifest, {})
    tracks = manifest.get("tracks", []) if isinstance(manifest, dict) else []
    return {
        _as_text(item.get("track_id")): item
        for item in tracks
        if isinstance(item, dict) and _as_text(item.get("track_id"))
    }


def _latest_timestamp(values: list[Any]) -> str:
    parsed = []
    for value in values:
        stamp = _as_text(value)
        if not stamp:
            continue
        normalized = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
        try:
            parsed_dt = datetime.fromisoformat(normalized)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            parsed.append(parsed_dt.astimezone(timezone.utc))
        except ValueError:
            continue
    if not parsed:
        return ""
    latest = max(parsed).astimezone(timezone.utc).replace(microsecond=0)
    return latest.isoformat().replace("+00:00", "Z")


def _utcnow_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    stamp = _as_text(value)
    if not stamp:
        return None
    normalized = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_framework_benchmark(paths: AutoBciControlPlanePaths) -> dict[str, Any] | None:
    global _framework_benchmark_cache, _framework_benchmark_mtime

    ledger_paths = [
        path
        for path in (
            paths.experiment_ledger,
            paths.repo_root / "tools" / "autoresearch" / "experiment_ledger.jsonl",
        )
        if path.exists()
    ]
    if not ledger_paths:
        return None

    current_mtime = max(path.stat().st_mtime for path in ledger_paths)
    if _framework_benchmark_cache is not None and current_mtime <= _framework_benchmark_mtime:
        return _framework_benchmark_cache

    scripts_dir = paths.repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    try:
        from benchmark_framework_scheduling import compute_scheduling_metrics, load_ledger
    except ImportError:
        return None

    all_rows: list[dict[str, Any]] = []
    for path in ledger_paths:
        all_rows.extend(load_ledger(path))
    if not all_rows:
        return None

    metrics = compute_scheduling_metrics(all_rows)
    diversity = metrics.get("direction_diversity", {})
    breakthroughs = metrics.get("breakthrough_efficiency", {})
    stagnation = metrics.get("stagnation", {})

    timestamps = [_parse_timestamp(row.get("recorded_at")) for row in all_rows]
    parsed_timestamps = sorted(ts for ts in timestamps if ts is not None)
    autonomous_minutes = 0.0
    if len(parsed_timestamps) >= 2:
        session_start = parsed_timestamps[0]
        previous = parsed_timestamps[0]
        longest_session = timedelta(0)
        for current in parsed_timestamps[1:]:
            if current - previous > timedelta(hours=2):
                longest_session = max(longest_session, previous - session_start)
                session_start = current
            previous = current
        longest_session = max(longest_session, previous - session_start)
        autonomous_minutes = longest_session.total_seconds() / 60.0

    direction_switches = 0
    previous_family: str | None = None
    for row in sorted(all_rows, key=lambda item: _as_text(item.get("recorded_at"))):
        track_id = _as_text(row.get("track_id")).lower()
        family = None
        for token in ("cnn_lstm", "state_space", "conformer", "tcn", "gru", "lstm", "ridge", "xgboost"):
            if token in track_id:
                family = token
                break
        if family and previous_family and family != previous_family:
            direction_switches += 1
        if family:
            previous_family = family

    result = {
        "total_iterations": metrics.get("total_iterations", 0),
        "time_span_hours": metrics.get("time_span_hours", 0),
        "diversity_index": diversity.get("diversity_index", 0),
        "unique_families": diversity.get("unique_families", 0),
        "breakthrough_rate": breakthroughs.get("breakthrough_rate", 0),
        "breakthrough_count": breakthroughs.get("breakthrough_count", 0),
        "cost_per_breakthrough": breakthroughs.get("cost_per_breakthrough"),
        "max_dry_streak": stagnation.get("max_dry_streak", 0),
        "max_stagnation_hours": stagnation.get("max_stagnation_hours", 0),
        "direction_switches": direction_switches,
        "autonomous_duration_minutes": autonomous_minutes,
        "iterations_per_hour": metrics.get("iterations_per_hour", 0),
    }
    _framework_benchmark_cache = result
    _framework_benchmark_mtime = current_mtime
    return result


def _is_cross_session_mainline_row(row: dict[str, Any]) -> bool:
    experiment_track = _as_text(row.get("experiment_track") or row.get("evaluation_mode")).lower()
    if experiment_track in {"cross_session_mainline", "canonical_mainline"}:
        return True
    if experiment_track:
        return False
    dataset_name = _as_text(row.get("dataset_name")).lower()
    if "upper_bound" in dataset_name or "same_session" in dataset_name:
        return False
    track_id = _as_text(row.get("track_id")).lower()
    return bool(track_id) and "upper_bound" not in track_id


def _extract_mainline_val_metric(row: dict[str, Any]) -> float | None:
    for key in ("formal_val_primary_metric", "val_primary_metric"):
        value = _as_float(row.get(key))
        if value is not None:
            return value

    metrics = row.get("final_metrics")
    if isinstance(metrics, dict):
        for key in ("formal_val_primary_metric", "val_primary_metric", "val_r_zero"):
            value = _as_float(metrics.get(key))
            if value is not None:
                return value

    smoke_metrics = row.get("smoke_metrics")
    if isinstance(smoke_metrics, dict):
        for key in ("formal_val_primary_metric", "val_primary_metric", "val_r_zero"):
            value = _as_float(smoke_metrics.get(key))
            if value is not None:
                return value

    raw_metrics = row.get("metrics")
    if isinstance(raw_metrics, dict):
        for key in ("val_primary_metric", "formal_val_primary_metric", "val_r_zero"):
            value = _as_float(raw_metrics.get(key))
            if value is not None:
                return value

    return None


def compute_mainline_stagnation(paths: AutoBciControlPlanePaths) -> dict[str, Any]:
    ledger_rows = read_jsonl(paths.experiment_ledger)
    candidates: list[tuple[datetime, float]] = []
    for row in ledger_rows:
        if not isinstance(row, dict) or not _is_cross_session_mainline_row(row):
            continue
        recorded_at = _parse_timestamp(row.get("recorded_at"))
        metric = _extract_mainline_val_metric(row)
        if recorded_at is None or metric is None:
            continue
        candidates.append((recorded_at, metric))

    if not candidates:
        return {
            "days_without_breakthrough": None,
            "stagnation_level": "unknown",
            "last_breakthrough_at": "",
            "latest_mainline_val_metric": None,
        }

    candidates.sort(key=lambda item: item[0])
    running_best: float | None = None
    last_breakthrough_at: datetime | None = None
    latest_metric: float | None = None
    for recorded_at, metric in candidates:
        latest_metric = metric
        if running_best is None or metric > running_best:
            running_best = metric
            last_breakthrough_at = recorded_at

    if last_breakthrough_at is None:
        return {
            "days_without_breakthrough": None,
            "stagnation_level": "unknown",
            "last_breakthrough_at": "",
            "latest_mainline_val_metric": latest_metric,
        }

    days_without_breakthrough = max((_utcnow_datetime() - last_breakthrough_at).days, 0)
    if days_without_breakthrough >= 3:
        stagnation_level = "stagnant"
    elif days_without_breakthrough >= 2:
        stagnation_level = "slowing"
    else:
        stagnation_level = "healthy"

    return {
        "days_without_breakthrough": days_without_breakthrough,
        "stagnation_level": stagnation_level,
        "last_breakthrough_at": last_breakthrough_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "latest_mainline_val_metric": latest_metric,
    }


def _topic_handoff_summary(item: dict[str, Any]) -> dict[str, Any]:
    handoff = item.get("structured_handoff")
    return {
        "topic_id": _as_text(item.get("topic_id")),
        "materialization_state": _as_text(item.get("materialization_state")),
        "materialized_track_id": _as_text(item.get("materialized_track_id")),
        "materialized_run_id": _as_text(item.get("materialized_run_id")),
        "materialized_smoke_path": _as_text(item.get("materialized_smoke_path")),
        "thinking_heartbeat_at": _as_text(item.get("thinking_heartbeat_at")),
        "last_retrieval_at": _as_text(item.get("last_retrieval_at")),
        "last_decision_at": _as_text(item.get("last_decision_at")),
        "last_judgment_at": _as_text(item.get("last_judgment_at")),
        "last_materialization_at": _as_text(item.get("last_materialization_at")),
        "last_smoke_at": _as_text(item.get("last_smoke_at")),
        "last_activity_at": _as_text(item.get("last_activity_at")),
        "stale_reason_codes": list(item.get("stale_reason_codes") or []),
        "pivot_reason_codes": list(item.get("pivot_reason_codes") or []),
        "structured_handoff": dict(handoff) if isinstance(handoff, dict) else {},
        "search_budget_state": dict(item.get("search_budget_state") or {}),
        "tool_usage_summary": dict(item.get("tool_usage_summary") or {}),
    }


def build_status_snapshot(paths: AutoBciControlPlanePaths | None = None) -> dict[str, Any]:
    resolved = paths or get_control_plane_paths()
    runtime = read_json(resolved.runtime_state, {})
    status = read_json(resolved.autoresearch_status, {})
    stagnation = compute_mainline_stagnation(resolved)
    track_states = status.get("track_states", []) if isinstance(status, dict) else []
    method_summaries = [
        build_method_summary(item, paths=resolved)
        for item in track_states
        if isinstance(item, dict) and _as_text(item.get("track_id"))
    ]
    method_by_track = {item["track_id"]: item for item in method_summaries}
    manifest_track_map = _manifest_track_map(resolved)
    upcoming_queue = [
        method_by_track.get(track_id)
        or build_method_summary(dict(manifest_item), paths=resolved)
        for track_id, manifest_item in manifest_track_map.items()
    ]
    roadmap = list(upcoming_queue)
    recent = sorted(method_summaries, key=lambda item: _as_text(item.get("latest_recorded_at")), reverse=True)
    moonshot_target = _as_float(runtime.get("moonshot_target"))
    moonshot_scope_label = _as_text(runtime.get("moonshot_scope_label"))
    moonshot_scoreboard, moonshot_summary = build_moonshot_scoreboard(method_summaries, target=moonshot_target)
    topics = sorted(
        read_topics_inbox(resolved.topics_inbox),
        key=lambda item: (
            -(float(item.get("priority") or 0.0)),
            _as_text(item.get("last_decision_at")),
        ),
    )[:10]
    latest_retrieval_packet = read_latest_packet(resolved.retrieval_packets_dir)
    latest_decision_packet = read_latest_packet(resolved.decision_packets_dir)
    latest_judgment_updates = list(reversed(read_jsonl(resolved.judgment_updates)[-5:]))
    recent_control_events = list(reversed(read_jsonl(resolved.control_events)[-8:]))
    program_state = _latest_program_state(resolved)
    recent_messages = read_recent_messages(resolved.messages_ledger, limit=8)
    current_direction_tags = runtime.get("current_direction_tags") or []
    if not current_direction_tags:
        active_track_id = _as_text(status.get("active_track_id"))
        active_method = method_by_track.get(active_track_id)
        if active_method and active_method.get("direction_tag"):
            current_direction_tags = [active_method["direction_tag"]]
    topic_handoff_summaries = [_topic_handoff_summary(item) for item in topics]
    recommended_incubation = dict(runtime.get("recommended_incubation") or {}) if isinstance(runtime.get("recommended_incubation"), dict) else {}
    active_incubation_campaigns = [
        dict(item)
        for item in (runtime.get("active_incubation_campaigns") or [])
        if isinstance(item, dict)
    ]
    thinking_overview = {
        "last_retrieval_at": _latest_timestamp(
            [
                _as_text(latest_retrieval_packet.get("recorded_at")),
                *[_as_text(item.get("last_retrieval_at")) for item in topics],
            ]
        ),
        "last_decision_at": _latest_timestamp(
            [
                _as_text(latest_decision_packet.get("recorded_at")),
                *[_as_text(item.get("last_decision_at")) for item in topics],
            ]
        ),
        "last_judgment_at": _latest_timestamp(
            [
                *[_as_text(item.get("recorded_at")) for item in latest_judgment_updates],
                *[_as_text(item.get("last_judgment_at")) for item in topics],
            ]
        ),
        "last_materialization_at": _latest_timestamp([_as_text(item.get("last_materialization_at")) for item in topics]),
        "last_smoke_at": _latest_timestamp([_as_text(item.get("last_smoke_at")) for item in topics]),
        "thinking_heartbeat_at": _latest_timestamp([_as_text(item.get("thinking_heartbeat_at")) for item in topics]),
        "stale_topic_count": sum(1 for item in topics if item.get("stale_reason_codes")),
        "pivot_topic_count": sum(1 for item in topics if item.get("pivot_reason_codes")),
        "search_only_topic_count": sum(
            1
            for item in topics
            if _as_text(item.get("materialization_state")) in {"search_only", "materialized_pending_smoke"}
        ),
        "pending_materialization_count": sum(
            1
            for item in topics
            if _as_text(item.get("materialization_state")) == "materialized_pending_smoke"
        ),
        "days_without_breakthrough": stagnation.get("days_without_breakthrough"),
        "stagnation_level": stagnation.get("stagnation_level"),
        "last_breakthrough_at": stagnation.get("last_breakthrough_at"),
    }
    automation_state = {
        "stagnation_level": stagnation.get("stagnation_level"),
        "days_without_breakthrough": stagnation.get("days_without_breakthrough"),
        "last_auto_pivot_at": _as_text(runtime.get("last_auto_pivot_at")),
        "active_incubation_track_id": _as_text(runtime.get("active_incubation_track_id")),
    }
    snapshot = {
        "repo_root": str(resolved.repo_root),
        "dashboard_url": resolved.dashboard_url,
        "campaign_id": _as_text(status.get("campaign_id")),
        "stage": _as_text(status.get("stage")),
        "campaign_mode": _as_text(status.get("campaign_mode")),
        "current_track_id": _as_text(status.get("active_track_id")),
        "agent_status": _as_text(runtime.get("agent_status") or runtime.get("autonomous_research_status") or "idle"),
        "current_task": _as_text(runtime.get("current_task") or runtime.get("last_autonomous_task")),
        "current_candidates": _normalize_candidate_strings(runtime.get("current_candidates") or runtime.get("last_autonomous_candidates") or []),
        "current_worktree": _as_text(runtime.get("current_worktree") or runtime.get("last_autonomous_worktree")),
        "validation_summary": _as_text(runtime.get("validation_summary") or runtime.get("last_autonomous_validation_summary")),
        "promoted_track_ids": list(runtime.get("promoted_track_ids") or runtime.get("last_autonomous_promoted_track_ids") or []),
        "current_direction_tags": list(current_direction_tags),
        "last_research_judgment_update": _as_text(runtime.get("last_research_judgment_update")),
        "mainline_promotion_status": _as_text(runtime.get("mainline_promotion_status")),
        "mainline_promotion_reason": _as_text(runtime.get("mainline_promotion_reason")),
        "recent_method_summaries": recent[:10],
        "upcoming_queue_method_summaries": upcoming_queue[:10],
        "roadmap_method_summaries": roadmap[:10],
        "algorithm_family_bests": build_algorithm_family_bests(method_summaries),
        "moonshot_target": moonshot_target,
        "moonshot_scope_label": moonshot_scope_label,
        "moonshot_scoreboard": moonshot_scoreboard,
        "topics": topics,
        "latest_retrieval_packet": latest_retrieval_packet,
        "latest_decision_packet": latest_decision_packet,
        "latest_judgment_updates": latest_judgment_updates,
        "recent_control_events": recent_control_events,
        "program_state": program_state,
        "recent_messages": recent_messages,
        "topic_handoff_summaries": topic_handoff_summaries,
        "thinking_overview": thinking_overview,
        "automation_state": automation_state,
        "framework_benchmark": _build_framework_benchmark(resolved),
        "recommended_incubation": recommended_incubation,
        "active_incubation_campaigns": active_incubation_campaigns,
        "runtime_state": runtime,
        "autoresearch_status": status,
    }
    snapshot.update(moonshot_summary)
    return snapshot
