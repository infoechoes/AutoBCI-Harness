from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bci_autoresearch.platform_support import default_cache_root, detached_process_kwargs, is_windows, venv_python_path

from .client_api import build_status_snapshot, compute_mainline_stagnation
from .paths import AUTOBCI_ROOT_ENV, DEFAULT_CACHE_ROOT_ENV, AutoBciControlPlanePaths, get_control_plane_paths
from .runtime_store import (
    append_hypothesis_log,
    append_jsonl,
    append_judgment_update,
    read_json,
    read_topics_inbox,
    write_decision_packet,
    write_json_atomic,
    write_retrieval_packet,
    write_topics_inbox,
)
from .thinking import (
    build_decision_packet,
    build_hypothesis_entry,
    build_judgment_update,
    build_retrieval_packet,
    build_topics,
)


DEFAULT_MAX_ITERATIONS = 8
DEFAULT_PATIENCE = 2
DEFAULT_SUPERVISION_HOURS = 72.0

MOONSHOT_TARGET = 0.6
MOONSHOT_SCOPE_LABEL = "同试次纯脑电 8 关节平均相关系数"
MOONSHOT_TOPIC_ID = "same_session_pure_brain_moonshot"
MOONSHOT_ULTRASCOUT_DATASET = "configs/datasets/walk_matched_v1_64clean_joints_upper_bound_ultrascout.yaml"
MOONSHOT_FORMAL_DATASET = "configs/datasets/walk_matched_v1_64clean_joints_upper_bound.yaml"
MOONSHOT_CANDIDATES = [
    "feature_lstm",
    "feature_gru",
    "feature_tcn",
    "feature_cnn_lstm",
    "feature_state_space_lite",
    "feature_conformer_lite",
]
MOONSHOT_CANDIDATE_REASONS = {
    "feature_lstm": "保留 Feature LSTM 作为同试次纯脑电序列基线，验证 phase_state 是否继续稳定增益。",
    "feature_gru": "Feature GRU 当前是最有希望的纯脑电新家族之一，应继续保留在 scout 首批。",
    "feature_tcn": "Feature TCN 并行友好、严格因果，适合作为轻量卷积时序对照。",
    "feature_cnn_lstm": "CNN-LSTM 先用因果卷积压局部时频模式，再交给 LSTM 聚合，适合同步冲击同试次上限。",
    "feature_state_space_lite": "State-Space Lite 用更便宜的递推状态混合器测试是否能改善跨窗口记忆而不拉高成本。",
    "feature_conformer_lite": "Conformer Lite 用轻量因果注意力测试更强的时序重加权是否能在同试次口径拉高均值。",
}
MOONSHOT_TRACK_SPECS = [
    ("feature_lstm", "lmp+hg_power+phase_state"),
    ("feature_gru", "lmp+hg_power+phase_state"),
    ("feature_tcn", "lmp+hg_power+phase_state"),
    ("feature_lstm", "hg_power+phase_state"),
    ("feature_gru", "hg_power+phase_state"),
    ("feature_tcn", "hg_power+phase_state"),
    ("feature_lstm", "lmp+hg_power"),
    ("feature_gru", "lmp+hg_power"),
    ("feature_tcn", "lmp+hg_power"),
    ("feature_cnn_lstm", "lmp+hg_power+phase_state"),
    ("feature_state_space_lite", "lmp+hg_power+phase_state"),
    ("feature_conformer_lite", "lmp+hg_power+phase_state"),
]

INCUBATION_SMOKE_DATASET = "configs/datasets/walk_matched_v1_64clean_joints_smoke.yaml"
INCUBATION_FORMAL_DATASET = "configs/datasets/walk_matched_v1_64clean_joints.yaml"
INCUBATION_CANDIDATES = [
    "feature_cnn_lstm",
    "feature_state_space_lite",
    "feature_conformer_lite",
]


class ControlPlaneError(RuntimeError):
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _slugify(value: str, fallback: str = "autobci-task") -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:64] or fallback


def _pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _runtime_state(paths: AutoBciControlPlanePaths) -> dict[str, Any]:
    return read_json(paths.runtime_state, {})


def _write_runtime_state(paths: AutoBciControlPlanePaths, payload: dict[str, Any]) -> None:
    payload["updated_at"] = _utcnow()
    write_json_atomic(paths.runtime_state, payload)


def _signal_pid(pid: int, sig: signal.Signals) -> bool:
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    return True


def _is_moonshot_task(task_text: str) -> bool:
    lowered = task_text.lower()
    keywords = (
        "same-session",
        "same session",
        "upper-bound",
        "upper bound",
        "同试次",
        "0.6",
        "moonshot",
    )
    return any(keyword in lowered for keyword in keywords)


def _feature_sequence_script(model_family: str) -> str:
    scripts = {
        "feature_lstm": "scripts/train_feature_lstm.py",
        "feature_gru": "scripts/train_feature_gru.py",
        "feature_tcn": "scripts/train_feature_tcn.py",
        "feature_cnn_lstm": "scripts/train_feature_cnn_lstm.py",
        "feature_state_space_lite": "scripts/train_feature_state_space_lite.py",
        "feature_conformer_lite": "scripts/train_feature_conformer_lite.py",
    }
    try:
        return scripts[model_family]
    except KeyError as exc:
        raise ControlPlaneError(f"未知的纯脑电 moonshot 模型家族：{model_family}") from exc


def _humanize_family(model_family: str) -> str:
    labels = {
        "feature_lstm": "Feature LSTM",
        "feature_gru": "Feature GRU",
        "feature_tcn": "Feature TCN",
        "feature_cnn_lstm": "Feature CNN-LSTM",
        "feature_state_space_lite": "Feature State-Space Lite",
        "feature_conformer_lite": "Feature Conformer Lite",
    }
    return labels.get(model_family, model_family)


def _feature_variant_suffix(feature_family: str) -> str:
    return (
        feature_family.replace("hg_power", "hg")
        .replace("+", "_")
        .replace("-", "_")
    )


def _parse_timestamp(value: Any) -> datetime | None:
    text = _as_text(value)
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _incubation_topic_id(model_family: str) -> str:
    return f"incubation_{model_family}_probe"


def _incubation_track_id(model_family: str, *, now: datetime) -> str:
    return f"{_incubation_topic_id(model_family)}_{now.strftime('%Y%m%d%H%M%S')}"


def _incubation_campaign_id(mission_id: str, model_family: str) -> str:
    return f"{mission_id}-incubation-{model_family.replace('_', '-')}"


def _manifest_track_ids(paths: AutoBciControlPlanePaths) -> list[str]:
    manifest = read_json(paths.track_manifest, {})
    tracks = manifest.get("tracks", []) if isinstance(manifest, dict) else []
    return [
        _as_text(item.get("track_id"))
        for item in tracks
        if isinstance(item, dict) and _as_text(item.get("track_id"))
    ]


def _recent_incubation_activity_exists(topics: list[dict[str, Any]], *, now: datetime) -> bool:
    cutoff = now - timedelta(hours=24)
    for item in topics:
        if _as_text(item.get("scope_label")) != "incubation":
            continue
        for field_name in ("last_materialization_at", "last_smoke_at"):
            parsed = _parse_timestamp(item.get(field_name))
            if parsed and parsed >= cutoff:
                return True
    return False


def _active_incubation_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in topics
        if _as_text(item.get("scope_label")) == "incubation"
        and _as_text(item.get("status")) in {"running", "queued", "runnable"}
        and _as_text(item.get("materialization_state")) == "materialized_pending_smoke"
    ]


def _attempted_incubation_families(topics: list[dict[str, Any]]) -> set[str]:
    families: set[str] = set()
    for item in topics:
        if _as_text(item.get("scope_label")) != "incubation":
            continue
        topic_id = _as_text(item.get("topic_id"))
        for family in INCUBATION_CANDIDATES:
            if topic_id == _incubation_topic_id(family):
                families.add(family)
                break
    return families


def _next_incubation_family(topics: list[dict[str, Any]]) -> str | None:
    attempted = _attempted_incubation_families(topics)
    for family in INCUBATION_CANDIDATES:
        if family not in attempted:
            return family
    return None


def _build_incubation_track(model_family: str, *, track_id: str) -> dict[str, Any]:
    family_label = _humanize_family(model_family)
    script = _feature_sequence_script(model_family)
    common_args = (
        f"{script} "
        "--feature-family lmp+hg_power "
        "--feature-reducers mean "
        "--signal-preprocess car_notch_bandpass "
        "--target-axes xyz "
        "--hidden-size 64 --num-layers 1 "
        "--feature-bin-ms 100.0 "
        "--seed 0 --final-eval "
    )
    return {
        "track_id": track_id,
        "track_goal": f"在跨试次主线上用 {family_label} 做一条最便宜的新方向 smoke，先验证它能否真正进入 runnable 层。",
        "promotion_target": "canonical_mainline",
        "smoke_command": (
            ".venv/bin/python "
            f"{common_args}"
            f"--dataset-config {INCUBATION_SMOKE_DATASET} "
            "--epochs 4 --batch-size 64 --patience 2"
        ),
        "formal_command": (
            ".venv/bin/python "
            f"{common_args}"
            f"--dataset-config {INCUBATION_FORMAL_DATASET} "
            "--epochs 12 --batch-size 64 --patience 3"
        ),
        "allowed_change_scope": ["scripts", "src/bci_autoresearch/models", "src/bci_autoresearch/features"],
        "internet_research_enabled": False,
        "track_origin": "incubation",
        "force_fresh_thread": True,
    }


def _upsert_incubation_topic(
    paths: AutoBciControlPlanePaths,
    *,
    model_family: str,
    track_id: str,
    launched_at: str,
) -> dict[str, Any]:
    topic_id = _incubation_topic_id(model_family)
    family_label = _humanize_family(model_family)
    topics = read_topics_inbox(paths.topics_inbox)
    payload = {
        "topic_id": topic_id,
        "title": f"{family_label} 孵化探针",
        "goal": "验证新方向是否真正进入 runnable 层，并先产出一条新的 smoke。",
        "success_metric": "new smoke artifact appears",
        "scope_label": "incubation",
        "priority": 0.88,
        "status": "running",
        "promotable": True,
        "blocked_reason": "",
        "proposed_tracks": [track_id],
        "source_evidence_ids": [],
        "created_by": "autobci-agent",
        "thinking_heartbeat_at": launched_at,
        "last_decision_at": launched_at,
        "last_decision_summary": f"主线已停滞，自动孵化 {family_label} 的最便宜 smoke 探针。",
        "last_materialization_at": launched_at,
        "materialization_state": "materialized_pending_smoke",
        "materialized_track_id": track_id,
        "materialized_run_id": "",
        "materialized_smoke_path": "",
        "structured_handoff": {
            "topic_id": topic_id,
            "materialized_track_id": track_id,
            "thread_id": "",
            "run_id": "",
            "next_action": "run smoke",
        },
        "last_activity_at": launched_at,
    }
    replaced = False
    for index, topic in enumerate(topics):
        if _as_text(topic.get("topic_id")) == topic_id:
            topics[index] = {**topic, **payload}
            replaced = True
            break
    if not replaced:
        topics.append(payload)
    topics.sort(
        key=lambda item: (
            -float(item.get("priority") or 0.0),
            _as_text(item.get("topic_id")),
        )
    )
    write_topics_inbox(paths.topics_inbox, topics)
    return payload


def _write_incubation_overlay(
    paths: AutoBciControlPlanePaths,
    *,
    campaign_id: str,
    track: dict[str, Any],
) -> Path:
    overlay_path = paths.runtime_overrides_dir / f"{campaign_id}.json"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        overlay_path,
        {
            "skip_track_ids": _manifest_track_ids(paths),
            "append_tracks": [track],
        },
    )
    return overlay_path


def _extract_smoke_result_for_track(
    status: dict[str, Any],
    *,
    track_id: str,
) -> tuple[str, str, str]:
    track_states = status.get("track_states", []) if isinstance(status, dict) else []
    for item in track_states:
        if not isinstance(item, dict) or _as_text(item.get("track_id")) != track_id:
            continue
        smoke_run_id = _as_text(item.get("latest_smoke_run_id") or item.get("latest_run_id"))
        thread_id = _as_text(item.get("codex_thread_id"))
        candidate_paths: list[str] = []
        for source in (item.get("local_best"), item.get("candidate"), item):
            if not isinstance(source, dict):
                continue
            result_json = _as_text(source.get("result_json"))
            if result_json:
                candidate_paths.append(result_json)
            for artifact in source.get("artifacts") or []:
                text = _as_text(artifact)
                if text:
                    candidate_paths.append(text)
        smoke_path = next((path for path in candidate_paths if path.endswith("_smoke.json")), "")
        if smoke_run_id or smoke_path:
            return smoke_run_id, smoke_path, thread_id
    return "", "", ""


def _finalize_active_incubation_if_needed(
    paths: AutoBciControlPlanePaths,
    *,
    mission_id: str,
) -> dict[str, Any] | None:
    runtime = _runtime_state(paths)
    active_campaigns = [
        dict(item)
        for item in (runtime.get("active_incubation_campaigns") or [])
        if isinstance(item, dict)
    ]
    if not active_campaigns:
        return None
    pid = int(runtime.get("pid") or 0)
    if pid and _pid_is_alive(pid):
        return None

    active = active_campaigns[0]
    topic_id = _as_text(active.get("topic_id"))
    track_id = _as_text(active.get("track_id"))
    status = read_json(paths.autoresearch_status, {})
    smoke_run_id, smoke_path, thread_id = _extract_smoke_result_for_track(status, track_id=track_id)
    topics = read_topics_inbox(paths.topics_inbox)
    finalized_state = "smoke_completed" if smoke_path else "research_only"
    now = _utcnow()
    for index, topic in enumerate(topics):
        if _as_text(topic.get("topic_id")) != topic_id:
            continue
        handoff = dict(topic.get("structured_handoff") or {})
        handoff["materialized_track_id"] = track_id
        if thread_id:
            handoff["thread_id"] = thread_id
        if smoke_run_id:
            handoff["run_id"] = smoke_run_id
        handoff["next_action"] = "return to decision loop"
        topics[index] = {
            **topic,
            "status": "done",
            "materialization_state": finalized_state,
            "materialized_track_id": track_id,
            "materialized_run_id": smoke_run_id,
            "materialized_smoke_path": smoke_path,
            "last_smoke_at": now if smoke_path else _as_text(topic.get("last_smoke_at")),
            "last_activity_at": now,
            "structured_handoff": handoff,
        }
        break
    write_topics_inbox(paths.topics_inbox, topics)

    refreshed_runtime = _runtime_state(paths)
    refreshed_runtime["active_incubation_track_id"] = ""
    refreshed_runtime["active_incubation_campaigns"] = []
    refreshed_runtime["current_campaign_id"] = mission_id
    refreshed_runtime["campaign_id"] = mission_id
    _write_runtime_state(paths, refreshed_runtime)
    append_jsonl(
        paths.supervisor_events,
        {
            "recorded_at": now,
            "event": "auto_incubation_finalized",
            "mission_id": mission_id,
            "topic_id": topic_id,
            "track_id": track_id,
            "state": finalized_state,
            "smoke_run_id": smoke_run_id,
            "smoke_path": smoke_path,
        },
    )
    think(paths)
    return {
        "topic_id": topic_id,
        "track_id": track_id,
        "materialization_state": finalized_state,
        "materialized_run_id": smoke_run_id,
        "materialized_smoke_path": smoke_path,
    }


def _maybe_start_auto_incubation(
    paths: AutoBciControlPlanePaths,
    *,
    mission_id: str,
) -> dict[str, Any] | None:
    runtime = _runtime_state(paths)
    if _pid_is_alive(int(runtime.get("pid") or 0)):
        return None
    if runtime.get("active_incubation_campaigns"):
        return None

    now_dt = datetime.now(timezone.utc)
    topics = read_topics_inbox(paths.topics_inbox)
    if _active_incubation_topics(topics):
        return None
    if _recent_incubation_activity_exists(topics, now=now_dt):
        return None

    stagnation = compute_mainline_stagnation(paths)
    if stagnation.get("stagnation_level") != "stagnant":
        return None

    family = _next_incubation_family(topics)
    if not family:
        return None

    track_id = _incubation_track_id(family, now=now_dt)
    campaign_id = _incubation_campaign_id(mission_id, family)
    track = _build_incubation_track(family, track_id=track_id)
    overlay_path = _write_incubation_overlay(paths, campaign_id=campaign_id, track=track)
    topic = _upsert_incubation_topic(
        paths,
        model_family=family,
        track_id=track_id,
        launched_at=now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    launch_payload = launch_campaign(
        paths,
        campaign_id=campaign_id,
        max_iterations=1,
        patience=1,
        runtime_track_overlay=overlay_path,
    )

    refreshed_runtime = _runtime_state(paths)
    active_campaign = {
        "campaign_id": launch_payload["campaign_id"],
        "topic_id": topic["topic_id"],
        "track_id": track_id,
        "family": family,
    }
    refreshed_runtime.update(
        {
            "mission_id": mission_id,
            "last_auto_pivot_at": now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "active_incubation_track_id": track_id,
            "recommended_incubation": {
                "family": family,
                "topic_id": topic["topic_id"],
                "track_id": track_id,
            },
            "active_incubation_campaigns": [active_campaign],
        }
    )
    _write_runtime_state(paths, refreshed_runtime)
    append_jsonl(
        paths.supervisor_events,
        {
            "recorded_at": _utcnow(),
            "event": "auto_incubation_started",
            "mission_id": mission_id,
            "topic_id": topic["topic_id"],
            "track_id": track_id,
            "family": family,
            "campaign_id": launch_payload["campaign_id"],
            "overlay_path": str(overlay_path),
            "stagnation_level": stagnation.get("stagnation_level"),
            "days_without_breakthrough": stagnation.get("days_without_breakthrough"),
        },
    )
    return {
        "family": family,
        "topic_id": topic["topic_id"],
        "track_id": track_id,
        "campaign_id": launch_payload["campaign_id"],
        "overlay_path": str(overlay_path),
    }


def _build_moonshot_track(*, model_family: str, feature_family: str) -> dict[str, Any]:
    script = _feature_sequence_script(model_family)
    family_label = _humanize_family(model_family)
    track_id = f"moonshot_upper_bound_{model_family}_{_feature_variant_suffix(feature_family)}_scout"
    common_args = (
        f"{script} "
        f"--feature-family {feature_family} "
        "--feature-reducers mean "
        "--signal-preprocess car_notch_bandpass "
        "--target-axes xyz "
    )
    smoke_command = (
        ".venv/bin/python "
        f"{common_args}"
        f"--dataset-config {MOONSHOT_ULTRASCOUT_DATASET} "
        "--epochs 2 --batch-size 128 --seed 0 --final-eval "
        "--hidden-size 32 --num-layers 1 --patience 1 --feature-bin-ms 100.0"
    )
    formal_command = (
        ".venv/bin/python "
        f"{common_args}"
        f"--dataset-config {MOONSHOT_FORMAL_DATASET} "
        "--epochs 12 --batch-size 64 --seed 0 --final-eval "
        "--hidden-size 64 --num-layers 1 --patience 3 --feature-bin-ms 100.0"
    )
    return {
        "track_id": track_id,
        "topic_id": MOONSHOT_TOPIC_ID,
        "runner_family": model_family,
        "track_goal": f"用 {family_label} 在同试次纯脑电 upper-bound 口径上测试 {feature_family}，冲击均值 r {MOONSHOT_TARGET:.1f}。",
        "promotion_target": MOONSHOT_TOPIC_ID,
        "internet_research_enabled": True,
        "smoke_command": smoke_command,
        "formal_command": formal_command,
        "allowed_change_scope": ["scripts", "src/bci_autoresearch/models", "src/bci_autoresearch/features"],
        "algorithm_family": model_family,
        "algorithm_label": family_label,
        "method_variant_label": feature_family,
        "input_mode_label": "只用脑电",
        "series_class": "mainline_brain",
        "promotable": True,
        "validated": True,
        "skip_codex_edit": True,
        "evaluation_scope": "same_session_pure_brain",
    }


def _write_moonshot_manifest(paths: AutoBciControlPlanePaths, *, task_slug: str) -> Path:
    manifest_path = paths.runtime_overrides_dir / f"{task_slug}-moonshot-tracks.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tracks = [_build_moonshot_track(model_family=model_family, feature_family=feature_family) for model_family, feature_family in MOONSHOT_TRACK_SPECS]
    write_json_atomic(
        manifest_path,
        {
            "review_cadence": "moonshot",
            "mission_profile": "same_session_pure_brain_moonshot",
            "moonshot_target": MOONSHOT_TARGET,
            "moonshot_scope_label": MOONSHOT_SCOPE_LABEL,
            "tracks": tracks,
        },
    )
    return manifest_path


def _record_candidate_evidence(
    paths: AutoBciControlPlanePaths,
    *,
    task_text: str,
    candidates: list[str],
) -> None:
    append_jsonl(
        paths.research_queries,
        {
            "recorded_at": _utcnow(),
            "task": task_text,
            "candidate_families": candidates,
            "kind": "autonomous_execute",
        },
    )
    for rank, family in enumerate(candidates, start=1):
        append_jsonl(
            paths.research_evidence,
            {
                "recorded_at": _utcnow(),
                "task": task_text,
                "candidate_model_family": family,
                "why_it_fits_this_repo": MOONSHOT_CANDIDATE_REASONS.get(family, "严格因果，可复用现有特征序列训练栈。"),
                "strict_causal_ok": True,
                "estimated_code_delta": "small" if family in {"feature_lstm", "feature_gru", "feature_tcn"} else "medium",
                "rank": rank,
            },
        )


def _execute_default_task(
    resolved: AutoBciControlPlanePaths,
    *,
    task_text: str,
    task_slug: str,
    worktree: Path,
    venv_dir: Path,
    verify_output: str,
    max_iterations: int,
    patience: int,
    supervise: bool,
) -> str:
    candidates = ["feature_gru", "feature_tcn"]
    _record_candidate_evidence(resolved, task_text=task_text, candidates=candidates)
    promoted = ["feature_gru_mainline", "feature_tcn_mainline"]
    _promote_track_ids_to_front(resolved, track_ids=promoted)
    runtime = _runtime_state(resolved)
    runtime.update(
        {
            "agent_status": "queued",
            "current_task": task_text,
            "current_candidates": candidates,
            "current_worktree": str(worktree),
            "validation_summary": "Feature GRU / Feature TCN 使用隔离 venv 完成 verify_env 与队列预检。",
            "promoted_track_ids": promoted,
            "current_direction_tags": ["G", "T"],
            "last_research_judgment_update": "继续优先纯脑电突破，先把 GRU / TCN 排到真实执行队列最前。",
            "mainline_promotion_status": "guarded",
            "mainline_promotion_reason": "尚未出现超过当前最可信纯脑电正式结果的复验候选。",
            "autonomous_research_status": "queued",
            "last_autonomous_task": task_text,
            "last_autonomous_candidates": candidates,
            "last_autonomous_worktree": str(worktree),
            "last_autonomous_validation_summary": verify_output,
            "last_autonomous_promoted_track_ids": promoted,
            "last_autonomous_failure_reason": "",
        }
    )
    _write_runtime_state(resolved, runtime)
    payload = launch_campaign(
        resolved,
        campaign_id=f"autobci-exec-{task_slug}",
        max_iterations=max_iterations,
        patience=patience,
    )
    runtime = _runtime_state(resolved)
    runtime.update(
        {
            "agent_status": "queued",
            "current_worktree": str(worktree),
            "validation_summary": verify_output,
            "promoted_track_ids": promoted,
            "current_candidates": candidates,
            "current_task": task_text,
            "current_direction_tags": ["G", "T"],
            "execution_venv_path": str(venv_dir),
            "execution_campaign_id": payload["campaign_id"],
        }
    )
    _write_runtime_state(resolved, runtime)
    if supervise:
        start_supervision_background(
            resolved,
            mission_id=payload["campaign_id"],
            max_iterations=max_iterations,
            patience=patience,
            auto_incubate=True,
        )
    return f"queued {', '.join(promoted)} via {payload['campaign_id']}"


def format_status_summary(paths: AutoBciControlPlanePaths | None = None) -> str:
    snapshot = build_status_snapshot(paths)
    lines = [
        "🧭 AutoBci 内置控制面",
        f"仓库：{snapshot['repo_root']}",
        f"看板：{snapshot['dashboard_url']}",
        f"任务：{snapshot.get('campaign_id') or '-'}",
        f"阶段：{snapshot.get('stage') or '-'}",
        f"当前轨：{snapshot.get('current_track_id') or '-'}",
        f"运行状态：{snapshot.get('agent_status') or '-'}",
    ]
    if snapshot.get("current_task"):
        lines.append(f"当前任务：{snapshot['current_task']}")
    if snapshot.get("current_candidates"):
        lines.append(f"当前候选：{', '.join(snapshot['current_candidates'])}")
    if snapshot.get("validation_summary"):
        lines.append(f"验证摘要：{snapshot['validation_summary']}")
    if snapshot.get("current_direction_tags"):
        lines.append(f"方向标签：{', '.join(snapshot['current_direction_tags'])}")
    thinking_overview = snapshot.get("thinking_overview") or {}
    if thinking_overview.get("thinking_heartbeat_at"):
        lines.append(f"思考心跳：{thinking_overview['thinking_heartbeat_at']}")
    if thinking_overview.get("stale_topic_count") is not None:
        lines.append(
            "停滞概览："
            f"{thinking_overview.get('stale_topic_count', 0)} 个卡住，"
            f"{thinking_overview.get('pivot_topic_count', 0)} 个已换向，"
            f"{thinking_overview.get('pending_materialization_count', 0)} 个待物化"
        )
    stale_topics = [item for item in snapshot.get("topic_handoff_summaries", []) if item.get("stale_reason_codes")]
    if stale_topics:
        top_stale = stale_topics[0]
        lines.append(
            "最近卡住："
            f"{top_stale.get('topic_id')} · {', '.join(top_stale.get('stale_reason_codes') or [])}"
        )
    family_lines = []
    for item in snapshot.get("algorithm_family_bests", [])[:6]:
        control_note = "（控制实验）" if item.get("is_control_best") else ""
        family_lines.append(
            f"{item.get('algorithm_label')}: val r {item.get('best_val_r_label')} 来自 {item.get('best_method_display_label')}{control_note}"
        )
    if family_lines:
        lines.append("算法家族最高：")
        lines.extend(f"- {line}" for line in family_lines)
    return "\n".join(lines)


def build_digest_summary(paths: AutoBciControlPlanePaths | None = None) -> str:
    snapshot = build_status_snapshot(paths)
    recent = snapshot.get("recent_method_summaries", [])[:5]
    body = "；".join(
        f"{item['method_display_label']} val r {item['latest_val_r_label']}"
        for item in recent
    ) or "最近还没有可读方法结果。"
    return f"{snapshot.get('campaign_id') or '当前无任务'}｜{snapshot.get('agent_status') or 'idle'}｜{body}"


def build_follow_summary(paths: AutoBciControlPlanePaths | None = None) -> str:
    snapshot = build_status_snapshot(paths)
    lines = [
        f"{snapshot.get('campaign_id') or '当前无任务'} · {snapshot.get('stage') or '-'} · {snapshot.get('current_track_id') or '-'}",
        f"运行状态：{snapshot.get('agent_status') or '-'}",
    ]
    thinking_overview = snapshot.get("thinking_overview") or {}
    if thinking_overview.get("last_decision_at") or thinking_overview.get("last_materialization_at"):
        lines.append(
            "推进链："
            f"decision={thinking_overview.get('last_decision_at') or '-'}，"
            f"materialization={thinking_overview.get('last_materialization_at') or '-'}，"
            f"smoke={thinking_overview.get('last_smoke_at') or '-'}"
        )
    upcoming = snapshot.get("upcoming_queue_method_summaries", [])[:5]
    if upcoming:
        lines.append("接下来：")
        lines.extend(f"- {item['method_display_label']}" for item in upcoming)
    stale_topics = [item for item in snapshot.get("topic_handoff_summaries", []) if item.get("stale_reason_codes")]
    if stale_topics:
        lines.append(f"最近卡住：{stale_topics[0].get('topic_id')} · {', '.join(stale_topics[0].get('stale_reason_codes') or [])}")
    return "\n".join(lines)


def think(paths: AutoBciControlPlanePaths | None = None) -> dict[str, Any]:
    resolved = paths or get_control_plane_paths()
    runtime = _runtime_state(resolved)
    previous_agent_status = _as_text(runtime.get("agent_status")) or "idle"
    runtime["agent_status"] = "thinking"
    _write_runtime_state(resolved, runtime)

    topics = build_topics(resolved)
    retrieval_packet = build_retrieval_packet(resolved, topics)
    decision_packet = build_decision_packet(resolved, topics, retrieval_packet)
    hypothesis_entry = build_hypothesis_entry(topics, retrieval_packet)
    judgment_update = build_judgment_update(resolved, topics, decision_packet, hypothesis_entry)

    write_topics_inbox(resolved.topics_inbox, topics)
    write_retrieval_packet(
        resolved.retrieval_packets_dir,
        retrieval_packet,
        recorded_at=_as_text(hypothesis_entry.get("recorded_at")),
    )
    write_decision_packet(
        resolved.decision_packets_dir,
        decision_packet,
        recorded_at=_as_text(judgment_update.get("recorded_at")),
    )
    append_hypothesis_log(resolved.hypothesis_log, hypothesis_entry)
    append_judgment_update(resolved.judgment_updates, judgment_update)

    refreshed_runtime = _runtime_state(resolved)
    refreshed_runtime["agent_status"] = previous_agent_status
    refreshed_runtime["last_research_judgment_update"] = _as_text(decision_packet.get("research_judgment_delta"))
    refreshed_runtime["thinking_status"] = "idle"
    if not refreshed_runtime.get("current_candidates"):
        refreshed_runtime["current_candidates"] = list(decision_packet.get("recommended_queue", []))
    if not refreshed_runtime.get("current_task"):
        refreshed_runtime["current_task"] = _as_text(retrieval_packet.get("current_problem_statement"))
    _write_runtime_state(resolved, refreshed_runtime)

    return decision_packet


def list_topics(paths: AutoBciControlPlanePaths | None = None) -> list[dict[str, Any]]:
    resolved = paths or get_control_plane_paths()
    topics = read_topics_inbox(resolved.topics_inbox)
    return sorted(
        topics,
        key=lambda item: (
            -(float(item.get("priority") or 0.0)),
            _as_text(item.get("last_decision_at")),
            _as_text(item.get("topic_id")),
        ),
    )


def topic_triage(
    paths: AutoBciControlPlanePaths | None = None,
    *,
    topic_id: str,
    title: str,
    goal: str,
    success_metric: str,
    scope_label: str,
    priority: float,
    promotable: bool,
) -> dict[str, Any]:
    resolved = paths or get_control_plane_paths()
    topics = read_topics_inbox(resolved.topics_inbox)
    now = _utcnow()
    payload = {
        "topic_id": topic_id,
        "title": title,
        "goal": goal,
        "success_metric": success_metric,
        "scope_label": scope_label,
        "priority": float(priority),
        "status": "triaged",
        "promotable": bool(promotable),
        "blocked_reason": "",
        "proposed_tracks": [],
        "source_evidence_ids": [],
        "created_by": "autobci-agent",
        "last_decision_at": now,
        "last_decision_summary": "人工或控制面显式立项，等待进入 runnable 阶段。",
    }
    replaced = False
    for index, topic in enumerate(topics):
        if _as_text(topic.get("topic_id")) == topic_id:
            topics[index] = payload
            replaced = True
            break
    if not replaced:
        topics.append(payload)
    topics.sort(key=lambda item: (-float(item.get("priority") or 0.0), _as_text(item.get("topic_id"))))
    write_topics_inbox(resolved.topics_inbox, topics)
    return {"topics": topics}


def queue_summary(paths: AutoBciControlPlanePaths | None = None) -> dict[str, Any]:
    resolved = paths or get_control_plane_paths()
    snapshot = build_status_snapshot(resolved)
    latest_decision = snapshot.get("latest_decision_packet") or {}
    recommended_queue = latest_decision.get("recommended_queue") or []
    return {
        "recommended_queue": recommended_queue,
        "recommended_formal_candidates": latest_decision.get("recommended_formal_candidates") or [],
        "stale_topics_to_deprioritize": latest_decision.get("stale_topics_to_deprioritize") or [],
        "thinking_overview": snapshot.get("thinking_overview") or {},
        "topic_handoff_summaries": snapshot.get("topic_handoff_summaries") or [],
    }


def judgment_summary(paths: AutoBciControlPlanePaths | None = None) -> dict[str, Any]:
    resolved = paths or get_control_plane_paths()
    snapshot = build_status_snapshot(resolved)
    return {
        "latest_judgment_updates": snapshot.get("latest_judgment_updates") or [],
        "thinking_overview": snapshot.get("thinking_overview") or {},
        "topic_handoff_summaries": snapshot.get("topic_handoff_summaries") or [],
    }


def launch_campaign(
    paths: AutoBciControlPlanePaths | None = None,
    *,
    campaign_id: str | None = None,
    track_manifest_path: str | Path | None = None,
    runtime_track_overlay: str | Path | None = None,
    baseline_metrics_path: str | Path | None = None,
    baseline_command: str | None = None,
    bank_qc_command: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    patience: int = DEFAULT_PATIENCE,
) -> dict[str, Any]:
    resolved = paths or get_control_plane_paths()
    runtime = _runtime_state(resolved)
    if _pid_is_alive(int(runtime.get("pid") or 0)):
        raise ControlPlaneError("已有受控 AutoResearch 进程在运行。")
    run_campaign_id = campaign_id or f"autobci-{int(time.time())}"
    resolved.launch_logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved.launch_logs_dir / f"{run_campaign_id}.log"
    command = [
        "npm",
        "-C",
        str(resolved.repo_root / "tools" / "autoresearch"),
        "run",
        "campaign",
        "--",
        "--campaign-id",
        run_campaign_id,
        "--max-iterations",
        str(max_iterations),
        "--patience",
        str(patience),
    ]
    if track_manifest_path:
        command.extend(["--track-manifest", str(track_manifest_path)])
    if runtime_track_overlay:
        command.extend(["--runtime-track-overlay", str(runtime_track_overlay)])
    if baseline_metrics_path:
        command.extend(["--baseline-metrics-path", str(baseline_metrics_path)])
    if baseline_command:
        command.extend(["--baseline-command", str(baseline_command)])
    if bank_qc_command:
        command.extend(["--bank-qc-command", str(bank_qc_command)])
    env = os.environ.copy()
    env.setdefault(DEFAULT_CACHE_ROOT_ENV, str(default_cache_root()))
    env[AUTOBCI_ROOT_ENV] = str(resolved.repo_root)
    with log_path.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=resolved.repo_root,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            **detached_process_kwargs(),
        )
    runtime.update(
        {
            "pid": process.pid,
            "campaign_id": run_campaign_id,
            "current_campaign_id": run_campaign_id,
            "runtime_status": "running",
            "campaign_mode": "exploration",
            "command": shlex.join(command),
            "log_path": str(log_path),
            "max_iterations": max_iterations,
            "patience": patience,
            "agent_status": runtime.get("agent_status") or "running",
            "launched_at": _utcnow(),
            "stop_reason": "none",
        }
    )
    _write_runtime_state(resolved, runtime)
    return {
        "campaign_id": run_campaign_id,
        "pid": process.pid,
        "launched_at": runtime["launched_at"],
        "log_path": str(log_path),
    }


def _request_runtime_state(paths: AutoBciControlPlanePaths, *, desired_state: str, request_key: str) -> dict[str, Any]:
    runtime = _runtime_state(paths)
    runtime["desired_state"] = desired_state
    runtime[request_key] = True
    runtime[f"{request_key}_at"] = _utcnow()
    _write_runtime_state(paths, runtime)
    return runtime


def _signal_runtime(paths: AutoBciControlPlanePaths, sig: signal.Signals, next_state: str, verb: str, request_key: str) -> str:
    runtime = _request_runtime_state(paths, desired_state=next_state, request_key=request_key)
    pid = int(runtime.get("pid") or 0)
    campaign = runtime.get("campaign_id") or pid or "-"
    if not _pid_is_alive(pid):
        runtime["runtime_status"] = f"{next_state}_requested"
        _write_runtime_state(paths, runtime)
        return f"已记录 {verb} 请求：当前没有可 signal 的 AutoResearch 进程（{campaign}）。"
    if is_windows():
        runtime["runtime_status"] = f"{next_state}_requested"
        _write_runtime_state(paths, runtime)
        return f"Windows 已记录 {verb} 请求：不会发送 SIGSTOP/SIGCONT，请运行时按 desired_state={next_state} 协作处理（{campaign}）。"
    signaled = _signal_pid(pid, sig)
    runtime["runtime_status"] = next_state if signaled else f"{next_state}_requested"
    _write_runtime_state(paths, runtime)
    return f"{verb} {campaign}" if signaled else f"已记录 {verb} 请求：signal 发送失败（{campaign}）。"


def pause_runtime(paths: AutoBciControlPlanePaths | None = None) -> str:
    return _signal_runtime(paths or get_control_plane_paths(), signal.SIGSTOP, "paused", "paused", "pause_requested")


def resume_runtime(paths: AutoBciControlPlanePaths | None = None) -> str:
    return _signal_runtime(paths or get_control_plane_paths(), signal.SIGCONT, "running", "resumed", "resume_requested")


def end_runtime(paths: AutoBciControlPlanePaths | None = None) -> str:
    resolved = paths or get_control_plane_paths()
    runtime = _runtime_state(resolved)
    targets = [int(runtime.get("pid") or 0), int(runtime.get("supervisor_pid") or 0)]
    stopped = [pid for pid in targets if pid > 0 and _signal_pid(pid, signal.SIGTERM)]
    runtime["runtime_status"] = "terminated"
    runtime["halt_requested"] = "ended"
    runtime["pid"] = None
    runtime["supervisor_pid"] = None
    _write_runtime_state(resolved, runtime)
    return f"ended {runtime.get('campaign_id') or '-'} (signaled {', '.join(str(pid) for pid in stopped) or 'none'})"


def _promote_track_ids_to_front(paths: AutoBciControlPlanePaths, *, track_ids: list[str]) -> list[str]:
    manifest = read_json(paths.track_manifest, {})
    tracks = manifest.get("tracks", []) if isinstance(manifest, dict) else []
    templates = {
        "feature_gru_mainline": {
            "track_id": "feature_gru_mainline",
            "topic_id": "wave1_autonomous",
            "runner_family": "feature_gru",
            "track_goal": "Use a strict-causal Feature GRU mainline to pursue pure-brain breakthrough on the current joints target.",
        },
        "feature_tcn_mainline": {
            "track_id": "feature_tcn_mainline",
            "topic_id": "wave1_autonomous",
            "runner_family": "feature_tcn",
            "track_goal": "Use a strict-causal Feature TCN mainline to pursue pure-brain breakthrough on the current joints target.",
        },
    }
    seen_ids = {str(item.get("track_id") or "").strip() for item in tracks if isinstance(item, dict)}
    for track_id in track_ids:
        if track_id not in seen_ids and track_id in templates:
            tracks.append(dict(templates[track_id]))
    rank = {track_id: index for index, track_id in enumerate(track_ids)}
    ordered = sorted(
        tracks,
        key=lambda item: (rank.get(str(item.get("track_id") or "").strip(), len(rank)),),
    )
    manifest["tracks"] = ordered
    write_json_atomic(paths.track_manifest, manifest)
    return [str(item.get("track_id") or "").strip() for item in ordered]


def _ensure_execution_worktree(paths: AutoBciControlPlanePaths, *, task_slug: str) -> Path:
    base = paths.execution_worktrees_root
    target = base / f"{task_slug}-{int(time.time())}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "-C", str(paths.repo_root), "worktree", "add", "--detach", str(target), "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        target.mkdir(parents=True, exist_ok=True)
    return target


def _bootstrap_execution_venv(worktree_root: Path, *, source_repo_root: Path) -> tuple[Path, str]:
    venv_dir = worktree_root / ".venv"
    python_bin = venv_python_path(venv_dir)
    if not python_bin.exists():
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)], check=True)
        python_bin = venv_python_path(venv_dir)
    verify_env = source_repo_root / "scripts" / "verify_env.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_repo_root / "src")
    completed = subprocess.run(
        [str(python_bin), str(verify_env)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=source_repo_root,
    )
    return venv_dir, completed.stdout.strip()


def execute_task(
    task: str,
    *,
    paths: AutoBciControlPlanePaths | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    patience: int = DEFAULT_PATIENCE,
    supervise: bool = True,
) -> str:
    resolved = paths or get_control_plane_paths()
    task_text = task.strip()
    if not task_text:
        raise ControlPlaneError("缺少 execute 任务描述。")
    task_slug = _slugify(task_text)
    worktree = _ensure_execution_worktree(resolved, task_slug=task_slug)
    venv_dir, verify_output = _bootstrap_execution_venv(worktree, source_repo_root=resolved.repo_root)
    if not _is_moonshot_task(task_text):
        return _execute_default_task(
            resolved,
            task_text=task_text,
            task_slug=task_slug,
            worktree=worktree,
            venv_dir=venv_dir,
            verify_output=verify_output,
            max_iterations=max_iterations,
            patience=patience,
            supervise=supervise,
        )

    candidates = list(MOONSHOT_CANDIDATES)
    _record_candidate_evidence(resolved, task_text=task_text, candidates=candidates)
    moonshot_manifest = _write_moonshot_manifest(resolved, task_slug=task_slug)
    promoted = [track["track_id"] for track in read_json(moonshot_manifest, {}).get("tracks", [])]
    runtime = _runtime_state(resolved)
    runtime.update(
        {
            "agent_status": "queued",
            "current_task": task_text,
            "current_candidates": candidates,
            "current_worktree": str(worktree),
            "validation_summary": "same-session 纯脑电 moonshot manifest 已生成，隔离 venv verify_env 已通过，准备进入 ultra-scout。",
            "promoted_track_ids": promoted,
            "current_direction_tags": ["G", "T", "P"],
            "last_research_judgment_update": "今晚切到同试次纯脑电 moonshot，广撒家族做 ultra-scout，再按榜单收 formal。",
            "mainline_promotion_status": "moonshot",
            "mainline_promotion_reason": "当前专用 mission 只服务于同试次纯脑电 0.6 冲刺，不混入控制实验。",
            "autonomous_research_status": "queued",
            "last_autonomous_task": task_text,
            "last_autonomous_candidates": candidates,
            "last_autonomous_worktree": str(worktree),
            "last_autonomous_validation_summary": verify_output,
            "last_autonomous_promoted_track_ids": promoted,
            "last_autonomous_failure_reason": "",
            "moonshot_target": MOONSHOT_TARGET,
            "moonshot_scope_label": MOONSHOT_SCOPE_LABEL,
            "moonshot_manifest_path": str(moonshot_manifest),
        }
    )
    _write_runtime_state(resolved, runtime)
    payload = launch_campaign(
        resolved,
        campaign_id=f"moonshot-{task_slug}",
        track_manifest_path=moonshot_manifest,
        max_iterations=max_iterations,
        patience=patience,
    )
    runtime = _runtime_state(resolved)
    runtime.update(
        {
            "agent_status": "queued",
            "current_worktree": str(worktree),
            "validation_summary": verify_output,
            "promoted_track_ids": promoted,
            "current_candidates": candidates,
            "current_task": task_text,
            "current_direction_tags": ["G", "T", "P"],
            "execution_venv_path": str(venv_dir),
            "execution_campaign_id": payload["campaign_id"],
            "moonshot_target": MOONSHOT_TARGET,
            "moonshot_scope_label": MOONSHOT_SCOPE_LABEL,
            "moonshot_manifest_path": str(moonshot_manifest),
        }
    )
    _write_runtime_state(resolved, runtime)
    if supervise:
        start_supervision_background(
            resolved,
            mission_id=payload["campaign_id"],
            max_iterations=max_iterations,
            patience=patience,
            auto_incubate=True,
        )
    return f"queued moonshot pure-brain scout via {payload['campaign_id']}"


def heal_mission(
    paths: AutoBciControlPlanePaths | None = None,
    *,
    mission_id: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    patience: int = DEFAULT_PATIENCE,
) -> str:
    resolved = paths or get_control_plane_paths()
    runtime = _runtime_state(resolved)
    campaign_id = mission_id or runtime.get("current_campaign_id") or runtime.get("campaign_id") or "autobci-heal"
    return f"heal scheduled for {campaign_id} with max_iterations={max_iterations} patience={patience}"


def supervise_mission(
    paths: AutoBciControlPlanePaths | None = None,
    *,
    mission_id: str | None = None,
    duration_hours: float = DEFAULT_SUPERVISION_HOURS,
    watch_interval_seconds: int = 60,
    summary_interval_seconds: int = 600,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    patience: int = DEFAULT_PATIENCE,
    auto_incubate: bool = False,
) -> str:
    resolved = paths or get_control_plane_paths()
    runtime = _runtime_state(resolved)
    mission = mission_id or runtime.get("current_campaign_id") or runtime.get("campaign_id") or "autobci-mission"
    started = time.monotonic()
    summary_due = started
    while time.monotonic() - started < max(0.0, duration_hours) * 3600.0:
        runtime = _runtime_state(resolved)
        if str(runtime.get("halt_requested") or "").lower() == "ended":
            break
        if auto_incubate:
            think(resolved)
            _finalize_active_incubation_if_needed(resolved, mission_id=mission)
            runtime = _runtime_state(resolved)
        pid = int(runtime.get("pid") or 0)
        if pid and not _pid_is_alive(pid):
            auto_started = False
            if auto_incubate:
                auto_started = _maybe_start_auto_incubation(
                    resolved,
                    mission_id=mission,
                ) is not None
            if not auto_started:
                launch_campaign(
                    resolved,
                    campaign_id=runtime.get("current_campaign_id") or mission,
                    max_iterations=max_iterations,
                    patience=patience,
                )
        elif auto_incubate and not pid:
            _maybe_start_auto_incubation(
                resolved,
                mission_id=mission,
            )
        if time.monotonic() >= summary_due:
            append_jsonl(
                resolved.supervisor_events,
                {
                    "recorded_at": _utcnow(),
                    "event": "summary",
                    "mission_id": mission,
                    "summary": format_status_summary(resolved),
                    "auto_incubate": auto_incubate,
                },
            )
            summary_due = time.monotonic() + max(1, summary_interval_seconds)
        time.sleep(max(1, watch_interval_seconds))
    append_jsonl(
        resolved.supervisor_events,
        {
            "recorded_at": _utcnow(),
            "event": "done",
            "mission_id": mission,
        },
    )
    return format_status_summary(resolved)


def start_supervision_background(
    paths: AutoBciControlPlanePaths | None = None,
    *,
    mission_id: str | None = None,
    duration_hours: float = DEFAULT_SUPERVISION_HOURS,
    watch_interval_seconds: int = 60,
    summary_interval_seconds: int = 600,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    patience: int = DEFAULT_PATIENCE,
    auto_incubate: bool = False,
) -> str:
    resolved = paths or get_control_plane_paths()
    runtime = _runtime_state(resolved)
    supervisor_pid = int(runtime.get("supervisor_pid") or 0)
    mission = mission_id or runtime.get("current_campaign_id") or runtime.get("campaign_id") or "autobci-mission"
    if _pid_is_alive(supervisor_pid):
        return f"supervision already running for {mission} (pid {supervisor_pid})"
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(resolved.repo_root / "src"))
    env[AUTOBCI_ROOT_ENV] = str(resolved.repo_root)
    command = [
        sys.executable,
        "-m",
        "bci_autoresearch.control_plane.cli",
        "supervise",
        "--foreground",
        "--repo-root",
        str(resolved.repo_root),
        "--mission-id",
        mission,
        "--hours",
        str(duration_hours),
        "--watch-interval",
        str(watch_interval_seconds),
        "--summary-interval",
        str(summary_interval_seconds),
        "--max-iterations",
        str(max_iterations),
        "--patience",
        str(patience),
    ]
    if auto_incubate:
        command.append("--auto-incubate")
    resolved.launch_logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved.launch_logs_dir / f"{mission}-supervisor.log"
    with log_path.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=resolved.repo_root,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            **detached_process_kwargs(),
        )
    runtime["supervisor_pid"] = process.pid
    runtime["supervisor_status"] = "running"
    runtime["mission_id"] = mission
    runtime["auto_incubate_enabled"] = auto_incubate
    _write_runtime_state(resolved, runtime)
    return f"supervision started for {mission} (pid {process.pid})"
