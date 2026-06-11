from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable


TitleModelFn = Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class TitleSuggestion:
    topic_title: str
    attempt_title: str
    task_fingerprint: str
    tags: list[str]
    debug_flag: bool
    title_source: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clip_text(value: str, limit: int = 18) -> str:
    value = " ".join(value.strip().split())
    if len(value) <= limit:
        return value
    return value[:limit]


def _input_mode(program: dict[str, Any]) -> str:
    goal = program.get("research_goal") if isinstance(program.get("research_goal"), dict) else {}
    task_type = _text(goal.get("task_type")).lower()
    search_space = program.get("search_space") if isinstance(program.get("search_space"), dict) else {}
    feature_families = search_space.get("allowed_feature_families")
    feature_text = " ".join(str(item).lower() for item in feature_families) if isinstance(feature_families, list) else ""
    program_id = _text(program.get("program_id")).lower()
    if "cross_modal" in task_type or "crossmodal" in program_id:
        return "cross_modal"
    if "image" in task_type or "image" in feature_text or "image_only" in program_id:
        return "image_only"
    if "gait" in program_id or "ecog" in feature_text:
        return "ecog_only"
    return "unspecified"


def _fingerprint_payload(program: dict[str, Any]) -> dict[str, str]:
    goal = program.get("research_goal") if isinstance(program.get("research_goal"), dict) else {}
    data_boundary = program.get("data_boundary") if isinstance(program.get("data_boundary"), dict) else {}
    labels = program.get("label_definition") if isinstance(program.get("label_definition"), dict) else {}
    metrics = program.get("metrics") if isinstance(program.get("metrics"), dict) else {}
    return {
        "dataset_name": _text(data_boundary.get("dataset_name")),
        "task_type": _text(goal.get("task_type")),
        "input_mode": _input_mode(program),
        "label_source": _text(labels.get("source")),
        "primary_metric": _text(metrics.get("primary")),
        "program_id": _text(program.get("program_id")),
    }


def task_fingerprint_for_program(program: dict[str, Any]) -> str:
    payload = _fingerprint_payload(program)
    required_signal = [
        payload.get("dataset_name", ""),
        payload.get("task_type", ""),
        payload.get("label_source", ""),
        payload.get("primary_metric", ""),
        payload.get("program_id", ""),
    ]
    if not any(required_signal):
        return ""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]
    return f"task-{digest}"


def _fallback_topic_title(program: dict[str, Any], user_message: str) -> tuple[str, list[str]]:
    program_id = _text(program.get("program_id"))
    mode = _input_mode(program)
    if program_id == "rsvp_ship_image_only_v0" or (mode == "image_only" and "ship" in user_message.lower()):
        return "纯图像船只二分类", ["rsvp", "ship", "image-only", "binary"]
    if program_id == "rsvp_ship_crossmodal_v0" or mode == "cross_modal":
        return "图像与脑电船只识别对照", ["rsvp", "ship", "cross-modal", "binary"]
    if program_id == "gait_phase_binary_v0":
        return "步态二分类", ["gait", "ecog", "binary"]
    goal = program.get("research_goal") if isinstance(program.get("research_goal"), dict) else {}
    statement = _text(goal.get("statement"))
    if statement:
        return _clip_text(statement, 16), []
    if program_id:
        return program_id, []
    if user_message:
        return _clip_text(user_message, 16), []
    return "未归组任务", []


def _attempt_base(topic_title: str, program: dict[str, Any], debug_flag: bool) -> str:
    program_id = _text(program.get("program_id"))
    if debug_flag and program_id == "rsvp_ship_image_only_v0":
        return "Intake 到 Director 调试"
    if program_id == "rsvp_ship_image_only_v0":
        return "纯图像船只二分类尝试"
    if program_id == "rsvp_ship_crossmodal_v0":
        return "跨模态船只识别尝试"
    if program_id == "gait_phase_binary_v0":
        return "步态二分类尝试"
    if debug_flag:
        return f"{topic_title}调试"
    return f"{topic_title}尝试"


class TitleService:
    def __init__(self, model_title_fn: TitleModelFn | None = None) -> None:
        self._model_title_fn = model_title_fn

    def suggest(
        self,
        *,
        user_message: str = "",
        program_draft: dict[str, Any] | None = None,
        attempt_index: int = 1,
        debug_flag: bool = False,
    ) -> TitleSuggestion:
        program = program_draft if isinstance(program_draft, dict) else {}
        model_payload = self._model_title_fn({"user_message": user_message, "program_draft": program}) if self._model_title_fn else None
        task_fingerprint = task_fingerprint_for_program(program)
        if isinstance(model_payload, dict) and _text(model_payload.get("topic_title")):
            topic_title = _clip_text(_text(model_payload.get("topic_title")), 18)
            tags = [str(item) for item in model_payload.get("tags", [])] if isinstance(model_payload.get("tags"), list) else []
            title_source = "model"
        elif task_fingerprint:
            topic_title, tags = _fallback_topic_title(program, user_message)
            title_source = "fallback_program"
        else:
            topic_title = _clip_text(user_message, 18) if user_message else "未归组任务"
            tags = []
            title_source = "first_user_excerpt" if user_message else "fallback_empty"

        index = max(1, int(attempt_index or 1))
        attempt_base = _attempt_base(topic_title, program, debug_flag)
        attempt_title = f"{attempt_base} #{index:02d}"
        return TitleSuggestion(
            topic_title=topic_title,
            attempt_title=attempt_title,
            task_fingerprint=task_fingerprint,
            tags=tags,
            debug_flag=debug_flag,
            title_source=title_source,
        )
