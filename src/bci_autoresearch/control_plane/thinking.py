from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .paths import AutoBciControlPlanePaths
from .registry import normalize_algorithm_family, resolve_direction_spec
from .runtime_store import read_json, read_jsonl, read_text, read_topics_inbox


TOPIC_STATUSES = {
    "triaged",
    "runnable",
    "blocked",
    "queued",
    "running",
    "done",
    "abandoned",
}

MOONSHOT_TOPIC_ID = "same_session_pure_brain_moonshot"
MOONSHOT_TITLE = "同试次纯脑电 0.6 冲刺"
MOONSHOT_GOAL = "把同试次纯脑电 8 关节平均相关系数提升到 0.6"
MOONSHOT_SUCCESS_METRIC = "val mean Pearson r >= 0.6"
MOONSHOT_SCOPE_LABEL = "same_session_pure_brain"
MOONSHOT_TRACK_PLACEHOLDER = "moonshot_upper_bound_feature_gru_lmp_hg_phase_state_scout"
DEFAULT_PROBLEM_STATEMENT = "纯脑电正式上限还没有被明确抬高。"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: Any) -> datetime | None:
    text = _as_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def _sort_timestamp(value: Any) -> tuple[int, datetime]:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return (1, datetime.min.replace(tzinfo=timezone.utc))
    return (0, parsed)


@dataclass
class Topic:
    topic_id: str
    title: str
    goal: str
    success_metric: str
    scope_label: str
    priority: float
    status: str
    promotable: bool
    blocked_reason: str = ""
    proposed_tracks: list[str] = field(default_factory=list)
    source_evidence_ids: list[str] = field(default_factory=list)
    created_by: str = "autobci-agent"
    thinking_heartbeat_at: str = ""
    last_retrieval_at: str = ""
    last_decision_at: str = ""
    last_judgment_at: str = ""
    last_materialization_at: str = ""
    last_smoke_at: str = ""
    last_decision_summary: str = ""
    materialization_state: str = ""
    materialized_track_id: str = ""
    materialized_run_id: str = ""
    materialized_smoke_path: str = ""
    structured_handoff: dict[str, Any] = field(default_factory=dict)
    stale_reason_codes: list[str] = field(default_factory=list)
    pivot_reason_codes: list[str] = field(default_factory=list)
    search_budget_state: dict[str, Any] = field(default_factory=dict)
    tool_usage_summary: dict[str, Any] = field(default_factory=dict)
    last_activity_at: str = ""

    def __post_init__(self) -> None:
        if not self.topic_id:
            raise ValueError("Topic.topic_id 不能为空")
        if self.status not in TOPIC_STATUSES:
            raise ValueError(f"Topic.status 不合法：{self.status}")
        if not self.title:
            raise ValueError(f"Topic.title 不能为空：{self.topic_id}")
        if not self.goal:
            raise ValueError(f"Topic.goal 不能为空：{self.topic_id}")
        if not self.success_metric:
            raise ValueError(f"Topic.success_metric 不能为空：{self.topic_id}")
        if not self.scope_label:
            raise ValueError(f"Topic.scope_label 不能为空：{self.topic_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalPacket:
    current_problem_statement: str
    hard_constraints: list[str]
    runtime_snapshot: dict[str, Any]
    topic_history: list[dict[str, Any]]
    similar_hypothesis_history: list[dict[str, Any]]
    relevant_evidence: list[dict[str, Any]]
    budget_and_queue_state: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.current_problem_statement:
            raise ValueError("RetrievalPacket.current_problem_statement 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionPacket:
    current_problem_statement: str
    recommended_topic_updates: list[dict[str, Any]]
    recommended_queue: list[str]
    recommended_formal_candidates: list[str]
    stale_topics_to_deprioritize: list[dict[str, Any]]
    research_judgment_delta: str

    def __post_init__(self) -> None:
        if not self.current_problem_statement:
            raise ValueError("DecisionPacket.current_problem_statement 不能为空")
        if not self.research_judgment_delta:
            raise ValueError("DecisionPacket.research_judgment_delta 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JudgmentUpdate:
    recorded_at: str
    run_id: str
    topic_id: str
    hypothesis_id: str
    outcome: str
    reason: str
    queue_update: str
    next_recommended_action: str

    def __post_init__(self) -> None:
        for field_name in ("recorded_at", "topic_id", "hypothesis_id", "outcome", "queue_update"):
            if not _as_text(getattr(self, field_name)):
                raise ValueError(f"JudgmentUpdate.{field_name} 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values:
        text = _as_text(item)
        if text:
            result.append(text)
    return result


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _merge_structured_handoff(
    existing: dict[str, Any],
    *,
    topic_id: str,
    materialized_track_id: str,
    materialized_run_id: str,
    source_evidence_ids: list[str],
) -> dict[str, Any]:
    handoff = _coerce_dict(existing.get("structured_handoff"))
    if not handoff:
        handoff = {}
    handoff.setdefault("topic_id", topic_id)
    if _as_text(handoff.get("hypothesis_id")):
        handoff["hypothesis_id"] = _as_text(handoff.get("hypothesis_id"))
    elif _as_text(existing.get("hypothesis_id")):
        handoff["hypothesis_id"] = _as_text(existing.get("hypothesis_id"))
    elif source_evidence_ids:
        handoff["hypothesis_id"] = f"hyp_{topic_id}"
    handoff["evidence_ids"] = _coerce_string_list(handoff.get("evidence_ids")) or list(source_evidence_ids)
    if materialized_track_id:
        handoff["materialized_track_id"] = materialized_track_id
    elif _as_text(handoff.get("materialized_track_id")):
        handoff["materialized_track_id"] = _as_text(handoff.get("materialized_track_id"))
    if materialized_run_id:
        handoff["run_id"] = materialized_run_id
    elif _as_text(handoff.get("run_id")):
        handoff["run_id"] = _as_text(handoff.get("run_id"))
    if _as_text(handoff.get("thread_id")):
        handoff["thread_id"] = _as_text(handoff.get("thread_id"))
    if _as_text(handoff.get("next_action")):
        handoff["next_action"] = _as_text(handoff.get("next_action"))
    return handoff


def _topic_latest_activity_at(
    *,
    existing: dict[str, Any],
    tracks: list[dict[str, Any]],
    state_by_track: dict[str, dict[str, Any]],
) -> str:
    marks: list[datetime] = []
    for field_name in (
        "last_smoke_at",
        "last_materialization_at",
        "last_judgment_at",
        "last_retrieval_at",
        "thinking_heartbeat_at",
        "last_decision_at",
    ):
        parsed = _parse_timestamp(existing.get(field_name))
        if parsed:
            marks.append(parsed)
    for track in tracks:
        parsed = _parse_timestamp(state_by_track.get(_as_text(track.get("track_id")), {}).get("updated_at"))
        if parsed:
            marks.append(parsed)
    if not marks:
        return ""
    return max(marks).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _topic_materialization_state(
    existing: dict[str, Any],
    *,
    materialized_track_id: str,
    materialized_smoke_path: str,
) -> str:
    explicit = _as_text(existing.get("materialization_state"))
    if explicit:
        return explicit
    if materialized_track_id and materialized_smoke_path:
        return "materialized_smoke"
    if materialized_track_id:
        return "materialized_pending_smoke"
    if _as_text(existing.get("structured_handoff")):
        return "search_only"
    return ""


def _topic_reason_codes(
    *,
    existing: dict[str, Any],
    tracks: list[dict[str, Any]],
    state_by_track: dict[str, dict[str, Any]],
    latest_activity_at: str,
    materialization_state: str,
    materialized_track_id: str,
    materialized_smoke_path: str,
) -> list[str]:
    reason_codes: list[str] = []
    latest_activity = _parse_timestamp(latest_activity_at)
    if latest_activity and latest_activity < datetime.now(timezone.utc) - timedelta(days=2):
        reason_codes.append("aged_2d")
    elif not latest_activity and (materialization_state or tracks):
        reason_codes.append("aged_2d")
    if materialization_state in {"search_only", "materialized_pending_smoke"} and not _as_text(materialized_smoke_path):
        reason_codes.append("search_only_no_materialization")
    if materialization_state == "materialized_pending_smoke" and materialized_track_id and not _as_text(materialized_smoke_path):
        reason_codes.append("no_new_smoke")
    if not reason_codes and _as_text(existing.get("last_decision_summary")).strip():
        summary = _as_text(existing.get("last_decision_summary"))
        if "wait" in summary.lower() or "等待" in summary:
            reason_codes.append("queue_unchanged")
    seen: set[str] = set()
    ordered: list[str] = []
    for code in reason_codes:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def _topic_pivot_reason_codes(
    *,
    existing: dict[str, Any],
    materialized_track_id: str,
    structured_handoff: dict[str, Any],
) -> list[str]:
    pivot_reason_codes: list[str] = []
    if _as_text(structured_handoff.get("thread_id")) and (
        materialized_track_id or _as_text(existing.get("materialization_state")) in {"search_only", "materialized_pending_smoke"}
    ):
        pivot_reason_codes.append("fresh_thread_and_new_track")
    elif materialized_track_id:
        pivot_reason_codes.append("new_track_materialized")
    explicit = existing.get("pivot_reason_codes")
    if isinstance(explicit, list):
        for item in explicit:
            text = _as_text(item)
            if text and text not in pivot_reason_codes:
                pivot_reason_codes.append(text)
    return pivot_reason_codes


def _topic_search_budget_state(existing: dict[str, Any]) -> dict[str, Any]:
    payload = _coerce_dict(existing.get("search_budget_state"))
    if payload:
        return payload
    fields = {key: existing.get(key) for key in ("queries", "evidence", "tool_calls", "budget_state") if key in existing}
    return dict(fields) if fields else {}


def _topic_tool_usage_summary(existing: dict[str, Any]) -> dict[str, Any]:
    payload = _coerce_dict(existing.get("tool_usage_summary"))
    if payload:
        return payload
    fields = {key: existing.get(key) for key in ("search_queries", "turn_items", "tool_calls") if key in existing}
    return dict(fields) if fields else {}


def _topic_from_existing(existing: dict[str, Any], *, topic_id: str, now: str) -> Topic:
    source_evidence_ids = _coerce_string_list(existing.get("source_evidence_ids"))
    materialized_track_id = _as_text(existing.get("materialized_track_id"))
    materialized_run_id = _as_text(existing.get("materialized_run_id"))
    materialized_smoke_path = _as_text(existing.get("materialized_smoke_path"))
    structured_handoff = _merge_structured_handoff(
        existing,
        topic_id=topic_id,
        materialized_track_id=materialized_track_id,
        materialized_run_id=materialized_run_id,
        source_evidence_ids=source_evidence_ids,
    )
    track_ids = _coerce_string_list(existing.get("proposed_tracks"))
    latest_activity_at = _as_text(existing.get("last_activity_at"))
    return Topic(
        topic_id=topic_id,
        title=_as_text(existing.get("title")) or _humanize_topic_title(topic_id),
        goal=_as_text(existing.get("goal")) or f"推进 {topic_id}",
        success_metric=_as_text(existing.get("success_metric")) or "待补齐",
        scope_label=_as_text(existing.get("scope_label")) or "research_probe",
        priority=float(existing.get("priority") or 0.5),
        status=_as_text(existing.get("status")) or "triaged",
        promotable=bool(existing.get("promotable", True)),
        blocked_reason=_as_text(existing.get("blocked_reason")),
        proposed_tracks=track_ids,
        source_evidence_ids=source_evidence_ids,
        created_by=_as_text(existing.get("created_by")) or "autobci-agent",
        thinking_heartbeat_at=_as_text(existing.get("thinking_heartbeat_at")) or latest_activity_at or _as_text(existing.get("last_decision_at")) or now,
        last_retrieval_at=_as_text(existing.get("last_retrieval_at")),
        last_decision_at=_as_text(existing.get("last_decision_at")) or now,
        last_judgment_at=_as_text(existing.get("last_judgment_at")),
        last_materialization_at=_as_text(existing.get("last_materialization_at")),
        last_smoke_at=_as_text(existing.get("last_smoke_at")),
        last_decision_summary=_as_text(existing.get("last_decision_summary")) or "保留人工立项 topic。",
        materialization_state=_topic_materialization_state(
            existing,
            materialized_track_id=materialized_track_id,
            materialized_smoke_path=materialized_smoke_path,
        ),
        materialized_track_id=materialized_track_id,
        materialized_run_id=materialized_run_id,
        materialized_smoke_path=materialized_smoke_path,
        structured_handoff=structured_handoff,
        stale_reason_codes=[],
        pivot_reason_codes=[],
        search_budget_state=_topic_search_budget_state(existing),
        tool_usage_summary=_topic_tool_usage_summary(existing),
        last_activity_at=latest_activity_at or now,
    )
def _extract_prefixed_line(text: str, prefixes: tuple[str, ...]) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for prefix in prefixes:
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
    return ""


def _current_problem_statement(paths: AutoBciControlPlanePaths, runtime: dict[str, Any]) -> str:
    runtime_statement = _as_text(runtime.get("last_research_judgment_update"))
    if runtime_statement.startswith("当前关键问题："):
        return runtime_statement.split("：", 1)[1].strip()

    research_tree = read_text(paths.research_tree)
    tree_statement = _extract_prefixed_line(research_tree, ("当前关键问题：",))
    if tree_statement:
        return tree_statement

    current_strategy = read_text(paths.current_strategy)
    strategy_statement = _extract_prefixed_line(current_strategy, ("当前关键问题：", "当前说明："))
    if strategy_statement:
        return strategy_statement

    if runtime_statement:
        return runtime_statement
    return DEFAULT_PROBLEM_STATEMENT


def _humanize_topic_title(topic_id: str) -> str:
    mapping = {
        MOONSHOT_TOPIC_ID: MOONSHOT_TITLE,
        "wave1_autonomous": "纯脑电新模型",
        "wave1_phase_state": "步态相位方向",
        "wave1_representation": "表征探索",
        "wave1_controls": "控制实验与对照",
        "relative_origin_xyz": "相对坐标结构线",
        "relative_origin_xyz_upper_bound": "同试次参考线",
        "canonical_mainline": "主线守线",
        "gait_phase_label_engineering": "步态标签工程",
        "gait_phase_eeg_classification": "步态脑电二分类",
    }
    return mapping.get(topic_id, topic_id.replace("_", " "))


def _topic_priority(topic_id: str, *, promotable: bool, is_control: bool) -> float:
    priority_map = {
        MOONSHOT_TOPIC_ID: 1.0,
        "wave1_autonomous": 0.92,
        "wave1_phase_state": 0.86,
        "gait_phase_eeg_classification": 0.84,
        "gait_phase_label_engineering": 0.48,
        "wave1_representation": 0.78,
        "canonical_mainline": 0.6,
        "relative_origin_xyz": 0.42,
        "relative_origin_xyz_upper_bound": 0.35,
        "wave1_controls": 0.2,
    }
    if topic_id in priority_map:
        return priority_map[topic_id]
    if is_control:
        return 0.2
    if promotable:
        return 0.55
    return 0.4


def _topic_goal(topic_id: str, tracks: list[dict[str, Any]]) -> str:
    if topic_id == MOONSHOT_TOPIC_ID:
        return MOONSHOT_GOAL
    if topic_id == "gait_phase_label_engineering":
        return "先把步态标签切稳，固定能直接给脑电侧使用的支撑/摆动参考标签。"
    if topic_id == "gait_phase_eeg_classification":
        return "验证脑电能不能把支撑和摆动这两个粗粒度步态状态分开，并比较不同算法家族、窗长和固定时延。"
    for track in tracks:
        goal = _as_text(track.get("track_goal"))
        if goal:
            return goal
    return f"推进 {topic_id} 对应的研究方向。"


def _topic_success_metric(topic_id: str, *, promotable: bool, is_control: bool) -> str:
    if topic_id == MOONSHOT_TOPIC_ID:
        return MOONSHOT_SUCCESS_METRIC
    if topic_id == "gait_phase_label_engineering":
        return "形成稳定可用的支撑/摆动参考标签，供后续脑电二分类使用"
    if topic_id == "gait_phase_eeg_classification":
        return "在固定步态标签下，让脑电支撑/摆动二分类稳定高于随机，并能比较不同算法家族"
    if is_control:
        return "提供控制对照，不进入主线晋升"
    if promotable:
        return "提升当前最可信的纯脑电正式结果"
    return "形成可解释的辅助证据"


def _topic_scope_label(topic_id: str, *, promotable: bool, is_control: bool) -> str:
    if topic_id == MOONSHOT_TOPIC_ID:
        return MOONSHOT_SCOPE_LABEL
    if topic_id == "gait_phase_label_engineering":
        return "gait_phase_label_engineering"
    if topic_id == "gait_phase_eeg_classification":
        return "gait_phase_eeg_classification"
    if is_control:
        return "control_reference"
    if promotable:
        return "cross_session_pure_brain"
    return "research_probe"


def _track_promotable(track: dict[str, Any], state: dict[str, Any] | None) -> bool:
    if state and state.get("promotable") is not None:
        return bool(state.get("promotable"))
    track_id = _as_text(track.get("track_id"))
    if track_id in {"kinematics_only_baseline", "hybrid_brain_plus_kinematics"}:
        return False
    if track_id.startswith("tree_calibration_") or track_id.startswith("relative_origin_xyz_upper_bound"):
        return False
    return True


def _track_is_control(track: dict[str, Any], state: dict[str, Any] | None) -> bool:
    series_class = _as_text((state or {}).get("series_class") or track.get("series_class")).lower()
    if series_class == "control":
        return True
    track_id = _as_text(track.get("track_id"))
    return track_id in {"kinematics_only_baseline", "hybrid_brain_plus_kinematics"} or track_id.startswith("tree_calibration_")


def _manifest_tracks(paths: AutoBciControlPlanePaths) -> list[dict[str, Any]]:
    payload = read_json(paths.track_manifest, {})
    if isinstance(payload, dict):
        raw_tracks = payload.get("tracks", [])
    else:
        raw_tracks = []
    return [item for item in raw_tracks if isinstance(item, dict) and _as_text(item.get("track_id"))]


def _status_track_map(paths: AutoBciControlPlanePaths) -> dict[str, dict[str, Any]]:
    payload = read_json(paths.autoresearch_status, {})
    raw_states = payload.get("track_states", []) if isinstance(payload, dict) else []
    return {
        _as_text(item.get("track_id")): item
        for item in raw_states
        if isinstance(item, dict) and _as_text(item.get("track_id"))
    }


def _group_manifest_tracks(paths: AutoBciControlPlanePaths) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for track in _manifest_tracks(paths):
        track_id = _as_text(track.get("track_id"))
        runner_family = normalize_algorithm_family(track.get("runner_family"), track_id=track_id)
        topic_id = _as_text(track.get("topic_id"))
        if not topic_id:
            direction = resolve_direction_spec(paths, track_id=track_id, algorithm_family=runner_family)
            topic_id = _as_text((direction or {}).get("tag")) or f"track::{track_id}"
        grouped.setdefault(topic_id, []).append(track)
    return grouped


def _default_moonshot_topic(now: str) -> Topic:
    return Topic(
        topic_id=MOONSHOT_TOPIC_ID,
        title=MOONSHOT_TITLE,
        goal=MOONSHOT_GOAL,
        success_metric=MOONSHOT_SUCCESS_METRIC,
        scope_label=MOONSHOT_SCOPE_LABEL,
        priority=1.0,
        status="runnable",
        promotable=True,
        blocked_reason="",
        proposed_tracks=[MOONSHOT_TRACK_PLACEHOLDER],
        source_evidence_ids=[],
        created_by="autobci-agent",
        last_decision_at=now,
        last_decision_summary="继续保留纯脑电 moonshot 为最高优先级。",
    )


def build_topics(paths: AutoBciControlPlanePaths) -> list[dict[str, Any]]:
    runtime = read_json(paths.runtime_state, {})
    status = read_json(paths.autoresearch_status, {})
    state_by_track = _status_track_map(paths)
    grouped = _group_manifest_tracks(paths)
    active_track_id = _as_text(status.get("active_track_id"))
    now = utcnow()

    topics_by_id: dict[str, Topic] = {MOONSHOT_TOPIC_ID: _default_moonshot_topic(now)}
    existing_by_id: dict[str, dict[str, Any]] = {
        _as_text(item.get("topic_id")): item
        for item in read_topics_inbox(paths.topics_inbox)
        if isinstance(item, dict) and _as_text(item.get("topic_id"))
    }

    for topic_id, tracks in grouped.items():
        track_ids = [_as_text(track.get("track_id")) for track in tracks]
        matched_states = [state_by_track[track_id] for track_id in track_ids if track_id in state_by_track]
        promotable = any(_track_promotable(track, state_by_track.get(_as_text(track.get("track_id")))) for track in tracks)
        is_control = all(_track_is_control(track, state_by_track.get(_as_text(track.get("track_id")))) for track in tracks)
        existing = existing_by_id.pop(topic_id, {})
        if topic_id == MOONSHOT_TOPIC_ID:
            topic_status = "running" if active_track_id in track_ids else "runnable"
        elif active_track_id and active_track_id in track_ids and matched_states:
            topic_status = "running"
        elif matched_states:
            topic_status = "queued"
        else:
            topic_status = "triaged"

        if existing:
            topic = _topic_from_existing(existing, topic_id=topic_id, now=now)
            topic.title = _as_text(existing.get("title")) or _humanize_topic_title(topic_id)
            topic.goal = _as_text(existing.get("goal")) or _topic_goal(topic_id, tracks)
            topic.success_metric = _as_text(existing.get("success_metric")) or _topic_success_metric(topic_id, promotable=promotable, is_control=is_control)
            topic.scope_label = _as_text(existing.get("scope_label")) or _topic_scope_label(topic_id, promotable=promotable, is_control=is_control)
            topic.priority = float(existing.get("priority") or _topic_priority(topic_id, promotable=promotable, is_control=is_control))
            topic.status = _as_text(existing.get("status")) or topic_status
            topic.promotable = bool(existing.get("promotable", promotable))
            topic.proposed_tracks = track_ids or topic.proposed_tracks
            topic.source_evidence_ids = _coerce_string_list(existing.get("source_evidence_ids")) or topic.source_evidence_ids
            topic.last_decision_at = _as_text(existing.get("last_decision_at")) or now
            topic.last_decision_summary = _as_text(existing.get("last_decision_summary")) or "保持当前优先级，等待新的 smoke/formal 结果。"
        else:
            topic = Topic(
                topic_id=topic_id,
                title=_humanize_topic_title(topic_id),
                goal=_topic_goal(topic_id, tracks),
                success_metric=_topic_success_metric(topic_id, promotable=promotable, is_control=is_control),
                scope_label=_topic_scope_label(topic_id, promotable=promotable, is_control=is_control),
                priority=_topic_priority(topic_id, promotable=promotable, is_control=is_control),
                status=topic_status,
                promotable=promotable,
                blocked_reason="",
                proposed_tracks=track_ids,
                source_evidence_ids=[],
                created_by="autobci-agent",
                thinking_heartbeat_at=now,
                last_decision_at=now,
                last_decision_summary="保持当前优先级，等待新的 smoke/formal 结果。",
            )
        latest_activity_at = _topic_latest_activity_at(existing=asdict(topic), tracks=tracks, state_by_track=state_by_track)
        materialization_state = _topic_materialization_state(
            asdict(topic),
            materialized_track_id=topic.materialized_track_id,
            materialized_smoke_path=topic.materialized_smoke_path,
        )
        structured_handoff = _merge_structured_handoff(
            asdict(topic),
            topic_id=topic_id,
            materialized_track_id=topic.materialized_track_id,
            materialized_run_id=topic.materialized_run_id,
            source_evidence_ids=topic.source_evidence_ids,
        )
        topic.thinking_heartbeat_at = _as_text(topic.thinking_heartbeat_at) or latest_activity_at or now
        topic.last_activity_at = latest_activity_at or _as_text(topic.thinking_heartbeat_at) or now
        topic.materialization_state = materialization_state
        topic.structured_handoff = structured_handoff
        topic.stale_reason_codes = _topic_reason_codes(
            existing=asdict(topic),
            tracks=tracks,
            state_by_track=state_by_track,
            latest_activity_at=topic.last_activity_at,
            materialization_state=topic.materialization_state,
            materialized_track_id=topic.materialized_track_id,
            materialized_smoke_path=topic.materialized_smoke_path,
        )
        topic.pivot_reason_codes = _topic_pivot_reason_codes(
            existing=asdict(topic),
            materialized_track_id=topic.materialized_track_id,
            structured_handoff=topic.structured_handoff,
        )
        topic.search_budget_state = _topic_search_budget_state(asdict(topic))
        topic.tool_usage_summary = _topic_tool_usage_summary(asdict(topic))
        topics_by_id[topic_id] = topic

    for topic_id, existing in existing_by_id.items():
        if topic_id in topics_by_id:
            continue
        topic = _topic_from_existing(existing, topic_id=topic_id, now=now)
        topic.last_activity_at = _topic_latest_activity_at(existing=asdict(topic), tracks=[], state_by_track=state_by_track) or _as_text(topic.last_activity_at) or now
        topic.stale_reason_codes = _topic_reason_codes(
            existing=asdict(topic),
            tracks=[],
            state_by_track=state_by_track,
            latest_activity_at=topic.last_activity_at,
            materialization_state=topic.materialization_state,
            materialized_track_id=topic.materialized_track_id,
            materialized_smoke_path=topic.materialized_smoke_path,
        )
        topic.pivot_reason_codes = _topic_pivot_reason_codes(
            existing=asdict(topic),
            materialized_track_id=topic.materialized_track_id,
            structured_handoff=topic.structured_handoff,
        )
        topics_by_id[topic_id] = topic

    ordered = sorted(
        (topic.to_dict() for topic in topics_by_id.values()),
        key=lambda item: (
            -(float(item.get("priority") or 0.0)),
            _sort_timestamp(item.get("last_decision_at")),
            item.get("topic_id", ""),
        ),
        reverse=False,
    )
    return ordered


def build_hard_constraints(paths: AutoBciControlPlanePaths) -> list[str]:
    constraints: list[str] = []
    for text in (
        read_text(paths.repo_root / "AGENTS.md"),
        read_text(paths.repo_root / "docs" / "CONSTITUTION.md"),
    ):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.")):
                constraints.append(line.lstrip("-*1234567890. ").strip())
    direction_tags = read_json(paths.direction_tags, {})
    priority_statement = _as_text(direction_tags.get("priority_statement")) if isinstance(direction_tags, dict) else ""
    flow_note = _as_text(direction_tags.get("flow_note")) if isinstance(direction_tags, dict) else ""
    if priority_statement:
        constraints.append(priority_statement)
    if flow_note:
        constraints.append(flow_note)
    return constraints[:10]


def _latest_run_id_for_topic(paths: AutoBciControlPlanePaths, topic_id: str) -> str:
    rows = [
        row
        for row in read_jsonl(paths.experiment_ledger)
        if _as_text(row.get("topic_id")) == topic_id and _as_text(row.get("run_id"))
    ]
    if not rows:
        return ""
    rows.sort(key=lambda item: _sort_timestamp(item.get("recorded_at")), reverse=True)
    return _as_text(rows[0].get("run_id"))


def _topic_history(paths: AutoBciControlPlanePaths, topic_id: str) -> list[dict[str, Any]]:
    rows = [row for row in read_jsonl(paths.experiment_ledger) if _as_text(row.get("topic_id")) == topic_id]
    rows.sort(key=lambda item: _sort_timestamp(item.get("recorded_at")), reverse=True)
    return rows[:5]


def _relevant_evidence(paths: AutoBciControlPlanePaths, topic_id: str) -> list[dict[str, Any]]:
    rows = [row for row in read_jsonl(paths.research_evidence) if isinstance(row, dict)]
    rows.sort(key=lambda item: _sort_timestamp(item.get("recorded_at")), reverse=True)
    evidence: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:3], start=1):
        payload = dict(row)
        payload.setdefault("evidence_id", f"evidence_{index:03d}")
        payload.setdefault("topic_id", topic_id)
        evidence.append(payload)
    return evidence


def _similar_hypothesis_history(paths: AutoBciControlPlanePaths, topic_id: str) -> list[dict[str, Any]]:
    rows = [row for row in read_jsonl(paths.hypothesis_log) if _as_text(row.get("topic_id")) == topic_id]
    rows.sort(key=lambda item: _sort_timestamp(item.get("recorded_at")), reverse=True)
    return rows[:3]


def build_retrieval_packet(paths: AutoBciControlPlanePaths, topics: list[dict[str, Any]]) -> dict[str, Any]:
    runtime = read_json(paths.runtime_state, {})
    status = read_json(paths.autoresearch_status, {})
    active_track_id = _as_text(status.get("active_track_id"))
    active_topic_id = ""
    for topic in topics:
        if active_track_id and active_track_id in topic.get("proposed_tracks", []):
            active_topic_id = _as_text(topic.get("topic_id"))
            break
    if not active_topic_id and topics:
        active_topic_id = _as_text(topics[0].get("topic_id"))

    packet = RetrievalPacket(
        current_problem_statement=_current_problem_statement(paths, runtime),
        hard_constraints=build_hard_constraints(paths),
        runtime_snapshot={
            "campaign_id": _as_text(status.get("campaign_id")),
            "stage": _as_text(status.get("stage")),
            "campaign_mode": _as_text(status.get("campaign_mode")),
            "active_track_id": active_track_id,
            "current_task": _as_text(runtime.get("current_task")),
        },
        topic_history=_topic_history(paths, active_topic_id),
        similar_hypothesis_history=_similar_hypothesis_history(paths, active_topic_id),
        relevant_evidence=_relevant_evidence(paths, active_topic_id),
        budget_and_queue_state={
            "manifest_track_count": len(_manifest_tracks(paths)),
            "current_candidates": runtime.get("current_candidates") or [],
            "active_track_id": active_track_id,
        },
    )
    return packet.to_dict()


def _queue_items(paths: AutoBciControlPlanePaths) -> list[dict[str, Any]]:
    status = read_json(paths.autoresearch_status, {})
    state_by_track = _status_track_map(paths)
    active_track_id = _as_text(status.get("active_track_id"))
    ordered_items: list[dict[str, Any]] = []
    for manifest_index, track in enumerate(_manifest_tracks(paths)):
        track_id = _as_text(track.get("track_id"))
        state = state_by_track.get(track_id, {})
        promotable = _track_promotable(track, state)
        is_control = _track_is_control(track, state)
        ordered_items.append(
            {
                "track_id": track_id,
                "promotable": promotable,
                "is_control": is_control,
                "is_active": track_id == active_track_id,
                "updated_at": _as_text(state.get("updated_at")),
                "val_r": state.get("latest_val_primary_metric"),
                "manifest_index": manifest_index,
            }
        )
    ordered_items.sort(
        key=lambda item: (
            0 if item["is_active"] else 1,
            0 if item["promotable"] else 1,
            0 if not item["is_control"] else 1,
            -float(item["val_r"] or -1e9),
            item["manifest_index"],
        )
    )
    return ordered_items


def _stale_topics(paths: AutoBciControlPlanePaths, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for topic in topics:
        topic_id = _as_text(topic.get("topic_id"))
        if topic_id == MOONSHOT_TOPIC_ID:
            continue
        reason_codes = [code for code in _coerce_string_list(topic.get("stale_reason_codes")) if code]
        if reason_codes:
            stale.append(
                {
                    "topic_id": topic_id,
                    "reason": "；".join(reason_codes),
                    "reason_codes": reason_codes,
                    "pivot_reason_codes": _coerce_string_list(topic.get("pivot_reason_codes")),
                    "materialization_state": _as_text(topic.get("materialization_state")),
                    "last_activity_at": _as_text(topic.get("last_activity_at")),
                    "handoff": _coerce_dict(topic.get("structured_handoff")),
                    "search_budget_state": _coerce_dict(topic.get("search_budget_state")),
                    "tool_usage_summary": _coerce_dict(topic.get("tool_usage_summary")),
                }
            )
    return stale


def build_decision_packet(paths: AutoBciControlPlanePaths, topics: list[dict[str, Any]], retrieval_packet: dict[str, Any]) -> dict[str, Any]:
    queue_items = _queue_items(paths)
    recommended_queue = [item["track_id"] for item in queue_items if item["promotable"]]
    recommended_formal_candidates = recommended_queue[:2]
    stale_topics = _stale_topics(paths, topics)
    if "纯脑电" in _as_text(retrieval_packet.get("current_problem_statement")):
        judgment_delta = "继续优先纯脑电突破，先把当前主线和 phase 条件路线留在推荐队列最前。"
    else:
        judgment_delta = "继续按当前关键问题推进，先保持最可信候选在队列前排。"
    packet = DecisionPacket(
        current_problem_statement=_as_text(retrieval_packet.get("current_problem_statement")) or DEFAULT_PROBLEM_STATEMENT,
        recommended_topic_updates=[
            {
                "topic_id": _as_text(topic.get("topic_id")),
                "status": _as_text(topic.get("status")),
                "priority": float(topic.get("priority") or 0.0),
            }
            for topic in topics[:10]
        ],
        recommended_queue=recommended_queue,
        recommended_formal_candidates=recommended_formal_candidates,
        stale_topics_to_deprioritize=stale_topics,
        research_judgment_delta=judgment_delta,
    )
    return packet.to_dict()


def build_hypothesis_entry(topics: list[dict[str, Any]], retrieval_packet: dict[str, Any]) -> dict[str, Any]:
    recorded_at = utcnow()
    hypothesis_id = f"hyp_{recorded_at.replace('-', '_').replace(':', '_').replace('T', '_').replace('Z', '')}"
    topic_id = _as_text((topics[0] if topics else {}).get("topic_id")) or MOONSHOT_TOPIC_ID
    return {
        "recorded_at": recorded_at,
        "topic_id": topic_id,
        "hypothesis_id": hypothesis_id,
        "summary": _as_text(retrieval_packet.get("current_problem_statement")) or DEFAULT_PROBLEM_STATEMENT,
        "status": "open",
    }


def build_judgment_update(paths: AutoBciControlPlanePaths, topics: list[dict[str, Any]], decision_packet: dict[str, Any], hypothesis_entry: dict[str, Any]) -> dict[str, Any]:
    topic_id = _as_text(hypothesis_entry.get("topic_id")) or MOONSHOT_TOPIC_ID
    next_action = "继续推进推荐队列。"
    if decision_packet.get("recommended_formal_candidates"):
        next_action = f"优先 formal：{' / '.join(decision_packet['recommended_formal_candidates'])}"
    judgment = JudgmentUpdate(
        recorded_at=utcnow(),
        run_id=_latest_run_id_for_topic(paths, topic_id),
        topic_id=topic_id,
        hypothesis_id=_as_text(hypothesis_entry.get("hypothesis_id")),
        outcome="inconclusive",
        reason=_as_text(decision_packet.get("research_judgment_delta")) or "等待新的 smoke/formal 结果。",
        queue_update="keep_active" if decision_packet.get("recommended_queue") else "triage",
        next_recommended_action=next_action,
    )
    return judgment.to_dict()
