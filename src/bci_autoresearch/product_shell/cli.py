from __future__ import annotations

import argparse
import getpass
import importlib
import io
import json
import os
import re
import shutil
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO, cast
from urllib.parse import urlencode
from urllib.request import urlopen

from bci_autoresearch.data_paths import (
    DEFAULT_TASK_ID as DEFAULT_DATA_TASK_ID,
    apply_dataset_to_program_draft,
    clear_task_dataset_root,
    configured_task_dataset,
    data_paths_config_path,
    normalize_user_path,
    save_task_dataset_root,
)
from bci_autoresearch.control_plane import (
    build_digest_summary,
    build_status_snapshot,
    format_status_summary,
    get_control_plane_paths,
)
from bci_autoresearch.control_plane.builtin_patch_worker import builtin_patch_worker_status
from bci_autoresearch.control_plane.director_plan import (
    load_latest_director_plan,
    latest_director_plan_path,
    run_director_plan,
)
from bci_autoresearch.control_plane.research_loop import (
    TASK_ID as DEFAULT_RESEARCH_TASK_ID,
    STRUCTURE_SANDBOX_RUNNER_ENV,
    STRUCTURE_SANDBOX_TIMEOUT_ENV,
    append_research_trace_event,
    explain_research_track,
    preview_research_step,
    run_research_loop,
    status_research_loop,
    step_research_loop,
    stop_research_loop,
)
from bci_autoresearch.control_plane.programs import (
    ProgramContractError,
    validate_program_contract,
)
from bci_autoresearch.control_plane.research_control import (
    build_research_control_snapshot,
    build_research_tree,
    clear_goal,
    complete_goal,
    start_goal,
    start_perp,
    stop_perp,
)
from bci_autoresearch.control_plane.runtime_store import append_jsonl, read_json, read_jsonl, write_json_atomic
from bci_autoresearch.platform_support import (
    default_cache_root,
    default_execution_worktrees_root,
    detached_process_kwargs,
    is_windows,
    venv_python_path,
)
from bci_autoresearch.storage_guard import (
    DATASET_BUDGET_ENV,
    DEFAULT_MAX_DATASET_BYTES,
    check_storage_budget,
)
from bci_autoresearch.storage_optimizer import build_storage_optimization_report
from bci_autoresearch.product_shell.chat_actions import (
    append_shell_trace,
    build_confirmation_message,
    build_direct_result_message,
    build_help_message,
    build_intake_chat_message,
    classify_user_turn,
    draft_amendment,
    freeze_program_from_intent,
    draft_proposal,
    ensure_shell_session,
    launch_smoke,
    next_turn_id,
    normalize_request,
)
from bci_autoresearch.product_shell.lifecycle import (
    active_attempt_count as lifecycle_active_attempt_count,
    archive_topic as lifecycle_archive_topic,
    archive_project as lifecycle_archive_project,
    create_topic as lifecycle_create_topic,
    create_project as lifecycle_create_project,
    create_snapshot as lifecycle_create_snapshot,
    find_topic_by_fingerprint as lifecycle_find_topic_by_fingerprint,
    fork_project_from_snapshot,
    format_projects_list as format_lifecycle_projects_list,
    get_current_project as lifecycle_get_current_project,
    get_project as lifecycle_get_project,
    get_topic as lifecycle_get_topic,
    import_experiment_manifest,
    list_projects as lifecycle_list_projects,
    next_attempt_index as lifecycle_next_attempt_index,
    reset_current_run as lifecycle_reset_current_run,
    resume_project as lifecycle_resume_project,
    set_current_project as lifecycle_set_current_project,
    update_project as lifecycle_update_project,
    update_topic as lifecycle_update_topic,
)
from bci_autoresearch.product_shell.remote_bridge import (
    current_remote_bridge,
    start_remote_bridge,
    stop_remote_bridge,
)
from bci_autoresearch.product_shell.titles import TitleService

try:
    from rich.align import Align
    from rich.box import ROUNDED
    from rich.console import Console, Group, RenderableType
    from rich.live import Live
    from rich.layout import Layout
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table
    from rich.terminal_theme import TerminalTheme
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised through fallback path
    Console = None  # type: ignore[assignment]
    Group = None  # type: ignore[assignment]
    Live = None  # type: ignore[assignment]
    Layout = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Padding = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]
    TerminalTheme = None  # type: ignore[assignment]
    Align = None  # type: ignore[assignment]
    ROUNDED = None  # type: ignore[assignment]
    RenderableType = Any  # type: ignore[misc,assignment]
    RICH_AVAILABLE = False

try:
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory as PTAutoSuggestFromHistory
    from prompt_toolkit.application import Application as PTApplication
    from prompt_toolkit.completion import Completer as PTCompleter
    from prompt_toolkit.completion import Completion as PTCompletion
    from prompt_toolkit.cursor_shapes import CursorShape
    from prompt_toolkit.filters import Condition as PTCondition
    from prompt_toolkit.filters import to_filter as PTToFilter
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.history import History as PTHistory
    from prompt_toolkit.key_binding import KeyBindings as PTKeyBindings
    from prompt_toolkit.layout.containers import Float as PTFloat
    from prompt_toolkit.layout.containers import FloatContainer as PTFloatContainer
    from prompt_toolkit.layout.containers import ConditionalContainer as PTConditionalContainer
    from prompt_toolkit.layout.dimension import Dimension as PTDimension
    from prompt_toolkit.layout import HSplit as PTHSplit
    from prompt_toolkit.layout import Layout as PTLayout
    from prompt_toolkit.layout import ScrollablePane as PTScrollablePane
    from prompt_toolkit.layout import VSplit as PTVSplit
    from prompt_toolkit.layout import Window as PTWindow
    from prompt_toolkit.layout.controls import FormattedTextControl as PTFormattedTextControl
    from prompt_toolkit.layout.menus import CompletionsMenu as PTCompletionsMenu
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style as PTStyle
    from prompt_toolkit.widgets import Frame as PTFrame
    from prompt_toolkit.widgets import TextArea as PTTextArea

    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised through fallback path
    PTAutoSuggestFromHistory = None  # type: ignore[assignment]
    PTApplication = None  # type: ignore[assignment]
    PTCompleter = object  # type: ignore[assignment]
    PTCompletion = None  # type: ignore[assignment]
    CursorShape = None  # type: ignore[assignment]
    PTCondition = None  # type: ignore[assignment]
    PTToFilter = None  # type: ignore[assignment]
    StyleAndTextTuples = Any  # type: ignore[misc,assignment]
    FileHistory = None  # type: ignore[assignment]
    PTHistory = object  # type: ignore[assignment]
    PTKeyBindings = None  # type: ignore[assignment]
    PTFloat = None  # type: ignore[assignment]
    PTFloatContainer = None  # type: ignore[assignment]
    PTConditionalContainer = None  # type: ignore[assignment]
    PTDimension = None  # type: ignore[assignment]
    PTHSplit = None  # type: ignore[assignment]
    PTLayout = None  # type: ignore[assignment]
    PTScrollablePane = None  # type: ignore[assignment]
    PTVSplit = None  # type: ignore[assignment]
    PTWindow = None  # type: ignore[assignment]
    PTFormattedTextControl = None  # type: ignore[assignment]
    PTCompletionsMenu = None  # type: ignore[assignment]
    MouseEventType = None  # type: ignore[assignment]
    patch_stdout = None  # type: ignore[assignment]
    PTStyle = None  # type: ignore[assignment]
    PTFrame = None  # type: ignore[assignment]
    PTTextArea = None  # type: ignore[assignment]
    PROMPT_TOOLKIT_AVAILABLE = False


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8878
LIVE_REFRESH_PER_SECOND = 8
AUTO_REFRESH_INTERVAL_SECONDS = 2.0
ACTIVE_REVEAL_INTERVAL_SECONDS = 5.0
AGENT_HISTORY_LIMIT = 30
INTAKE_HISTORY_LIMIT = 80
INTAKE_AGENT_CONTEXT_HISTORY_LIMIT = 12
SYSTEM_EVENT_LIMIT = 12
PAGE_SCROLL_LINES = 8
TUI_OUTPUT_HISTORY_LIMIT = 80
TUI_STREAM_OUTPUT_VISIBLE_LIMIT = 4
TUI_TOOL_OUTPUT_LINE_LIMIT = 18
CLEAR_SCREEN = "\033[2J\033[H"
INTAKE_WELCOME = "描述你的研究任务，我会整理成研究计划、风险和下一步判断。"
INTAKE_COMPOSER_PLACEHOLDER = "直接描述任务，或输入 / 查看高级命令"
DEFAULT_INTAKE_AGENT_MODEL = "gpt-5.5"
DEFAULT_INTAKE_AGENT_TIMEOUT_SECONDS = 60.0
AUTOBCI_TUI_TEST_MODE_ENV = "AUTOBCI_TUI_TEST_MODE"
AUTOBCI_TUI_ENGINE_ENV = "AUTOBCI_TUI_ENGINE"
NON_TRANSCRIPT_ACTIONS = {
    "quit",
    "help",
    "status",
    "dashboard",
    "report",
    "program",
    "events",
    "details",
    "approve",
    "cancel",
    "judge",
    "guard",
    "plan",
    "new",
    "archive",
    "rename",
    "title",
    "clear",
    "experiments",
    "projects",
    "model",
    "model_select",
    "data",
    "data_set",
    "reasoning",
    "director",
    "director_select",
    "research",
    "remote",
    "switch",
    "switch_select",
    "resume",
    "continue",
    "fork",
    "snapshot",
    "reset",
}
SLASH_COMMANDS = (
    "/new",
    "/data",
    "/new clean",
    "/plan",
    "/model",
    "/theme",
    "/reasoning",
    "/director",
    "/run",
    "/research",
    "/remote",
    "/tasks",
    "/switch",
    "/continue",
    "/projects",
    "/resume",
    "/snapshot",
    "/fork",
    "/archive",
    "/rename",
    "/title regenerate",
    "/clear",
    "/reset current run",
    "/dashboard",
    "/program show",
    "/approve",
    "/cancel",
    "/status",
    "/report latest",
    "/events",
    "/help",
    "/quit",
)
SLASH_MENU_COMMANDS = (
    "/new",
    "/data",
    "/run",
    "/model",
    "/tasks",
    "/dashboard",
)
SLASH_COMMAND_HELP = {
    "/new": "新起一个任务",
    "/data": "选择本地数据目录",
    "/new clean": "干净开始，不继承旧聊天",
    "/plan": "多轮制定 Program 计划",
    "/model": "切换模型",
    "/theme": "已废弃：TUI 已移除",
    "/reasoning": "切换推理调试显示",
    "/director": "调度研究方向队列",
    "/run": "开始或继续研究",
    "/research": "开始或继续研究",
    "/remote": "已废弃：改用 headless CLI / mobile gateway",
    "/tasks": "切换任务",
    "/switch": "切换任务",
    "/continue": "继续当前项目",
    "/projects": "列出已有任务",
    "/resume": "按任务 id 恢复",
    "/snapshot": "保存当前任务快照",
    "/fork": "从快照分叉新任务",
    "/archive": "归档当前尝试并开新尝试",
    "/rename": "重命名当前尝试",
    "/title regenerate": "重新生成标题",
    "/clear": "清空当前上下文并开新任务",
    "/reset current run": "重置当前 run 引用",
    "/dashboard": "打开真实运行态",
    "/program show": "查看任务契约",
    "/approve": "冻结草案或确认动作",
    "/cancel": "取消待确认动作",
    "/status": "查看当前状态",
    "/report latest": "查看最新摘要",
    "/events": "显示安全闸 / 评估事件",
    "/help": "查看命令",
    "/quit": "退出",
}
PROJECT_SWITCH_LIMIT = 20
SWITCH_NATURAL_LANGUAGE_HINTS = (
    "切换线程",
    "切换任务",
    "切换项目",
    "列出线程",
    "列出任务",
    "列出项目",
    "看看线程",
    "看看任务",
)
NEW_CLEAN_NATURAL_LANGUAGE_HINTS = (
    "新起一个 session",
    "新起一个任务",
    "新开一个 session",
    "新开一个任务",
    "新建一个 session",
    "新建一个任务",
    "重新开始",
)
SNAPSHOT_NATURAL_LANGUAGE_HINTS = (
    "保存当前状态",
    "保存状态",
    "保存快照",
    "存个快照",
)
FORK_NATURAL_LANGUAGE_HINTS = (
    "从这里分叉",
    "从当前状态分叉",
    "分叉当前任务",
)
MODEL_NATURAL_LANGUAGE_HINTS = (
    "切换模型",
    "换模型",
    "配置模型",
    "模型设置",
    "配置 minimax",
    "配置 minix",
    "填 minimax api",
    "填 minix api",
    "设置 api",
)
MODEL_STATUS_NATURAL_LANGUAGE_HINTS = (
    "你是什么模型",
    "你用的是什么模型",
    "现在是什么模型",
    "当前是什么模型",
    "当前模型",
    "intake 是什么模型",
    "intake模型",
    "which model",
    "what model",
)
DIRECTOR_NATURAL_LANGUAGE_HINTS = (
    "调试 director",
    "调试director",
    "director 调试",
    "生成研究队列",
    "研究队列",
    "让 director 排队",
    "让director排队",
)
MODEL_AGENT_OPTIONS = (
    {"name": "intake", "label": "计划/对话模型", "live": True, "note": "用于整理 Program 和日常对话"},
    {"name": "worker", "label": "代码 Worker 模型", "live": False, "note": "用于内置 patch worker 生成受限 JSON patch"},
)
MODEL_PROVIDER_ORDER = ("minimax-cn", "minimax", "xiaomi", "deepseek", "glm", "qwen", "kimi", "openai", "anthropic")
USER_STAGE_LABELS = {
    "Director": "方向选择",
    "Executor": "执行沙盒",
    "Judge": "结果复核",
    "Guard": "边界检查",
    "Research Memory": "研究记录",
    "Intake": "计划/对话",
    "Planner": "Program",
}

PALETTE = {
    "background": "#101318",
    "panel_bg": "#181d24",
    "panel_alt": "#1c222b",
    "border": "#55606f",
    "text": "#edf1f6",
    "muted": "#aab4c0",
    "accent": "#d0aa6f",
    "success": "#8bb8d9",
    "warning": "#d6c08f",
    "user_text": "#f0d49a",
    "agent_text": "#b7d7ee",
    "tool_text": "#d8d2c5",
}


class SlashCommandCompleter(PTCompleter):  # type: ignore[misc,valid-type]
    def get_completions(self, document: Any, complete_event: Any) -> Any:
        text_before_cursor = str(getattr(document, "text_before_cursor", "") or "")
        if "\n" in text_before_cursor:
            return
        prefix = text_before_cursor.lstrip()
        if not prefix.startswith("/"):
            return
        lowered_prefix = prefix.lower()
        for command in SLASH_MENU_COMMANDS:
            if command.lower().startswith(lowered_prefix):
                yield PTCompletion(
                    command,
                    start_position=-len(prefix),
                    display=command,
                    display_meta=SLASH_COMMAND_HELP.get(command, ""),
                )


def build_slash_command_completer() -> SlashCommandCompleter | None:
    if not PROMPT_TOOLKIT_AVAILABLE or PTCompletion is None:
        return None
    return SlashCommandCompleter()


def _build_full_width_completions_menu() -> Any:
    if PTCompletionsMenu is None:
        return None
    menu = PTCompletionsMenu(max_height=12, scroll_offset=1)
    if PTToFilter is not None and hasattr(menu, "content") and hasattr(menu.content, "dont_extend_width"):
        menu.content.dont_extend_width = PTToFilter(False)
    if PTFrame is not None:
        frame = PTFrame(menu, style="class:completion-frame")
        framed_menu = (
            PTConditionalContainer(frame, filter=menu.filter)
            if PTConditionalContainer is not None and hasattr(menu, "filter")
            else frame
        )
        setattr(framed_menu, "completion_menu", menu)
        setattr(framed_menu, "completion_frame", True)
        return framed_menu
    return menu


def _rewrite_lifecycle_natural_language(command: str) -> str:
    normalized = normalize_request(command)
    if not normalized or normalized.startswith("/"):
        return command
    lowered = normalized.lower()
    if any(hint in normalized or hint in lowered for hint in DIRECTOR_NATURAL_LANGUAGE_HINTS):
        return "/director"
    if any(hint in normalized or hint in lowered for hint in MODEL_STATUS_NATURAL_LANGUAGE_HINTS):
        return "/model current"
    if any(hint in normalized or hint in lowered for hint in MODEL_NATURAL_LANGUAGE_HINTS):
        return "/model"
    if any(hint in normalized or hint in lowered for hint in SWITCH_NATURAL_LANGUAGE_HINTS):
        return "/tasks"
    if any(hint in normalized or hint in lowered for hint in NEW_CLEAN_NATURAL_LANGUAGE_HINTS):
        return "/new"
    if any(hint in normalized or hint in lowered for hint in SNAPSHOT_NATURAL_LANGUAGE_HINTS):
        return "/snapshot"
    if any(hint in normalized or hint in lowered for hint in FORK_NATURAL_LANGUAGE_HINTS):
        return "/fork"
    return command


def _switch_attempt_visible(item: dict[str, Any], mode: str) -> bool:
    program_id = str(item.get("program_id") or "").strip()
    program_status = str(item.get("program_status") or "not_started").strip()
    status = str(item.get("status") or "").strip()
    title = str(item.get("title") or item.get("attempt_title") or "").strip()
    is_empty_attempt = (
        not program_id
        and program_status in {"", "-", "not_started"}
        and (status == "archived" or title in {"", "未命名实验", "未归组任务"})
        and not isinstance(item.get("pending_action"), dict)
    )
    if is_empty_attempt:
        return False
    if mode == "all":
        return True
    if mode == "debug":
        return bool(item.get("debug_flag"))
    return not bool(item.get("debug_flag"))


def _project_switch_options(paths: Any, *, mode: str = "default") -> list[dict[str, Any]]:
    current = lifecycle_get_current_project(paths)
    current_id = str(current.get("project_id") or current.get("experiment_id") or "")
    rows = [item for item in lifecycle_list_projects(paths) if _switch_attempt_visible(item, mode)]
    topics_by_id: dict[str, dict[str, Any]] = {}
    for item in rows:
        project_id = str(item.get("project_id") or item.get("experiment_id") or "").strip()
        if not project_id:
            continue
        topic_id = str(item.get("topic_id") or "").strip() or f"ungrouped:{project_id}"
        topic = topics_by_id.setdefault(
            topic_id,
            {
                "topic_id": topic_id,
                "topic_title": str(item.get("topic_title") or ("未归组任务" if topic_id.startswith("ungrouped:") else topic_id)),
                "topic_status": str(item.get("topic_status") or item.get("status") or "active"),
                "updated_at": str(item.get("updated_at") or ""),
                "attempts": [],
            },
        )
        if str(item.get("updated_at") or "") > str(topic.get("updated_at") or ""):
            topic["updated_at"] = str(item.get("updated_at") or "")
        topic["attempts"].append(
            {
                "project_id": project_id,
                "title": str(item.get("attempt_title") or item.get("title") or "未命名尝试"),
                "status": str(item.get("status") or "-"),
                "program_status": str(item.get("program_status") or "not_started"),
                "active_run_id": str(item.get("active_run_id") or "-"),
                "updated_at": str(item.get("updated_at") or "-"),
                "is_current": project_id == current_id,
                "debug_flag": bool(item.get("debug_flag")),
                "attempt_index": int(item.get("attempt_index") or 0),
            }
        )
    topics = list(topics_by_id.values())
    for topic in topics:
        topic["attempts"].sort(
            key=lambda item: (int(item.get("attempt_index") or 0), str(item.get("updated_at") or "")),
            reverse=True,
        )
    topics.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return topics[:PROJECT_SWITCH_LIMIT]


def _format_attempt_line(topic_index: int, attempt_index: int, item: dict[str, Any]) -> str:
    marker = "*" if item.get("is_current") else " "
    debug = " · debug" if item.get("debug_flag") else ""
    return (
        f"   {topic_index}.{attempt_index} {marker} {item.get('title') or '未命名尝试'}{debug} · "
        f"{item.get('status') or '-'} · Program:{item.get('program_status') or 'not_started'} · "
        f"Run:{item.get('active_run_id') or '-'}"
    )


def _format_project_switch_message(paths: Any, options: list[dict[str, Any]], *, mode: str = "default") -> str:
    if not options:
        hint = "输入 /new 新建一个任务。"
        if mode == "default":
            hint += " 如果在找调试任务，输入 /tasks --debug。"
        return f"当前还没有可切换的任务。{hint}"
    lines = [
        "选择要切换的任务：",
        "",
    ]
    for index, topic in enumerate(options, start=1):
        attempts = list(topic.get("attempts") or [])
        active_attempts = sum(1 for item in attempts if str(item.get("status") or "") == "active")
        lines.append(
            f"{index}. {topic.get('topic_title') or '未归组任务'} · "
            f"{len(attempts)} attempts · active:{active_attempts} · status:{topic.get('topic_status') or '-'}"
        )
        for attempt_index, attempt in enumerate(attempts[:3], start=1):
            lines.append(_format_attempt_line(index, attempt_index, attempt))
    lines.extend(["", "输入 1 展开 Topic；输入 1.2 直接切换到某次尝试。也可以输入 /new 新建。"])
    return "\n".join(lines)


def _format_topic_attempt_message(topic: dict[str, Any], *, topic_index: int) -> str:
    attempts = list(topic.get("attempts") or [])
    lines = [f"{topic_index}. {topic.get('topic_title') or '未归组任务'}：", ""]
    for attempt_index, attempt in enumerate(attempts, start=1):
        lines.append(_format_attempt_line(topic_index, attempt_index, attempt))
    lines.extend(["", f"输入编号切换，例如 {topic_index}.1 或 1。输入 /tasks 返回 Topic 列表。"])
    return "\n".join(lines)


def _open_project_switcher(paths: Any, session_state: dict[str, Any], *, mode: str = "default") -> str:
    options = _project_switch_options(paths, mode=mode)
    if not options:
        session_state.pop("selection_context", None)
        return _format_project_switch_message(paths, options, mode=mode)
    session_state["selection_context"] = {"kind": "topic_switch", "topics": options, "mode": mode}
    return _format_project_switch_message(paths, options, mode=mode)


def _project_switch_selection_token(command: str, session_state: dict[str, Any]) -> str | None:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict) or selection_context.get("kind") not in {"topic_switch", "topic_attempt_switch"}:
        return None
    stripped = command.strip()
    return stripped if re.fullmatch(r"\d+(?:\.\d+)?", stripped) else None


def _topic_from_context(selection_context: dict[str, Any], topic_index: int) -> dict[str, Any] | None:
    raw_topics = selection_context.get("topics")
    topics = list(raw_topics) if isinstance(raw_topics, list) else []
    if topic_index < 1 or topic_index > len(topics):
        return None
    return cast(dict[str, Any], topics[topic_index - 1])


def _resume_project_switch_option(paths: Any, session_state: dict[str, Any], token: int | str) -> str:
    token_text = str(token).strip()
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict) or selection_context.get("kind") not in {"topic_switch", "topic_attempt_switch"}:
        selection_context = {"kind": "topic_switch", "topics": _project_switch_options(paths), "mode": "default"}
    if selection_context.get("kind") == "topic_switch":
        if "." in token_text:
            topic_text, attempt_text = token_text.split(".", 1)
            topic_index = int(topic_text)
            attempt_index = int(attempt_text)
            topic = _topic_from_context(selection_context, topic_index)
            if topic is None:
                return f"没有第 {topic_index} 个任务。请输入列表里的编号，或输入 /tasks 重新查看。"
        else:
            topic_index = int(token_text)
            topic = _topic_from_context(selection_context, topic_index)
            if topic is None:
                return f"没有第 {topic_index} 个任务。请输入列表里的编号，或输入 /tasks 重新查看。"
            session_state["selection_context"] = {
                "kind": "topic_attempt_switch",
                "topic_index": topic_index,
                "topic": topic,
                "topics": selection_context.get("topics"),
            }
            return _format_topic_attempt_message(topic, topic_index=topic_index)
    else:
        topic = cast(dict[str, Any], selection_context.get("topic") or {})
        topic_index = int(selection_context.get("topic_index") or 1)
        if "." in token_text:
            topic_text, attempt_text = token_text.split(".", 1)
            topic_index = int(topic_text)
            parent = {"topics": selection_context.get("topics")}
            topic = _topic_from_context(parent, topic_index) or topic
            attempt_index = int(attempt_text)
        else:
            attempt_index = int(token_text)
    attempts = list(topic.get("attempts") or [])
    if attempt_index < 1 or attempt_index > len(attempts):
        return f"没有第 {token_text} 个任务。请输入列表里的编号，或输入 /tasks 重新查看。"
    target = attempts[attempt_index - 1]
    project_id = str(target.get("project_id") or "").strip()
    if not project_id:
        return f"第 {token_text} 个任务缺少 project id。请输入 /tasks 重新查看。"
    manifest, missing_refs = resume_experiment_workspace(paths, session_state, project_id)
    session_state.pop("selection_context", None)
    title = str(manifest.get("title") or target.get("title") or project_id)
    message = f"已切换到任务 {topic_index}.{attempt_index}：{title}（{project_id}）。"
    if isinstance(manifest.get("pending_action"), dict):
        message += " 已恢复一个等待确认的动作，可以继续 approve 或 cancel。"
    if missing_refs:
        message += " 但有部分历史 artifact 缺失，需要重新生成或重新确认。"
    return message


def _model_selection_number(command: str, session_state: dict[str, Any]) -> int | None:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict):
        return None
    if not str(selection_context.get("kind") or "").startswith("model_"):
        return None
    stripped = command.strip()
    return int(stripped) if stripped.isdigit() else None


def _model_manual_text(command: str, session_state: dict[str, Any]) -> str | None:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict) or selection_context.get("kind") != "model_manual_model":
        return None
    text = command.strip()
    return text or None


def _data_path_input_text(command: str, session_state: dict[str, Any]) -> str | None:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict) or selection_context.get("kind") != "data_path_input":
        return None
    stripped = str(command or "").strip()
    first_token = stripped.split(maxsplit=1)[0] if stripped else ""
    if first_token in SLASH_COMMANDS:
        session_state.pop("selection_context", None)
        return None
    if stripped.lower() in {"cancel", "取消", "返回", "退出", "no", "n"}:
        session_state.pop("selection_context", None)
        return "__cancel__"
    return stripped or None


def _format_data_path_status(repo_root: Path) -> str:
    record = configured_task_dataset(repo_root, task_id=DEFAULT_DATA_TASK_ID)
    lines = ["本地数据目录配置：", ""]
    if isinstance(record, dict):
        root = Path(str(record.get("dataset_root") or "")).expanduser()
        exists = root.exists() and root.is_dir()
        lines.extend(
            [
                f"- dataset_name: {record.get('dataset_name') or root.name or '-'}",
                f"- dataset_root: {root}",
                f"- status: {'ready' if exists else 'missing'}",
                f"- source: {record.get('source') or 'local'}",
            ]
        )
        if exists:
            try:
                budget = check_storage_budget(
                    root,
                    purpose="BCI dataset",
                    env_var=DATASET_BUDGET_ENV,
                    default_max_bytes=DEFAULT_MAX_DATASET_BYTES,
                )
                lines.append(
                    f"- storage_budget: {budget.as_dict()['current_human']} / "
                    f"{budget.as_dict()['max_human']} ({'ok' if budget.ok else 'over_limit'})"
                )
            except Exception as exc:
                lines.append(f"- storage_budget: unavailable ({type(exc).__name__}: {exc})")
    else:
        lines.append("- 当前还没有配置本地 BCI 数据目录。")
    lines.extend(
        [
            "",
            "设置方式：",
            "- CLI：autobci data set /absolute/path/to/dataset。",
            "- 对话入口：让 Claude Code、Codex、Cursor 或 Hermes 调用上面的 CLI。",
            "- 环境变量：AUTOBCI_DATASET_ROOT=/path/to/dataset。",
            f"- 本地配置文件：{data_paths_config_path(repo_root)}",
        ]
    )
    return "\n".join(lines)


def _format_storage_audit_report(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    roots = report.get("roots") if isinstance(report.get("roots"), list) else []
    recommendations = report.get("recommendations") if isinstance(report.get("recommendations"), list) else []
    lines = [
        "本地记录存储审计：",
        "",
        f"- repo_root: {report.get('repo_root') or '-'}",
        f"- scanned_roots: {summary.get('scanned_roots') or 0}",
        f"- scanned_size: {summary.get('scanned_human') or '-'}",
        f"- duplicate_groups: {summary.get('duplicate_groups') or 0}",
        f"- duplicate_waste: {summary.get('duplicate_waste_human') or '0B'}",
        f"- compressible_files: {summary.get('compressible_files') or 0}",
        f"- compressible_candidates: {summary.get('compressible_candidate_human') or '0B'}",
    ]
    if roots:
        lines.extend(["", "扫描目录："])
        for item in roots[:8]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('path')}: {item.get('human')} · files {item.get('files')}")
    if recommendations:
        lines.extend(["", "建议："])
        for item in recommendations:
            lines.append(f"- {item}")
    lines.append("")
    lines.append("当前是 audit-only，不会压缩、删除或移动任何文件。")
    return "\n".join(lines)


def _save_data_path_from_input(repo_root: Path, session_state: dict[str, Any], raw_path: str) -> str:
    try:
        path = normalize_user_path(raw_path, repo_root=repo_root)
        record = save_task_dataset_root(repo_root, path, task_id=DEFAULT_DATA_TASK_ID)
    except Exception as exc:
        session_state["selection_context"] = {"kind": "data_path_input", "task_id": DEFAULT_DATA_TASK_ID}
        return (
            f"数据目录没有保存：{exc}\n"
            "请重新粘贴一个存在的目录，或输入“取消”。"
        )
    session_state.pop("selection_context", None)
    return (
        "已保存本地数据目录。\n"
        f"- dataset_name: {record['dataset_name']}\n"
        f"- dataset_root: {record['dataset_root']}\n"
        f"- config: {data_paths_config_path(repo_root)}\n"
        "\n之后生成 Program 或运行 research-loop 时会读取这个路径；不会复制或修改原始数据。"
    )


def _handle_data_direct_command(parts: list[str], repo_root: Path, session_state: dict[str, Any]) -> str:
    subcommand = str(parts[1]).lower() if len(parts) > 1 else ""
    if not subcommand:
        session_state["selection_context"] = {"kind": "data_path_input", "task_id": DEFAULT_DATA_TASK_ID}
        return (
            "选择本地数据目录。\n"
            "请把本地 BCI 数据文件夹拖进输入框，或粘贴绝对路径后回车。\n"
            "输入“取消”可退出。\n\n"
            + _format_data_path_status(repo_root)
        )
    if subcommand in {"show", "status", "current"}:
        session_state.pop("selection_context", None)
        return _format_data_path_status(repo_root)
    if subcommand in {"clear", "reset"}:
        session_state.pop("selection_context", None)
        cleared = clear_task_dataset_root(repo_root, task_id=DEFAULT_DATA_TASK_ID)
        return "已清除本地数据目录配置。" if cleared else "当前没有本地数据目录配置可清除。"
    raw_path = " ".join(parts[1:]).strip()
    return _save_data_path_from_input(repo_root, session_state, raw_path)


def _format_goal_status(payload: dict[str, Any]) -> str:
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else payload
    if not isinstance(goal, dict) or not goal:
        return "Goal：当前没有 active goal。"
    return "\n".join(
        [
            f"Goal：{goal.get('status') or '-'}",
            f"- goal_id: {goal.get('goal_id') or '-'}",
            f"- objective: {goal.get('objective') or '-'}",
            f"- success_check: {goal.get('success_check') or '-'}",
            f"- evidence: {goal.get('evidence') or '-'}",
        ]
    )


def _format_perp_status(payload: dict[str, Any]) -> str:
    perp = payload.get("perp") if isinstance(payload.get("perp"), dict) else payload
    if not isinstance(perp, dict) or not perp:
        return "Perp：当前没有 active perpetual loop。"
    return "\n".join(
        [
            f"Perp：{perp.get('status') or '-'}",
            f"- perp_id: {perp.get('perp_id') or '-'}",
            f"- objective: {perp.get('objective') or '-'}",
            f"- cadence: {perp.get('cadence') or '-'}",
            f"- execution_model: {perp.get('execution_model') or '-'}",
        ]
    )


def _format_research_tree_status(payload: dict[str, Any]) -> str:
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    perp = payload.get("perp") if isinstance(payload.get("perp"), dict) else {}
    return "\n".join(
        [
            "Research Tree：",
            f"- nodes: {len(nodes)}",
            f"- edges: {len(edges)}",
            f"- goal: {goal.get('status') or 'none'}",
            f"- perp: {perp.get('status') or 'none'}",
            f"- state_path: {payload.get('state_path') or '-'}",
        ]
    )


def _handle_goal_cli(args: argparse.Namespace, *, repo_root: Path) -> dict[str, Any]:
    action = str(args.goal_action or "status")
    if action == "start":
        return start_goal(
            repo_root,
            objective=" ".join(str(part) for part in args.objective).strip(),
            success_check=args.success or "",
            constraints=args.constraint or [],
            replace=bool(args.replace),
        )
    if action == "complete":
        return complete_goal(repo_root, evidence=str(args.evidence or "").strip())
    if action == "clear":
        return clear_goal(repo_root)
    if action == "status":
        return {"ok": True, "goal": build_research_control_snapshot(repo_root).get("goal") or {}}
    raise ValueError(f"Unknown goal action: {action}")


def _handle_perp_cli(args: argparse.Namespace, *, repo_root: Path) -> dict[str, Any]:
    action = str(args.perp_action or "status")
    if action == "start":
        return start_perp(
            repo_root,
            objective=" ".join(str(part) for part in args.objective).strip(),
            cadence=args.cadence,
            scope=args.scope,
            replace=bool(args.replace),
        )
    if action == "stop":
        return stop_perp(repo_root, reason=str(args.reason or "").strip())
    if action == "status":
        return {"ok": True, "perp": build_research_control_snapshot(repo_root).get("perp") or {}}
    raise ValueError(f"Unknown perp action: {action}")


def _handle_research_tree_cli(args: argparse.Namespace, *, repo_root: Path) -> dict[str, Any]:
    action = str(args.tree_action or "show")
    if action in {"show", "status"}:
        return build_research_tree(repo_root)
    raise ValueError(f"Unknown research-tree action: {action}")


def _model_provider_rows() -> list[dict[str, Any]]:
    rows = _provider_list()
    by_name = {str(item.get("name") or "").strip().lower(): item for item in rows}
    ordered = [by_name[name] for name in MODEL_PROVIDER_ORDER if name in by_name]
    ordered.extend(item for item in rows if str(item.get("name") or "").strip().lower() not in MODEL_PROVIDER_ORDER)
    return ordered


def _resolve_agent_model_status(agent: str) -> dict[str, Any]:
    try:
        payload = _provider_call(("resolve_agent_provider_model",), agent)
    except Exception as exc:
        payload = {
            "agent": agent,
            "provider": "-",
            "model": "-",
            "live": agent == "intake",
            "error_code": "invalid_provider_config",
            "message": str(exc),
        }
    return dict(payload) if isinstance(payload, dict) else {"agent": agent, "provider": "-", "model": "-", "live": agent == "intake"}


def _provider_ready_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "状态未知"
    if bool(row.get("ready")):
        return "ready"
    missing = str(row.get("missing_api_key_env") or "").strip()
    if missing:
        return f"missing key:{missing}"
    if row.get("ready") is False:
        return "not ready"
    return "状态未知"


def _provider_display_label(row: dict[str, Any]) -> str:
    name = str(row.get("name") or row.get("id") or "-").strip()
    display = str(row.get("display_name") or "").strip()
    if display and display.lower() != name.lower():
        return f"{name} ({display})"
    return name


def _canonical_provider_name(provider: str) -> str:
    provider_name = str(provider or "").strip().lower()
    if not provider_name:
        return ""
    try:
        presets = importlib.import_module("bci_autoresearch.providers.presets")
        get_provider_preset = getattr(presets, "get_provider_preset", None)
        if callable(get_provider_preset):
            preset = get_provider_preset(provider_name)
            name = str(getattr(preset, "name", "") or "").strip().lower()
            if name:
                return name
    except Exception:
        pass
    return provider_name


def _agent_label(agent: str) -> str:
    for item in MODEL_AGENT_OPTIONS:
        if item["name"] == agent:
            return str(item["label"])
    return agent.title()


def _user_stage_label(value: str) -> str:
    text = str(value or "")
    return USER_STAGE_LABELS.get(text, text)


def _replace_internal_stage_labels(text: str) -> str:
    result = str(text or "")
    for internal, label in USER_STAGE_LABELS.items():
        result = result.replace(internal, label)
    return result


def _normalize_reasoning_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    aliases = {
        "": "audit",
        "on": "raw",
        "debug": "raw",
        "cot": "raw",
        "chain": "raw",
        "summary": "audit",
        "audit": "audit",
        "off": "off",
        "hide": "off",
    }
    return aliases.get(mode, mode)


def _current_reasoning_mode(session_state: dict[str, Any]) -> str:
    return _normalize_reasoning_mode(str(session_state.get("reasoning_mode") or "audit"))


def _redact_reasoning_text(text: str) -> str:
    redacted = re.sub(r"sk-(?:api-)?[A-Za-z0-9_-]{12,}", "sk-[redacted]", str(text or ""))
    return re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", redacted)


def _format_reasoning_mode_message(session_state: dict[str, Any]) -> str:
    mode = _current_reasoning_mode(session_state)
    return "\n".join(
        [
            "推理调试",
            "",
            f"当前模式：{mode}",
            "",
            "1. audit：默认，只保存可审计判断摘要、工具动作和 artifact 引用",
            "2. raw：调试模式，展示并保存 provider 显式返回的 reasoning/thinking 字段；没有返回就显示 unavailable",
            "3. off：界面不追加推理调试提示；本地 control event 仍记录动作摘要",
            "",
            "用法：/reasoning audit | /reasoning raw | /reasoning off",
        ]
    )


def _set_reasoning_mode(session_state: dict[str, Any], mode: str) -> str:
    normalized = _normalize_reasoning_mode(mode)
    if normalized not in {"audit", "raw", "off"}:
        return f"不支持的推理调试模式：{mode}。可用：audit / raw / off。"
    session_state["reasoning_mode"] = normalized
    if normalized == "raw":
        return "推理调试已切到 raw：只展示和保存 provider 明确返回的原始 reasoning/thinking；没有返回就标记 unavailable，不用摘要冒充 CoT。"
    if normalized == "off":
        return "推理调试显示已关闭；本地 control event 仍会记录动作摘要。"
    return "推理调试已切到 audit：默认保存可审计判断摘要，不展示原始 CoT。"


def _reasoning_debug_suffix(intent: dict[str, Any], session_state: dict[str, Any]) -> str:
    mode = _current_reasoning_mode(session_state)
    if mode != "raw":
        return ""
    raw = str(intent.get("raw_reasoning") or "").strip()
    if raw:
        excerpt = _redact_reasoning_text(raw)[:1200] + ("..." if len(raw) > 1200 else "")
        return "\n\n推理调试：\nraw CoT：\n" + excerpt
    summary = str(intent.get("reasoning_summary") or intent.get("summary") or "").strip()
    if summary:
        return "\n\n推理调试：provider 未返回原始 CoT；可审计判断摘要已保存。摘要：" + summary
    return "\n\n推理调试：provider 未返回原始 CoT；本轮只保存可审计判断摘要。"


def _format_model_menu_message() -> str:
    current = _resolve_agent_model_status("intake")
    provider = str(current.get("provider") or "-")
    provider_rows = {str(item.get("name") or "").strip().lower(): item for item in _model_provider_rows()}
    ready = _provider_ready_label(provider_rows.get(provider))
    lines = [
        "模型设置",
        "",
        f"当前计划/对话模型：{provider} / {current.get('model') or '-'} · {ready}",
    ]
    current_error = str(current.get("message") or "").strip()
    if current_error:
        lines.append(f"配置错误：{current_error}")
    lines.extend(
        [
            "",
            "1. 切换当前模型",
            "2. 配置 Provider API key",
            "3. 测试 Provider",
            "4. 查看所有模型",
            "5. 配置代码 Worker 模型",
            "6. 返回",
            "",
            "输入编号继续。",
        ]
    )
    return "\n".join(
        lines
    )


def _open_model_menu(session_state: dict[str, Any]) -> str:
    session_state["selection_context"] = {
        "kind": "model_menu",
        "options": [
            {"action": "switch_intake"},
            {"action": "configure_key"},
            {"action": "test_provider"},
            {"action": "list_models"},
            {"action": "switch_worker"},
            {"action": "close"},
        ],
    }
    return _format_model_menu_message()


def _intake_model_setup_status() -> dict[str, Any]:
    current = _resolve_agent_model_status("intake")
    provider = _canonical_provider_name(str(current.get("provider") or ""))
    provider_rows = {str(item.get("name") or "").strip().lower(): item for item in _model_provider_rows()}
    row = provider_rows.get(provider)
    ready = bool(row and row.get("ready") and not current.get("error_code"))
    return {
        "ready": ready,
        "current": current,
        "provider": provider or str(current.get("provider") or "-"),
        "provider_row": row or {},
        "ready_label": _provider_ready_label(row),
    }


def _format_initial_model_setup_message() -> str:
    status = _intake_model_setup_status()
    current = status["current"] if isinstance(status.get("current"), dict) else {}
    row = status["provider_row"] if isinstance(status.get("provider_row"), dict) else {}
    provider = str(status.get("provider") or current.get("provider") or "-")
    model = str(current.get("model") or row.get("model") or row.get("default_model") or "-")
    missing = str(row.get("missing_api_key_env") or "").strip()
    missing_line = f"缺少：{missing}" if missing else f"状态：{status.get('ready_label') or '未就绪'}"
    return "\n".join(
        [
            "首次配置 / Model Setup",
            "",
            "AutoBCI 没有内置模型 key；未配置时不会生成 Program，也不会用本地兜底冒充智能。",
            f"当前计划/对话模型：{provider} / {model} · {status.get('ready_label') or '-'}",
            missing_line,
            "",
            "1. 配置 Provider API key",
            "2. 切换当前模型",
            "3. 查看所有模型",
            "4. 稍后配置",
            "",
            "输入编号继续。也可以随时输入 /model 打开模型设置。",
        ]
    )


def _open_initial_model_setup(session_state: dict[str, Any]) -> str:
    session_state["selection_context"] = {
        "kind": "model_initial_setup",
        "options": [
            {"action": "configure_key"},
            {"action": "switch_intake"},
            {"action": "list_models"},
            {"action": "close"},
        ],
    }
    return _format_initial_model_setup_message()


def _maybe_open_initial_model_setup(session_state: dict[str, Any]) -> str:
    if is_tui_test_mode_enabled():
        return ""
    if bool(_intake_model_setup_status().get("ready")):
        return ""
    return _open_initial_model_setup(session_state)


def _open_model_agent_picker(session_state: dict[str, Any]) -> str:
    lines = ["选择要配置的模型用途：", ""]
    for index, item in enumerate(MODEL_AGENT_OPTIONS, start=1):
        status = _resolve_agent_model_status(str(item["name"]))
        lines.append(
            f"{index}. {item['label']} · {status.get('provider') or '-'} / {status.get('model') or '-'} · {item['note']}"
        )
    lines.extend(["", "输入编号继续。"])
    session_state["selection_context"] = {"kind": "model_agent_select", "options": list(MODEL_AGENT_OPTIONS)}
    return "\n".join(lines)


def _open_model_provider_picker(session_state: dict[str, Any], *, agent: str) -> str:
    rows = _model_provider_rows()
    lines = [f"选择{_agent_label(agent)}使用的 Provider：", ""]
    for index, row in enumerate(rows, start=1):
        name = _provider_display_label(row)
        model = str(row.get("model") or row.get("default_model") or "-")
        lines.append(f"{index}. {name} · {model} · {_provider_ready_label(row)}")
    lines.extend(["", "输入编号继续。"])
    session_state["selection_context"] = {"kind": "model_provider_select", "agent": agent, "options": rows}
    return "\n".join(lines)


def _open_model_model_picker(session_state: dict[str, Any], *, agent: str, provider: str) -> str:
    row = next((item for item in _model_provider_rows() if str(item.get("name") or "").strip().lower() == provider), {})
    provider_label = _provider_display_label(row) if row else provider
    resolved_model = str(row.get("model") or row.get("default_model") or "").strip()
    if not resolved_model:
        resolved_model = "-"
    options = [{"model": resolved_model}, {"manual": True}]
    lines = [
        f"选择 {provider_label} 的模型：",
        "",
        f"1. {resolved_model} · 当前配置或 provider 默认",
        "2. 手动输入模型名",
        "",
        "选择模型后会先测试，测试通过才保存。",
    ]
    session_state["selection_context"] = {
        "kind": "model_model_select",
        "agent": agent,
        "provider": provider,
        "options": options,
    }
    return "\n".join(lines)


def _open_model_key_provider_picker(
    session_state: dict[str, Any],
    *,
    after_save_agent: str | None = None,
    initial_setup: bool = False,
) -> str:
    rows = [item for item in _model_provider_rows() if str(item.get("api_key_env") or "").strip()]
    lines = ["选择要配置 API key 的 Provider：", ""]
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {_provider_display_label(row)} · {_provider_ready_label(row)}")
    lines.extend(["", "输入编号后会进入隐藏输入。"])
    session_state["selection_context"] = {
        "kind": "model_key_provider_select",
        "options": rows,
        "after_save_agent": after_save_agent,
        "initial_setup": initial_setup,
    }
    return "\n".join(lines)


def _open_model_test_provider_picker(session_state: dict[str, Any]) -> str:
    rows = _model_provider_rows()
    lines = ["选择要测试的 Provider：", ""]
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {_provider_display_label(row)} · {row.get('model') or row.get('default_model') or '-'} · {_provider_ready_label(row)}")
    lines.extend(["", "输入编号开始测试。"])
    session_state["selection_context"] = {"kind": "model_test_provider_select", "options": rows}
    return "\n".join(lines)


def _format_all_model_statuses() -> str:
    payload = _provider_list_payload()
    agents = payload.get("agents")
    providers = payload.get("providers")
    lines = ["模型与 Provider 状态：", ""]
    if isinstance(agents, list) and agents:
        lines.append("当前模型：")
        for item in agents:
            if isinstance(item, dict):
                if str(item.get("agent") or "").strip().lower() != "intake":
                    continue
                live = "已接入" if item.get("live") else "暂未接入实时调用"
                lines.append(f"- {_agent_label(str(item.get('agent') or '-'))}: {item.get('provider') or '-'} / {item.get('model') or '-'} · {live}")
        lines.append("")
    lines.append("Provider：")
    if isinstance(providers, list):
        for item in providers:
            if isinstance(item, dict):
                lines.append(f"- {_provider_display_label(item)} · {item.get('model') or item.get('default_model') or '-'} · {_provider_ready_label(item)}")
    lines.extend(["", "输入 /model 返回模型设置。"])
    return "\n".join(lines)


def _prepare_secret_input(
    session_state: dict[str, Any],
    provider: str,
    *,
    after_save_agent: str | None = None,
    after_save_model: str | None = None,
) -> str:
    provider_name = _canonical_provider_name(provider)
    if not provider_name:
        return "请选择要配置 API key 的 Provider。"
    secret_input: dict[str, Any] = {"kind": "provider_api_key", "provider": provider_name}
    if after_save_agent:
        secret_input["after_save_agent"] = after_save_agent
    if after_save_model:
        secret_input["after_save_model"] = after_save_model
    session_state["secret_input"] = secret_input
    session_state.pop("selection_context", None)
    return f"已准备为 {provider_name} 保存 API key。下一行会使用隐藏输入；粘贴 key 后按回车。"


def _save_provider_secret_from_input(
    provider: str,
    api_key: str,
    *,
    after_save_agent: str | None = None,
    after_save_model: str | None = None,
) -> str:
    key = str(api_key or "").strip()
    if not key:
        return f"{provider} API key 为空，未保存。"
    payload = _provider_call(("write_provider_secret",), provider, key)
    result = dict(payload) if isinstance(payload, dict) else {"ok": True, "provider": provider}
    if not result.get("ok"):
        return f"{provider} API key 保存失败。"
    saved = f"已保存 {provider} API key。"
    if not after_save_agent:
        return saved + f"输入 /model test {provider} 可以测试连通性。"
    model = str(after_save_model or "").strip()
    if not model:
        row = next((item for item in _model_provider_rows() if str(item.get("name") or "").strip().lower() == provider), {})
        model = str(row.get("model") or row.get("default_model") or "").strip()
    if not model:
        return saved + "但没有可用模型名，计划/对话模型未切换。"
    return saved + "\n" + _test_and_set_agent_model(after_save_agent, provider, model)


def _test_provider_message(provider: str, *, model: str | None = None) -> tuple[bool, str]:
    payload = _provider_test(provider, model=model)
    ok = bool(payload.get("ok"))
    provider_name = str(payload.get("provider") or provider)
    resolved_model = str(payload.get("model") or model or "-")
    if ok:
        return True, f"{provider_name} provider 可用：{resolved_model}"
    message = str(payload.get("message") or payload.get("error_code") or "测试失败")
    missing = str(payload.get("missing_api_key_env") or "").strip()
    suffix = f"。缺少 {missing}" if missing else ""
    return False, f"{provider_name} provider 不可用：{message}{suffix}"


def _test_and_set_agent_model(agent: str, provider: str, model: str) -> str:
    ok, message = _test_provider_message(provider, model=model)
    if not ok:
        trace = _format_action_trace(
            [
                _manual_action_event(
                    actor="Model Runtime",
                    action="测试 Provider",
                    summary=f"{provider} / {model} 未通过连通性或 JSON 兼容测试。",
                    details=[message, "旧模型保持不变。"],
                )
            ]
        )
        return f"{message}。旧模型保持不变。\n\n{trace}"
    payload = _provider_call(("set_agent_model", "set_agent_provider_model"), agent, provider, model=model)
    result = dict(payload) if isinstance(payload, dict) else {"ok": True, "agent": agent, "provider": provider, "model": model}
    if not result.get("ok"):
        label = _agent_label(agent)
        noun = "" if label.endswith("模型") else " 模型"
        trace = _format_action_trace(
            [
                _manual_action_event(
                    actor="Model Runtime",
                    action="切换模型",
                    summary=f"{label} -> {provider} / {model} 写入配置失败。",
                    details=["Provider 测试已通过，但 agent 配置写入失败。", "旧模型保持不变。"],
                )
            ]
        )
        return f"{label}{noun}设置失败。旧模型保持不变。\n\n{trace}"
    live_note = "已接入实时调用" if result.get("live") else "已保存配置，暂未接入实时调用"
    label = _agent_label(agent)
    noun = "" if label.endswith("模型") else " 模型"
    trace = _format_action_trace(
        [
            _manual_action_event(
                actor="Model Runtime",
                action="测试 Provider",
                summary=f"{provider} / {model} 可用。",
                details=[message],
            ),
            _manual_action_event(
                actor="Model Runtime",
                action="切换模型",
                summary=f"{label} -> {provider} / {model}",
                details=[live_note, "后续 Program 对话会显示当前实际模型。"],
            ),
        ]
    )
    return f"{label}{noun}已设置为 {provider} / {model}。{live_note}。\n\n{trace}"


def _handle_model_selection(session_state: dict[str, Any], index: int) -> str:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict):
        return _open_model_menu(session_state)
    options = list(selection_context.get("options") or [])
    if index < 1 or index > len(options):
        return f"没有第 {index} 个选项。请输入列表里的编号，或输入 /model 重新查看。"
    kind = str(selection_context.get("kind") or "")
    selected = options[index - 1]
    if kind == "model_initial_setup":
        action = str(selected.get("action") or "")
        if action == "configure_key":
            return _open_model_key_provider_picker(session_state, after_save_agent="intake", initial_setup=True)
        if action == "switch_intake":
            return _open_model_provider_picker(session_state, agent="intake")
        if action == "list_models":
            session_state.pop("selection_context", None)
            return _format_all_model_statuses()
        session_state.pop("selection_context", None)
        return "已跳过首次模型配置。AutoBCI 不会调用未配置的模型；需要时输入 /model。"
    if kind == "model_menu":
        action = str(selected.get("action") or "")
        if action == "switch_intake":
            return _open_model_provider_picker(session_state, agent="intake")
        if action == "switch_worker":
            return _open_model_provider_picker(session_state, agent="worker")
        if action == "configure_key":
            return _open_model_key_provider_picker(session_state)
        if action == "test_provider":
            return _open_model_test_provider_picker(session_state)
        if action == "list_models":
            session_state.pop("selection_context", None)
            return _format_all_model_statuses()
        session_state.pop("selection_context", None)
        return "已退出模型设置。"
    if kind == "model_agent_select":
        agent = str(selected.get("name") or "intake")
        return _open_model_provider_picker(session_state, agent=agent)
    if kind == "model_provider_select":
        agent = str(selection_context.get("agent") or "intake")
        provider = str(selected.get("name") or "").strip().lower()
        return _open_model_model_picker(session_state, agent=agent, provider=provider)
    if kind == "model_model_select":
        agent = str(selection_context.get("agent") or "intake")
        provider = str(selection_context.get("provider") or "").strip().lower()
        if selected.get("manual"):
            session_state["selection_context"] = {"kind": "model_manual_model", "agent": agent, "provider": provider, "options": []}
            return f"请输入 {provider} 的模型名，例如 abab6.5s-chat。"
        model = str(selected.get("model") or "").strip()
        provider_row = next((item for item in _model_provider_rows() if str(item.get("name") or "").strip().lower() == provider), {})
        if str(provider_row.get("api_key_env") or "").strip() and not bool(provider_row.get("ready")):
            return _prepare_secret_input(
                session_state,
                provider,
                after_save_agent=agent,
                after_save_model=model,
            )
        session_state.pop("selection_context", None)
        return _test_and_set_agent_model(agent, provider, model)
    if kind == "model_key_provider_select":
        provider = str(selected.get("name") or "").strip().lower()
        after_save_agent = str(selection_context.get("after_save_agent") or "").strip().lower() or None
        after_save_model = None
        if after_save_agent:
            after_save_model = str(selected.get("model") or selected.get("default_model") or "").strip() or None
        return _prepare_secret_input(
            session_state,
            provider,
            after_save_agent=after_save_agent,
            after_save_model=after_save_model,
        )
    if kind == "model_test_provider_select":
        provider = str(selected.get("name") or "").strip().lower()
        session_state.pop("selection_context", None)
        return _test_provider_message(provider, model=str(selected.get("model") or "") or None)[1]
    return _open_model_menu(session_state)


def _handle_model_manual_model(session_state: dict[str, Any], model: str) -> str:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict) or selection_context.get("kind") != "model_manual_model":
        return _open_model_menu(session_state)
    agent = str(selection_context.get("agent") or "intake")
    provider = str(selection_context.get("provider") or "").strip().lower()
    session_state.pop("selection_context", None)
    return _test_and_set_agent_model(agent, provider, model)


def _handle_model_direct_command(parts: list[str], session_state: dict[str, Any]) -> str:
    if len(parts) == 1:
        return _open_model_menu(session_state)
    subcommand = str(parts[1]).lower()
    if subcommand == "list":
        session_state.pop("selection_context", None)
        return _format_all_model_statuses()
    if subcommand == "key":
        provider = str(parts[2] if len(parts) > 2 else "").strip().lower()
        if not provider:
            return _open_model_key_provider_picker(session_state)
        return _prepare_secret_input(session_state, provider)
    if subcommand == "test":
        provider = str(parts[2] if len(parts) > 2 else "").strip().lower()
        if not provider:
            return _open_model_test_provider_picker(session_state)
        return _test_provider_message(provider)[1]
    if subcommand == "set" and len(parts) >= 5:
        agent = str(parts[2]).strip().lower()
        provider = str(parts[3]).strip().lower()
        model = " ".join(parts[4:]).strip()
        return _test_and_set_agent_model(agent, provider, model)
    return _open_model_menu(session_state)


def _director_selection_number(command: str, session_state: dict[str, Any]) -> int | None:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict):
        return None
    if not str(selection_context.get("kind") or "").startswith("director_"):
        return None
    stripped = command.strip()
    return int(stripped) if stripped.isdigit() else None


def _format_director_menu_message() -> str:
    return "\n".join(
        [
            "研究方向调度",
            "",
            "1. 生成研究队列",
            "2. 查看最近队列",
            "3. 查看证据包",
            "4. 返回",
            "",
            "只生成当前 Program 边界内的候选研究方向，不启动执行沙盒。",
            "输入编号继续。",
        ]
    )


def _open_director_menu(session_state: dict[str, Any]) -> str:
    session_state["selection_context"] = {
        "kind": "director_menu",
        "options": [
            {"action": "generate"},
            {"action": "latest"},
            {"action": "evidence"},
            {"action": "close"},
        ],
    }
    return _format_director_menu_message()


def _format_director_plan_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "还没有研究方向队列。输入 /director 后选 1 生成。"
    tracks = payload.get("tracks") if isinstance(payload.get("tracks"), list) else []
    web = payload.get("web_research") if isinstance(payload.get("web_research"), dict) else {}
    artifact_paths = payload.get("artifact_paths") if isinstance(payload.get("artifact_paths"), dict) else {}
    lines = [
        f"研究方向队列：{payload.get('plan_id') or '-'}",
        f"Web：{web.get('web_status') or '-'} / {web.get('provider') or '-'}",
        f"Artifact：{artifact_paths.get('latest') or artifact_paths.get('plan') or latest_director_plan_path(Path('.'))}",
        "",
    ]
    for index, track in enumerate(tracks[:12], start=1):
        if not isinstance(track, dict):
            continue
        runnable = "可跑" if bool(track.get("runnable_now")) else "候选"
        lines.append(f"{index}. [{runnable}] {track.get('title') or track.get('track_id') or '-'} · {track.get('algorithm_family') or '-'}")
    if len(tracks) > 12:
        lines.append(f"... 还有 {len(tracks) - 12} 个方向。")
    return "\n".join(lines)


def _format_director_evidence_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "还没有研究证据包。输入 /director 后选 1 生成。"
    evidence_pack = payload.get("evidence_pack") if isinstance(payload.get("evidence_pack"), dict) else {}
    evidence = evidence_pack.get("evidence") if isinstance(evidence_pack.get("evidence"), list) else []
    web = payload.get("web_research") if isinstance(payload.get("web_research"), dict) else {}
    lines = [
        f"研究证据包：{payload.get('plan_id') or '-'}",
        f"Web：{web.get('web_status') or '-'} / {web.get('provider') or '-'}",
        "",
    ]
    if not evidence:
        lines.append("没有证据条目。")
        return "\n".join(lines)
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("evidence_id") or "-")
        source_type = str(item.get("source_type") or "-")
        summary = str(item.get("summary") or item.get("snippet") or "").strip()
        lines.append(f"{index}. {title} · {source_type}")
        if summary:
            lines.append(f"   {summary[:220]}")
    return "\n".join(lines)


def _generate_director_plan_message(paths: Any, session_state: dict[str, Any]) -> str:
    payload = run_director_plan(paths.repo_root, min_tracks=10, web="auto")
    session_state.pop("selection_context", None)
    tracks = payload.get("tracks") if isinstance(payload.get("tracks"), list) else []
    runnable_count = sum(1 for track in tracks if isinstance(track, dict) and bool(track.get("runnable_now")))
    web = payload.get("web_research") if isinstance(payload.get("web_research"), dict) else {}
    artifact_paths = payload.get("artifact_paths") if isinstance(payload.get("artifact_paths"), dict) else {}
    algorithm_families = sorted({str(track.get("algorithm_family") or "") for track in tracks if isinstance(track, dict) and track.get("algorithm_family")})
    trace = _format_action_trace(
        [
            _manual_action_event(
                actor="Director",
                action="读取任务状态",
                summary=f"{payload.get('program_id') or '-'} / {payload.get('source_run_id') or '-'}",
                details=[f"输入：{payload.get('source_state_path') or '-'}"],
            ),
            _manual_action_event(
                actor="Web Research",
                action="检索证据",
                summary=f"{web.get('web_status') or '-'} / {web.get('provider') or '-'}",
                details=[
                    f"query 数：{(web.get('budget_state') or {}).get('queries_used') or 0}",
                    f"证据条数：{web.get('evidence_returned') or 0}",
                    f"首个 query：{(web.get('queries') or ['-'])[0] if isinstance(web.get('queries'), list) and web.get('queries') else '-'}",
                ],
            ),
            _manual_action_event(
                actor="Director",
                action="生成研究方向队列",
                summary=f"{len(tracks)} 个方向；{runnable_count} 个当前可跑。",
                details=[
                    "方向族：" + ", ".join(algorithm_families[:6]) if algorithm_families else "方向族：-",
                    f"latest：{artifact_paths.get('latest') or '-'}",
                    "未启动执行沙盒，未写正式 tracks manifest。",
                ],
            ),
        ]
    )
    summary = _format_director_plan_summary(payload)
    return (
        f"已生成研究方向队列：{payload.get('plan_id')}。"
        f"共 {len(tracks)} 个方向（10+ 个方向），其中 {runnable_count} 个标记为现在 runner 可跑。"
        "没有启动执行沙盒，也没有写正式 tracks manifest。\n\n"
        + trace
        + "\n\n"
        + summary
    )


def _handle_director_selection(paths: Any, session_state: dict[str, Any], index: int) -> str:
    selection_context = session_state.get("selection_context")
    if not isinstance(selection_context, dict):
        return _open_director_menu(session_state)
    options = list(selection_context.get("options") or [])
    if index < 1 or index > len(options):
        return f"没有第 {index} 个选项。请输入列表里的编号，或输入 /director 重新查看。"
    action = str((options[index - 1] or {}).get("action") or "")
    if action == "generate":
        return _generate_director_plan_message(paths, session_state)
    if action == "latest":
        session_state.pop("selection_context", None)
        return _format_director_plan_summary(load_latest_director_plan(paths.repo_root))
    if action == "evidence":
        session_state.pop("selection_context", None)
        return _format_director_evidence_summary(load_latest_director_plan(paths.repo_root))
    session_state.pop("selection_context", None)
    return "已退出研究方向调度。"


def _handle_director_direct_command(parts: list[str], paths: Any, session_state: dict[str, Any]) -> str:
    if len(parts) == 1:
        return _open_director_menu(session_state)
    subcommand = str(parts[1]).strip().lower()
    if subcommand in {"plan", "generate", "run"}:
        return _generate_director_plan_message(paths, session_state)
    if subcommand in {"latest", "show", "queue"}:
        session_state.pop("selection_context", None)
        return _format_director_plan_summary(load_latest_director_plan(paths.repo_root))
    if subcommand in {"evidence", "pack"}:
        session_state.pop("selection_context", None)
        return _format_director_evidence_summary(load_latest_director_plan(paths.repo_root))
    return _open_director_menu(session_state)


def _trace_event_display_title(item: dict[str, Any]) -> str:
    display = item.get("display") if isinstance(item.get("display"), dict) else {}
    return _replace_internal_stage_labels(
        str(display.get("title") or f"{item.get('actor') or '-'} · {item.get('action') or item.get('event_type') or '-'}")
    )


def _trace_event_display_lines(item: dict[str, Any], *, detail_limit: int = 2) -> list[str]:
    display = item.get("display") if isinstance(item.get("display"), dict) else {}
    title = _trace_event_display_title(item)
    summary = _replace_internal_stage_labels(str(display.get("summary") or item.get("reason") or "").strip())
    lines = [f"- {title}" + (f"：{summary}" if summary else "")]
    details = display.get("details") if isinstance(display.get("details"), list) else []
    for detail in details[:detail_limit]:
        if str(detail or "").strip():
            lines.append(f"  {_replace_internal_stage_labels(str(detail))}")
    return lines


def _manual_action_event(
    *,
    actor: str,
    action: str,
    summary: str = "",
    details: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "actor": actor,
        "event_type": "manual_action",
        "action": action,
        "display": {
            "title": f"{actor} · {action}",
            "summary": summary,
            "details": [str(item) for item in (details or []) if str(item or "").strip()],
        },
    }


def _format_action_trace(events: list[dict[str, Any]], *, max_events: int = 6, detail_limit: int = 2) -> str:
    if not events:
        return ""
    selected = [
        item
        for item in events
        if isinstance(item, dict)
        and str(item.get("event_type") or "")
        in {"director_decision", "tool_call_start", "tool_call_end", "artifact_written", "evaluator_result", "judge_decision", "human_gate_waiting"}
    ]
    if not selected:
        selected = [item for item in events if isinstance(item, dict)]
    lines = ["行动记录："]
    for item in selected[:max_events]:
        lines.extend(_trace_event_display_lines(item, detail_limit=detail_limit))
    if len(selected) > max_events:
        lines.append(f"- ... 还有 {len(selected) - max_events} 条动作，输入 /run events 查看。")
    return "\n".join(lines)


def _format_research_loop_step_message(payload: dict[str, Any]) -> str:
    if payload.get("status") == "stalled":
        return f"研究闭环已停止空转：{payload.get('reason') or '-'}"
    if payload.get("status") == "empty":
        return "研究闭环队列为空：没有可执行 track。"
    track = payload.get("track") if isinstance(payload.get("track"), dict) else {}
    judgment = payload.get("judgment") if isinstance(payload.get("judgment"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    robust = result.get("robust_summary") if isinstance(result.get("robust_summary"), dict) else {}
    robust_text = ""
    mean = robust.get("best_test_balanced_accuracy_mean")
    std = robust.get("best_test_balanced_accuracy_std")
    if isinstance(mean, (int, float)):
        robust_text = f"\n多划分 best BA：mean={float(mean):.4f}, std={float(std or 0.0):.4f}"
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    trace_text = _format_action_trace([item for item in events if isinstance(item, dict)])
    message = (
        "研究闭环已推进。\n"
        f"研究方向：{track.get('title') or track.get('track_id') or '-'}\n"
        f"方向：{track.get('direction') or '-'}\n"
        f"执行：{track.get('action_type') or '-'} / {track.get('runner') or '-'}\n"
        f"判断：{judgment.get('decision') or '-'} · {judgment.get('reason') or '-'}\n"
        f"风险：{', '.join(str(item) for item in (judgment.get('risk_flags') or [])) or '-'}\n"
        f"trace：{len(payload.get('ledger_row', {}).get('result', {}).get('trace_event_ids', []) if isinstance(payload.get('ledger_row'), dict) else [])} events"
        f"{robust_text}"
    )
    if trace_text:
        message += "\n\n" + trace_text
    return message


def _research_gate_choice(command: str) -> str | None:
    token = str(command or "").strip().lower()
    if not token or token.startswith("/"):
        return None
    yes = {"y", "yes", "继续", "确认", "accept", "keep", "同意", "开始"}
    no = {"n", "no", "暂停", "停", "hold", "reject", "拒绝", "取消"}
    details = {"d", "detail", "details", "展开", "详情", "show", "evidence", "diff"}
    if token in yes:
        return "yes"
    if token in no:
        return "no"
    if token in details:
        return "details"
    return None


def _research_gate_lines(gate: dict[str, Any], *, details: bool = False) -> list[str]:
    track = gate.get("track") if isinstance(gate.get("track"), dict) else {}
    lines = [
        str(gate.get("prompt") or "Continue?  [Y] Yes   [N] No / Pause   [D] Details"),
        f"研究方向：{track.get('title') or track.get('track_id') or '-'}",
        f"动作：{track.get('action_type') or '-'} / {track.get('runner') or '-'}",
        f"原因：{gate.get('reason') or track.get('hypothesis') or '-'}",
    ]
    if gate.get("risk_flags"):
        lines.append("风险：" + ", ".join(str(item) for item in gate.get("risk_flags") or []))
    if details:
        params = track.get("params") if isinstance(track.get("params"), dict) else {}
        lines.extend(
            [
                "",
                "Details:",
                f"- track_id: {track.get('track_id') or '-'}",
                f"- direction: {track.get('direction') or '-'}",
                f"- params: {json.dumps(params, ensure_ascii=False, sort_keys=True) if params else '-'}",
                f"- expected_signal: {track.get('expected_signal') or '-'}",
                f"- stop_condition: {track.get('stop_condition') or '-'}",
            ]
        )
        editable = track.get("editable_files") if isinstance(track.get("editable_files"), list) else []
        if editable:
            lines.append("- editable_files: " + ", ".join(str(item) for item in editable))
        payload = gate.get("payload") if isinstance(gate.get("payload"), dict) else {}
        if payload:
            judgment = payload.get("judgment") if isinstance(payload.get("judgment"), dict) else {}
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            lines.extend(
                [
                    f"- decision: {judgment.get('decision') or '-'}",
                    f"- judgment_reason: {judgment.get('reason') or '-'}",
                    f"- result_status: {result.get('status') or '-'}",
                ]
            )
            if result.get("diff_summary"):
                lines.append("- diff_summary: " + str(result.get("diff_summary")))
            refs = judgment.get("evidence_refs") if isinstance(judgment.get("evidence_refs"), list) else []
            if refs:
                lines.append("- evidence_refs: " + ", ".join(str(item) for item in refs[:5]))
    return lines


def _format_research_gate(gate: dict[str, Any], *, details: bool = False) -> str:
    return "\n".join(_research_gate_lines(gate, details=details))


def _post_step_gate(payload: dict[str, Any]) -> dict[str, Any] | None:
    track = payload.get("track") if isinstance(payload.get("track"), dict) else {}
    judgment = payload.get("judgment") if isinstance(payload.get("judgment"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    risk_flags = [str(item) for item in (judgment.get("risk_flags") if isinstance(judgment.get("risk_flags"), list) else [])]
    action_type = str(track.get("action_type") or "")
    decision = str(judgment.get("decision") or "")
    if decision == "promoted":
        prompt = "Promote this result?  [Y] Accept   [N] Hold   [D] Show evidence"
        gate_type = "promote_review"
    elif action_type == "edit_code":
        prompt = "Keep this candidate?  [Y] Keep   [N] Reject / rollback   [D] Show diff"
        gate_type = "edit_code_review"
    elif risk_flags:
        prompt = "Continue after risk review?  [Y] Continue   [N] Pause   [D] Show evidence"
        gate_type = "risk_review"
    else:
        return None
    return {
        "kind": "research_gate",
        "gate_type": gate_type,
        "task_id": payload.get("task_id") or DEFAULT_RESEARCH_TASK_ID,
        "track": track,
        "payload": payload,
        "prompt": prompt,
        "reason": judgment.get("reason") or "",
        "risk_flags": risk_flags,
    }


def _preview_gate_from_payload(preview: dict[str, Any]) -> dict[str, Any]:
    track = preview.get("track") if isinstance(preview.get("track"), dict) else {}
    action_type = str(track.get("action_type") or "")
    if action_type == "edit_code":
        prompt = "Allow structure sandbox edit?  [Y] Yes   [N] Pause   [D] Show editable boundary"
        risk_flags = ["code_change_candidate"]
    else:
        prompt = "Continue?  [Y] Yes   [N] No / Pause   [D] Details"
        risk_flags = []
    return {
        "kind": "research_gate",
        "gate_type": preview.get("gate_type") or "step_pre",
        "task_id": preview.get("task_id") or DEFAULT_RESEARCH_TASK_ID,
        "track": track,
        "prompt": prompt,
        "reason": "方向选择准备把这个 track 作为下一步 active track。",
        "risk_flags": risk_flags,
    }


def _open_research_step_gate(paths: Any, session_state: dict[str, Any]) -> str:
    preview = preview_research_step(paths.repo_root, task_id=DEFAULT_RESEARCH_TASK_ID)
    if preview.get("status") == "stalled":
        return f"研究闭环已停止空转：{preview.get('reason') or '-'}"
    if preview.get("status") == "empty":
        return "研究闭环队列为空：没有可执行 track。"
    gate = _preview_gate_from_payload(preview)
    session_state["research_gate"] = gate
    append_research_trace_event(
        paths.repo_root,
        task_id=str(gate.get("task_id") or DEFAULT_RESEARCH_TASK_ID),
        actor="headless_cli",
        event_type="human_gate_waiting",
        action=str(gate.get("gate_type") or "step_pre"),
        track=gate.get("track") if isinstance(gate.get("track"), dict) else {},
        reason=str(gate.get("reason") or ""),
        risk_flags=[str(item) for item in (gate.get("risk_flags") or [])],
    )
    return _format_research_gate(gate)


def _handle_research_gate_response(paths: Any, session_state: dict[str, Any], command: str) -> str | None:
    gate = session_state.get("research_gate")
    if not isinstance(gate, dict):
        return None
    choice = _research_gate_choice(command)
    if choice is None:
        return None
    if choice == "details":
        return _format_research_gate(gate, details=True)
    if choice == "no":
        session_state.pop("research_gate", None)
        append_research_trace_event(
            paths.repo_root,
            task_id=str(gate.get("task_id") or DEFAULT_RESEARCH_TASK_ID),
            actor="headless_cli",
            event_type="human_gate_waiting",
            action="human_paused",
            track=gate.get("track") if isinstance(gate.get("track"), dict) else {},
            reason="Owner 选择暂停或保留人工复核。",
            decision={"choice": "no"},
            risk_flags=[str(item) for item in (gate.get("risk_flags") or [])],
        )
        return "已暂停当前研究步骤。输入 /run step 可重新打开下一步确认。"
    gate_type = str(gate.get("gate_type") or "")
    if gate_type in {"step_pre", "edit_code_pre"}:
        session_state.pop("research_gate", None)
        payload = step_research_loop(paths.repo_root, task_id=str(gate.get("task_id") or DEFAULT_RESEARCH_TASK_ID))
        message = _format_research_loop_step_message(payload)
        post_gate = _post_step_gate(payload)
        if post_gate is not None:
            session_state["research_gate"] = post_gate
            append_research_trace_event(
                paths.repo_root,
                task_id=str(post_gate.get("task_id") or DEFAULT_RESEARCH_TASK_ID),
                actor="headless_cli",
                event_type="human_gate_waiting",
                action=str(post_gate.get("gate_type") or "post_step_review"),
                track=post_gate.get("track") if isinstance(post_gate.get("track"), dict) else {},
                reason=str(post_gate.get("reason") or ""),
                decision={"source_decision": (payload.get("judgment") or {}).get("decision") if isinstance(payload.get("judgment"), dict) else ""},
                risk_flags=[str(item) for item in (post_gate.get("risk_flags") or [])],
            )
            message += "\n\n" + _format_research_gate(post_gate)
        return message
    session_state.pop("research_gate", None)
    return "已记录复核选择，研究闭环保持暂停。输入 /run step 继续下一步。"


def _format_research_events_summary(payload: dict[str, Any]) -> str:
    events = payload.get("recent_events") if isinstance(payload.get("recent_events"), list) else []
    if not events:
        return "研究事件流：暂无 research trace event。"
    lines = ["研究事件流："]
    for item in events[-12:]:
        if not isinstance(item, dict):
            continue
        timestamp = str(item.get("created_at") or "-")
        display_lines = _trace_event_display_lines(item, detail_limit=3)
        if display_lines:
            lines.append(f"- {timestamp} {display_lines[0].lstrip('- ')}")
            lines.extend("  " + line.strip() for line in display_lines[1:])
        else:
            lines.append(
                f"- {timestamp} {item.get('actor') or '-'} · "
                f"{item.get('event_type') or '-'} · {item.get('action') or '-'} · "
                f"{item.get('track_id') or '-'}"
            )
    return "\n".join(lines)


def _handle_research_direct_command(parts: list[str], paths: Any, session_state: dict[str, Any]) -> str:
    subcommand = str(parts[1]).strip().lower() if len(parts) > 1 else "status"
    if subcommand in {"step", "run"}:
        return _open_research_step_gate(paths, session_state)
    if subcommand == "status":
        payload = status_research_loop(paths.repo_root, task_id=DEFAULT_RESEARCH_TASK_ID)
        active = payload.get("active_track") if isinstance(payload.get("active_track"), dict) else {}
        return (
            "研究闭环状态："
            f"{payload.get('phase') or '-'} · ledger {payload.get('ledger_count') or 0} · queued {payload.get('queued_count') or 0}\n"
            f"最近方向：{active.get('track_id') or '-'}"
        )
    if subcommand == "stop":
        payload = stop_research_loop(paths.repo_root, task_id=DEFAULT_RESEARCH_TASK_ID)
        session_state.pop("research_gate", None)
        return f"研究闭环已停止：{payload.get('status') or '-'}"
    if subcommand in {"events", "trace"}:
        payload = status_research_loop(paths.repo_root, task_id=DEFAULT_RESEARCH_TASK_ID)
        return _format_research_events_summary(payload)
    if subcommand == "explain" and len(parts) >= 3:
        payload = explain_research_track(paths.repo_root, task_id=DEFAULT_RESEARCH_TASK_ID, track_id=str(parts[2]))
        chain = payload.get("judgment_chain") if isinstance(payload.get("judgment_chain"), list) else []
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        if not chain and not events:
            return "没有找到这个 track 的判断链。"
        lines = ["研究判断链："]
        lines.extend(f"- {item}" for item in chain)
        if payload.get("risk_flags"):
            lines.append("风险：" + ", ".join(str(item) for item in payload.get("risk_flags") or []))
        if events:
            lines.append("事件：")
            lines.extend(f"- {item.get('actor') or '-'} · {item.get('event_type') or '-'} · {item.get('action') or '-'}" for item in events[-8:])
        return "\n".join(lines)
    return "用法：/run status、/run step、/run stop、/run explain <track_id>"


def _parse_remote_args(parts: list[str]) -> tuple[str, int]:
    host = "0.0.0.0"
    port = 8788
    index = 1
    while index < len(parts):
        item = str(parts[index]).strip()
        if item == "--host" and index + 1 < len(parts):
            host = str(parts[index + 1]).strip() or host
            index += 2
            continue
        if item == "--port" and index + 1 < len(parts):
            port = int(str(parts[index + 1]).strip())
            index += 2
            continue
        index += 1
    return host, port


def _format_remote_bridge_started(bridge: Any) -> str:
    return "\n".join(
        [
            "Remote 已开启：当前 TUI 会话可以在手机或外部 Bot 继续对话。",
            "",
            f"手机浏览器：{bridge.lan_url}",
            f"本机浏览器：{bridge.local_url}",
            "",
            "HTTP 接口：",
            f"- POST {bridge.lan_url.replace('/?token=', '/message?token=')}  body: {{\"text\":\"现在进展如何？\"}}",
            f"- GET  {bridge.lan_url.replace('/?token=', '/events?token=')}  查看最近回复和研究事件",
            "",
            "边界：这是 current-session remote，不是全局主控。手机消息会进入当前 Program / 当前尝试；退出 TUI 后 bridge 会停止。",
        ]
    )


def _handle_remote_direct_command(
    parts: list[str],
    *,
    paths: Any,
    session_state: dict[str, Any],
    repo_root: Path,
    host: str,
    port: int,
    python_executable: str | None,
    use_model_agent: bool,
) -> str:
    stopped = stop_remote_bridge(session_state)
    suffix = "\n\n旧 bridge 已关闭。" if stopped else ""
    return (
        "旧 `/remote` current-session bridge 已废弃。"
        "请让 Hermes、ClawBot、Claude Code、Codex、Cursor 或其它 agent 直接调用 headless CLI：\n"
        "- autobci status --json\n"
        "- autobci ask \"现在进展如何？\" --json\n"
        "- autobci-agent research-loop status --json\n"
        f"{suffix}"
    )


def _terminal_runtime_profile() -> dict[str, object]:
    term = str(os.environ.get("TERM") or "").strip().lower()
    term_program = str(os.environ.get("TERM_PROGRAM") or "").strip().lower()
    resources_dir = str(os.environ.get("GHOSTTY_RESOURCES_DIR") or "").strip()
    is_ghostty = "ghostty" in term or term_program == "ghostty" or bool(resources_dir)
    disable_mouse = _env_flag_enabled("AUTOBCI_DISABLE_MOUSE")
    return {
        "is_ghostty": is_ghostty,
        "mouse_support": not disable_mouse,
        "animate_ui": False if is_ghostty else True,
        "defer_repaint_while_typing": is_ghostty,
        "cursor": None if is_ghostty or CursorShape is None else CursorShape.BLOCK,
    }


def _char_display_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _display_width(text: str) -> int:
    return sum(_char_display_width(char) for char in text)


def _clip_display(text: str, max_width: int) -> str:
    current_width = 0
    parts: list[str] = []
    for char in text:
        char_width = _char_display_width(char)
        if current_width + char_width > max_width:
            break
        parts.append(char)
        current_width += char_width
    return "".join(parts)


def _pad_display(text: str, width: int) -> str:
    clipped = _clip_display(text, width)
    pad = max(width - _display_width(clipped), 0)
    return clipped + (" " * pad)


def _utc_now_label() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _time_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "--:--"
    compact = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(compact)
    except ValueError:
        return raw[:16]
    return parsed.strftime("%m-%d %H:%M")


def _intake_sessions_dir(paths: Any) -> Path:
    return Path(paths.monitor_dir) / "intake_sessions"


def _experiments_dir(paths: Any) -> Path:
    return Path(paths.monitor_dir) / "experiments"


def _experiments_current_path(paths: Any) -> Path:
    return _experiments_dir(paths) / "current.json"


def _experiment_manifest_path(paths: Any, experiment_id: str) -> Path:
    return _experiments_dir(paths) / experiment_id / "manifest.json"


def _read_experiment_manifest(paths: Any, experiment_id: str) -> dict[str, Any]:
    payload = read_json(_experiment_manifest_path(paths, experiment_id), {})
    return payload if isinstance(payload, dict) else {}


def _write_experiment_manifest(paths: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    experiment_id = str(manifest.get("experiment_id") or "").strip()
    if not experiment_id:
        raise ValueError("experiment_id 不能为空")
    path = _experiment_manifest_path(paths, experiment_id)
    manifest["path"] = str(path)
    write_json_atomic(path, manifest)
    import_experiment_manifest(paths, manifest, set_current=False)
    return manifest


def _write_current_experiment(paths: Any, manifest: dict[str, Any]) -> dict[str, str]:
    import_experiment_manifest(paths, manifest, set_current=True)
    payload = {
        "experiment_id": str(manifest.get("experiment_id") or ""),
        "path": str(manifest.get("path") or _experiment_manifest_path(paths, str(manifest.get("experiment_id") or ""))),
        "updated_at": _utc_now_label(),
    }
    write_json_atomic(_experiments_current_path(paths), payload)
    return payload


def _manifest_from_project(paths: Any, project: dict[str, Any]) -> dict[str, Any]:
    if not project:
        return {}
    project_id = str(project.get("project_id") or project.get("experiment_id") or "")
    return {
        "project_id": project_id,
        "experiment_id": project_id,
        "title": str(project.get("title") or "未命名项目"),
        "status": str(project.get("status") or "active"),
        "created_at": str(project.get("created_at") or ""),
        "updated_at": str(project.get("updated_at") or ""),
        "archived_at": str(project.get("archived_at") or ""),
        "resumed_at": str(project.get("resumed_at") or ""),
        "topic_id": str(project.get("topic_id") or ""),
        "topic_title": str(project.get("topic_title") or ""),
        "attempt_index": int(project.get("attempt_index") or 0),
        "attempt_title": str(project.get("attempt_title") or project.get("title") or ""),
        "task_fingerprint": str(project.get("task_fingerprint") or ""),
        "debug_flag": bool(project.get("debug_flag")),
        "title_source": str(project.get("title_source") or ""),
        "tags": list(project.get("tags") or []),
        "intake_session_id": str(project.get("intake_session_id") or project.get("current_session_id") or ""),
        "session_id": str(project.get("intake_session_id") or project.get("current_session_id") or ""),
        "program_id": str(project.get("program_id") or project.get("current_program_id") or ""),
        "program_status": str(project.get("program_status") or "not_started"),
        "pending_action": project.get("pending_action"),
        "run_ids": list(project.get("run_ids") or []),
        "active_run_id": str(project.get("active_run_id") or ""),
        "artifact_refs": list(project.get("artifact_refs") or []),
        "parent_project_id": str(project.get("parent_project_id") or ""),
        "source_snapshot_id": str(project.get("source_snapshot_id") or ""),
        "notes": [],
        "path": str(project.get("manifest_path") or _experiment_manifest_path(paths, project_id)),
    }


def _read_current_experiment_manifest(paths: Any) -> dict[str, Any]:
    current_project = lifecycle_get_current_project(paths)
    if current_project:
        return _manifest_from_project(paths, current_project)
    current = read_json(_experiments_current_path(paths), {})
    if not isinstance(current, dict):
        return {}
    experiment_id = str(current.get("experiment_id") or "").strip()
    if experiment_id:
        manifest = _read_experiment_manifest(paths, experiment_id)
        if manifest:
            import_experiment_manifest(paths, manifest, set_current=True)
            return manifest
    path_text = str(current.get("path") or "").strip()
    if path_text:
        payload = read_json(Path(path_text), {})
        if isinstance(payload, dict):
            import_experiment_manifest(paths, payload, set_current=True)
            return payload
    return {}


def _new_experiment_id() -> str:
    return f"exp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _pending_action_program_id(pending_action: dict[str, Any] | None) -> str:
    if not isinstance(pending_action, dict):
        return ""
    draft = pending_action.get("program_draft")
    if isinstance(draft, dict):
        return str(draft.get("program_id") or "").strip()
    return str(pending_action.get("program_id") or "").strip()


def _experiment_title_from_state(
    *,
    pending_action: dict[str, Any] | None = None,
    session_history: list[dict[str, Any]] | None = None,
    fallback: str = "未命名实验",
) -> str:
    if isinstance(pending_action, dict):
        normalized = str(pending_action.get("normalized_request") or "").strip()
        if normalized:
            return _clip_display(normalized, 28)
        draft = pending_action.get("program_draft")
        if isinstance(draft, dict) and str(draft.get("program_id") or "").strip():
            return str(draft.get("program_id"))
    for item in session_history or []:
        if str(item.get("role") or "") == "user" and str(item.get("text") or "").strip():
            return _clip_display(str(item.get("text")), 28)
    return fallback


def _program_draft_from_pending(pending_action: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pending_action, dict):
        return {}
    draft = pending_action.get("program_draft")
    return dict(draft) if isinstance(draft, dict) else {}


def _program_state_from_contract(program: dict[str, Any]) -> dict[str, str]:
    goal = program.get("research_goal") if isinstance(program.get("research_goal"), dict) else {}
    metrics = program.get("metrics") if isinstance(program.get("metrics"), dict) else {}
    return {
        "program_id": str(program.get("program_id") or "").strip(),
        "program_status": str(program.get("status") or "").strip(),
        "task_type": str(goal.get("task_type") or program.get("task_type") or "").strip(),
        "primary_metric": str(metrics.get("primary") or program.get("primary_metric") or "").strip(),
    }


def _program_state_from_artifact_refs(artifact_refs: list[str] | None) -> dict[str, str]:
    for ref in artifact_refs or []:
        path = Path(str(ref))
        if path.name != "program.json" or not path.exists():
            continue
        payload = read_json(path, {})
        if isinstance(payload, dict):
            state = _program_state_from_contract(payload)
            if state.get("program_id"):
                return state
    return {}


def _user_message_for_title(pending_action: dict[str, Any] | None, paths: Any) -> str:
    if isinstance(pending_action, dict):
        for key in ("normalized_request", "source_request_summary", "command_text"):
            value = str(pending_action.get(key) or "").strip()
            if value:
                return value
        draft = pending_action.get("program_draft")
        if isinstance(draft, dict):
            value = str(draft.get("source_request_summary") or "").strip()
            if value:
                return value
    for item in read_current_intake_history(paths, limit=8):
        if str(item.get("role") or "") == "user" and str(item.get("text") or "").strip():
            return str(item.get("text") or "").strip()
    return ""


def _apply_lifecycle_title(
    paths: Any,
    manifest: dict[str, Any],
    *,
    pending_action: dict[str, Any] | None,
    force: bool = False,
) -> dict[str, Any]:
    program_draft = _program_draft_from_pending(pending_action)
    user_message = _user_message_for_title(pending_action, paths)
    debug_flag = bool(manifest.get("debug_flag")) or is_tui_test_mode_enabled()
    service = TitleService()
    seed = service.suggest(user_message=user_message, program_draft=program_draft, attempt_index=1, debug_flag=debug_flag)
    project_id = str(manifest.get("project_id") or manifest.get("experiment_id") or "").strip()

    if seed.task_fingerprint:
        current_topic_id = str(manifest.get("topic_id") or "").strip()
        topic = lifecycle_get_topic(paths, current_topic_id) if current_topic_id else {}
        if not topic or str(topic.get("task_fingerprint") or "") != seed.task_fingerprint:
            topic = lifecycle_find_topic_by_fingerprint(paths, seed.task_fingerprint)
        if not topic:
            topic = lifecycle_create_topic(
                paths,
                topic_title=seed.topic_title,
                task_fingerprint=seed.task_fingerprint,
                tags=seed.tags,
            )
        elif str(topic.get("status") or "") == "archived" and str(manifest.get("status") or "") != "archived":
            topic = lifecycle_update_topic(paths, str(topic.get("topic_id") or ""), status="active", event_type="topic_reopen")

        topic_id = str(topic.get("topic_id") or "")
        existing_index = int(manifest.get("attempt_index") or 0)
        existing_topic_id = str(manifest.get("topic_id") or "")
        attempt_index = existing_index if existing_index > 0 and existing_topic_id == topic_id else lifecycle_next_attempt_index(paths, topic_id)
        suggestion = service.suggest(
            user_message=user_message,
            program_draft=program_draft,
            attempt_index=attempt_index,
            debug_flag=debug_flag,
        )
        manual_attempt_title = str(manifest.get("title_source") or "") == "manual_attempt" and not force
        manifest["topic_id"] = topic_id
        manifest["topic_title"] = str(topic.get("topic_title") or suggestion.topic_title)
        manifest["task_fingerprint"] = suggestion.task_fingerprint
        manifest["attempt_index"] = attempt_index
        manifest["debug_flag"] = suggestion.debug_flag
        manifest["tags"] = suggestion.tags
        if force or not manual_attempt_title:
            manifest["attempt_title"] = suggestion.attempt_title
            manifest["title"] = suggestion.attempt_title
            manifest["title_source"] = "regenerated" if force else suggestion.title_source
        elif manifest.get("attempt_title"):
            manifest["title"] = manifest["attempt_title"]
        return manifest

    if force or manifest.get("title") in {"", "未命名实验"} or not manifest.get("title"):
        suggestion = service.suggest(user_message=user_message, program_draft={}, attempt_index=1, debug_flag=debug_flag)
        manifest["title"] = suggestion.topic_title
        manifest["attempt_title"] = suggestion.topic_title
        manifest["title_source"] = suggestion.title_source
        manifest["debug_flag"] = debug_flag
    return manifest


def _sync_experiment_manifest(
    paths: Any,
    session_state: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
    force_title: bool = False,
) -> dict[str, Any]:
    manifest = _read_current_experiment_manifest(paths)
    if not manifest:
        manifest = _ensure_experiment_workspace(paths, session_state, snapshot=snapshot)
    now = _utc_now_label()
    pending = session_state.get("pending_action") if isinstance(session_state.get("pending_action"), dict) else None
    program_state = snapshot.get("program_state") if isinstance(snapshot, dict) and isinstance(snapshot.get("program_state"), dict) else {}
    if pending is not None:
        manifest["pending_action"] = dict(pending)
    else:
        manifest["pending_action"] = None
    artifact_program = _program_state_from_artifact_refs(artifact_refs)
    pending_draft = _program_draft_from_pending(pending)
    pending_program = _program_state_from_contract(pending_draft) if pending_draft else {}
    program_id = (
        str(artifact_program.get("program_id") or "").strip()
        or str(pending_program.get("program_id") or "").strip()
        or str(manifest.get("program_id") or "").strip()
    )
    program_status = (
        str(artifact_program.get("program_status") or "").strip()
        or str(pending_program.get("program_status") or "").strip()
        or str(manifest.get("program_status") or "").strip()
    )
    if not program_id:
        program_status = "not_started"
    manifest["program_id"] = program_id
    manifest["program_status"] = program_status or "not_started"
    del program_state
    if str(session_state.get("intake_session_id") or "").strip():
        manifest["intake_session_id"] = str(session_state.get("intake_session_id"))
    if artifact_refs:
        existing = [str(item) for item in manifest.get("artifact_refs", []) if str(item).strip()]
        for ref in artifact_refs:
            if str(ref).strip() and str(ref) not in existing:
                existing.append(str(ref))
        manifest["artifact_refs"] = existing
    manifest = _apply_lifecycle_title(paths, manifest, pending_action=pending, force=force_title)
    if manifest.get("title") in {"", "未命名实验"} or not manifest.get("title"):
        history = read_current_intake_history(paths, limit=8)
        manifest["title"] = _experiment_title_from_state(pending_action=pending, session_history=history)
    manifest["updated_at"] = now
    _write_experiment_manifest(paths, manifest)
    _write_current_experiment(paths, manifest)
    return manifest


def _ensure_experiment_workspace(
    paths: Any,
    session_state: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = _read_current_experiment_manifest(paths)
    experiment_id = str(session_state.get("experiment_id") or "").strip()
    if experiment_id and (not current or str(current.get("experiment_id") or "") != experiment_id):
        current = _read_experiment_manifest(paths, experiment_id)
    if current:
        session_state["experiment_id"] = str(current.get("experiment_id") or "")
        intake_session_id = str(current.get("intake_session_id") or "").strip()
        if intake_session_id:
            session_state["intake_session_id"] = intake_session_id
            _ensure_intake_session(paths, session_state)
        pending = current.get("pending_action")
        if isinstance(pending, dict) and not isinstance(session_state.get("pending_action"), dict):
            session_state["pending_action"] = dict(pending)
        _write_current_experiment(paths, current)
        return current

    session = _ensure_intake_session(paths, session_state)
    now = _utc_now_label()
    program_state = snapshot.get("program_state") if isinstance(snapshot, dict) and isinstance(snapshot.get("program_state"), dict) else {}
    experiment_id = _new_experiment_id()
    session_state["experiment_id"] = experiment_id
    manifest = {
        "experiment_id": experiment_id,
        "title": "未命名实验",
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "archived_at": "",
        "resumed_at": "",
        "topic_id": "",
        "topic_title": "",
        "attempt_index": 0,
        "attempt_title": "",
        "task_fingerprint": "",
        "debug_flag": is_tui_test_mode_enabled(),
        "title_source": "",
        "tags": [],
        "intake_session_id": session["session_id"],
        "program_id": str(program_state.get("program_id") or ""),
        "program_status": str(program_state.get("status") or "not_started"),
        "pending_action": session_state.get("pending_action") if isinstance(session_state.get("pending_action"), dict) else None,
        "run_ids": [],
        "artifact_refs": [],
        "parent_experiment_id": "",
        "notes": [],
    }
    _write_experiment_manifest(paths, manifest)
    _write_current_experiment(paths, manifest)
    return manifest


def _archive_current_experiment(paths: Any, session_state: dict[str, Any], *, reason: str) -> dict[str, Any]:
    manifest = _sync_experiment_manifest(paths, session_state)
    manifest["status"] = "archived"
    manifest["archived_at"] = _utc_now_label()
    manifest["archive_reason"] = reason
    _write_experiment_manifest(paths, manifest)
    return manifest


def start_new_experiment_workspace(
    paths: Any,
    session_state: dict[str, Any],
    *,
    archive_current: bool = True,
    archive_reason: str = "rotated_by_new",
) -> dict[str, Any]:
    if archive_current and _read_current_experiment_manifest(paths):
        _archive_current_experiment(paths, session_state, reason=archive_reason)
    session_state.pop("pending_action", None)
    session_state["experiment_id"] = _new_experiment_id()
    start_new_intake_session(paths, session_state)
    now = _utc_now_label()
    manifest = {
        "experiment_id": str(session_state["experiment_id"]),
        "title": "未命名实验",
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "archived_at": "",
        "resumed_at": "",
        "topic_id": "",
        "topic_title": "",
        "attempt_index": 0,
        "attempt_title": "",
        "task_fingerprint": "",
        "debug_flag": is_tui_test_mode_enabled(),
        "title_source": "",
        "tags": [],
        "intake_session_id": str(session_state.get("intake_session_id") or ""),
        "program_id": "",
        "program_status": "not_started",
        "pending_action": None,
        "run_ids": [],
        "artifact_refs": [],
        "parent_experiment_id": "",
        "notes": [],
    }
    _write_experiment_manifest(paths, manifest)
    _write_current_experiment(paths, manifest)
    return manifest


def list_experiment_manifests(paths: Any) -> list[dict[str, Any]]:
    rows = [_manifest_from_project(paths, item) for item in lifecycle_list_projects(paths)]
    if rows:
        return rows
    experiments_dir = _experiments_dir(paths)
    if not experiments_dir.exists():
        return []
    legacy_rows: list[dict[str, Any]] = []
    for path in sorted(experiments_dir.glob("*/manifest.json")):
        payload = read_json(path, {})
        if isinstance(payload, dict) and str(payload.get("experiment_id") or "").strip():
            import_experiment_manifest(paths, payload, set_current=False)
            legacy_rows.append(payload)
    legacy_rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return legacy_rows


def format_experiments_list(paths: Any) -> str:
    rows = list_experiment_manifests(paths)
    if not rows:
        return "实验工作区列表：当前还没有实验工作区。"
    lines = ["实验工作区列表（Project 兼容视图）："]
    current = _read_current_experiment_manifest(paths)
    current_id = str(current.get("experiment_id") or "")
    for item in rows[:20]:
        marker = "*" if str(item.get("experiment_id") or "") == current_id else "-"
        title = str(item.get("title") or "未命名实验")
        lines.append(
            f"{marker} {item.get('experiment_id')} · {item.get('status') or '-'} · "
            f"{title} · Program:{item.get('program_status') or 'not_started'} · "
            f"updated:{item.get('updated_at') or '-'}"
        )
    return "\n".join(lines)


def resume_experiment_workspace(paths: Any, session_state: dict[str, Any], experiment_id: str) -> tuple[dict[str, Any], list[str]]:
    project = lifecycle_get_project(paths, experiment_id)
    target = _manifest_from_project(paths, lifecycle_resume_project(paths, experiment_id)) if project else _read_experiment_manifest(paths, experiment_id)
    if not target:
        raise ValueError(f"找不到实验工作区：{experiment_id}")
    current = _read_current_experiment_manifest(paths)
    if current and str(current.get("experiment_id") or "") != experiment_id:
        _archive_current_experiment(paths, session_state, reason="switched_by_resume")
    target["status"] = "active"
    target["resumed_at"] = _utc_now_label()
    target["updated_at"] = _utc_now_label()
    session_state["experiment_id"] = experiment_id
    intake_session_id = str(target.get("intake_session_id") or "").strip()
    if intake_session_id:
        session_state["intake_session_id"] = intake_session_id
        _ensure_intake_session(paths, session_state)
    pending = target.get("pending_action")
    if isinstance(pending, dict):
        session_state["pending_action"] = dict(pending)
    else:
        session_state.pop("pending_action", None)
    _write_experiment_manifest(paths, target)
    _write_current_experiment(paths, target)
    missing_refs = []
    for ref in target.get("artifact_refs", []) if isinstance(target.get("artifact_refs"), list) else []:
        ref_text = str(ref or "").strip()
        if ref_text.startswith("/") and not Path(ref_text).exists():
            missing_refs.append(ref_text)
    return target, missing_refs


def _experiment_state_for_snapshot(paths: Any, session_state: dict[str, Any]) -> dict[str, Any]:
    manifest = _ensure_experiment_workspace(paths, session_state)
    project_id = str(manifest.get("project_id") or manifest.get("experiment_id") or "")
    session_id = str(manifest.get("session_id") or manifest.get("intake_session_id") or "")
    run_ids = manifest.get("run_ids") if isinstance(manifest.get("run_ids"), list) else []
    active_run_id = str(manifest.get("active_run_id") or (run_ids[0] if run_ids else ""))
    return {
        "project_id": project_id,
        "experiment_id": project_id,
        "session_id": session_id,
        "intake_session_id": session_id,
        "title": str(manifest.get("title") or "未命名实验"),
        "status": str(manifest.get("status") or "active"),
        "program_id": str(manifest.get("program_id") or ""),
        "program_status": str(manifest.get("program_status") or "not_started"),
        "active_run_id": active_run_id,
        "pending_action_kind": (
            str(manifest.get("pending_action", {}).get("user_intent_kind") or "")
            if isinstance(manifest.get("pending_action"), dict)
            else ""
        ),
    }


def _attach_experiment_state(
    snapshot: dict[str, object],
    paths: Any,
    session_state: dict[str, Any],
) -> dict[str, object]:
    enriched = dict(snapshot)
    enriched["experiment_state"] = _experiment_state_for_snapshot(paths, session_state)
    try:
        enriched["research_loop"] = status_research_loop(paths.repo_root, task_id=DEFAULT_RESEARCH_TASK_ID)
    except Exception:
        enriched["research_loop"] = {"available": False}
    return enriched


def _ensure_intake_session(paths: Any, session_state: dict[str, Any]) -> dict[str, str]:
    sessions_dir = _intake_sessions_dir(paths)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    current_path = sessions_dir / "current.json"
    current = read_json(current_path, {})
    session_id = str(session_state.get("intake_session_id") or "").strip()
    if not session_id and isinstance(current, dict):
        session_id = str(current.get("session_id") or "").strip()
    if not session_id:
        session_id = str(session_state.get("session_id") or f"intake-{uuid.uuid4().hex[:12]}")
    session_state["intake_session_id"] = session_id
    history_path = sessions_dir / f"{session_id}.jsonl"
    payload = {
        "session_id": session_id,
        "path": str(history_path),
        "updated_at": _utc_now_label(),
    }
    write_json_atomic(current_path, payload)
    return payload


def start_new_intake_session(paths: Any, session_state: dict[str, Any]) -> dict[str, str]:
    session_state["intake_session_id"] = f"intake-{uuid.uuid4().hex[:12]}"
    return _ensure_intake_session(paths, session_state)


def append_intake_history_turn(
    paths: Any,
    session_state: dict[str, Any],
    *,
    turn_id: str,
    role: str,
    text: str,
    intent_kind: str = "",
    program_id: str = "",
    run_id: str = "",
    refs: list[str] | None = None,
    visibility: str = "intake_only",
) -> dict[str, Any]:
    session = _ensure_intake_session(paths, session_state)
    row = {
        "turn_id": turn_id,
        "created_at": _utc_now_label(),
        "role": role,
        "text": str(text or "").strip(),
        "intent_kind": intent_kind,
        "program_id": program_id,
        "run_id": run_id,
        "refs": list(refs or []),
        "visibility": visibility,
    }
    append_jsonl(Path(session["path"]), row)
    _ensure_intake_session(paths, session_state)
    return row


def read_current_intake_history(paths: Any, *, limit: int = INTAKE_HISTORY_LIMIT) -> list[dict[str, Any]]:
    current = read_json(_intake_sessions_dir(paths) / "current.json", {})
    if not isinstance(current, dict):
        return []
    path_text = str(current.get("path") or "").strip()
    if not path_text:
        return []
    history_path = Path(path_text)
    if not history_path.exists():
        return []
    rows = [row for row in read_jsonl(history_path) if isinstance(row, dict)]
    if limit <= 0:
        return rows
    return rows[-limit:]


def _rich_text(value: str, *, style: str | None = None, no_wrap: bool = False) -> Text:
    text = Text(value, style=style or PALETTE["text"], no_wrap=no_wrap, overflow="ellipsis")
    return text


def _hex_to_rgb(hex_value: str) -> tuple[int, int, int]:
    value = hex_value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _build_terminal_theme() -> TerminalTheme | None:
    if not RICH_AVAILABLE or TerminalTheme is None:
        return None
    normal = [
        _hex_to_rgb("#0b171c"),
        _hex_to_rgb(PALETTE["accent"]),
        _hex_to_rgb(PALETTE["success"]),
        _hex_to_rgb(PALETTE["warning"]),
        _hex_to_rgb("#86b8d8"),
        _hex_to_rgb("#a995c7"),
        _hex_to_rgb("#6bb0a9"),
        _hex_to_rgb(PALETTE["text"]),
    ]
    bright = [
        _hex_to_rgb("#15303a"),
        _hex_to_rgb(PALETTE["accent"]),
        _hex_to_rgb(PALETTE["success"]),
        _hex_to_rgb(PALETTE["warning"]),
        _hex_to_rgb("#a2d2ef"),
        _hex_to_rgb("#b8a9d8"),
        _hex_to_rgb("#87c7bf"),
        _hex_to_rgb("#f4f6f0"),
    ]
    return TerminalTheme(
        background=_hex_to_rgb(PALETTE["background"]),
        foreground=_hex_to_rgb(PALETTE["text"]),
        normal=normal,
        bright=bright,
    )


def _build_prompt_toolkit_style() -> PTStyle | None:
    if not PROMPT_TOOLKIT_AVAILABLE or PTStyle is None:
        return None
    return PTStyle.from_dict(
        {
            "app": f"bg:{PALETTE['background']} {PALETTE['text']}",
            "header": f"bg:{PALETTE['panel_alt']} {PALETTE['text']}",
            "header.brand": f"bg:{PALETTE['panel_alt']} {PALETTE['text']} bold",
            "header.accent": f"bg:{PALETTE['panel_alt']} {PALETTE['accent']} bold",
            "header.muted": f"bg:{PALETTE['panel_alt']} {PALETTE['muted']}",
            "header.banner.label": f"bg:#2b3440 {PALETTE['accent']} bold",
            "header.banner.value": f"bg:#232b35 {PALETTE['text']}",
            "panel": f"bg:{PALETTE['panel_bg']} {PALETTE['text']}",
            "panel.border": PALETTE["border"],
            "panel.title": f"{PALETTE['accent']} bold",
            "panel.key": f"{PALETTE['accent']} bold",
            "panel.value": PALETTE["text"],
            "panel.good": PALETTE["success"],
            "panel.muted": PALETTE["muted"],
            "director.header": f"{PALETTE['accent']} bold",
            "executor.header": f"{PALETTE['text']} bold",
            "director.title.active": f"{PALETTE['accent']} bold",
            "director.title.inactive": f"{PALETTE['accent']}",
            "executor.title.active": f"{PALETTE['success']} bold",
            "executor.title.inactive": PALETTE["text"],
            "director.frame.active": "bg:#242b35",
            "director.frame.inactive": "bg:#171c23",
            "executor.frame.active": "bg:#202934",
            "executor.frame.inactive": "bg:#171c23",
            "director.body.active": f"bg:#242b35 {PALETTE['text']}",
            "director.body.inactive": f"bg:{PALETTE['panel_bg']} {PALETTE['muted']}",
            "executor.body.active": f"bg:#202934 {PALETTE['text']}",
            "executor.body.inactive": f"bg:{PALETTE['panel_bg']} {PALETTE['muted']}",
            "thinking": f"{PALETTE['accent']} bold",
            "thinking.director.live0": f"{PALETTE['warning']} bold",
            "thinking.director.live1": f"#f0d8aa bold",
            "thinking.director.live2": f"{PALETTE['accent']} bold",
            "thinking.executor.live0": f"{PALETTE['success']} bold",
            "thinking.executor.live1": f"#c9ddf1 bold",
            "thinking.executor.live2": f"#9ebed7 bold",
            "thinking.dot.active": f"{PALETTE['accent']} bold",
            "thinking.dot.dim": PALETTE["muted"],
            "item.time": PALETTE["muted"],
            "item.title.director": f"{PALETTE['accent']} bold",
            "item.title.executor": f"{PALETTE['success']} bold",
            "item.title.handoff": f"{PALETTE['warning']} bold",
            "item.detail": PALETTE["text"],
            "item.result": PALETTE["success"],
            "item.next": PALETTE["muted"],
            "item.empty": PALETTE["muted"],
            "markdown.bold": "bold",
            "message.user.prefix": f"{PALETTE['accent']} bold",
            "message.user.text": PALETTE["user_text"],
            "message.agent.prefix": f"{PALETTE['success']} bold",
            "message.agent.text": PALETTE["agent_text"],
            "message.tool.text": PALETTE["tool_text"],
            "scrollbar.background": "bg:#131821",
            "scrollbar.button": "bg:#5f6d7f",
            "scrollbar.arrow": f"bg:#232b35 {PALETTE['accent']}",
            "input-area": f"bg:{PALETTE['panel_alt']} {PALETTE['text']}",
            "input-frame.border": PALETTE["border"],
            "input-frame.label": f"{PALETTE['accent']} bold",
            "prompt.label": f"{PALETTE['accent']} bold",
            "prompt.arrow": PALETTE["muted"],
            "placeholder": f"{PALETTE['muted']} italic",
            "completion-menu": f"bg:{PALETTE['panel_alt']} {PALETTE['text']}",
            "completion-menu.completion.current": f"bg:#2b3440 {PALETTE['accent']} bold",
            "completion-menu.meta.completion": f"bg:{PALETTE['panel_alt']} {PALETTE['muted']}",
            "completion-menu.meta.completion.current": f"bg:#2b3440 {PALETTE['text']}",
            "completion-frame": f"bg:{PALETTE['panel_alt']} {PALETTE['text']}",
            "completion-frame.border": PALETTE["border"],
            "completion-frame.label": PALETTE["muted"],
        }
    )


if PTFormattedTextControl is not None:

    class _PaneFormattedTextControl(PTFormattedTextControl):  # type: ignore[misc]
        def __init__(
            self,
            *args: Any,
            on_scroll: Callable[[int], None] | None = None,
            on_focus: Callable[[], None] | None = None,
            **kwargs: Any,
        ) -> None:
            self._on_scroll = on_scroll
            self._on_focus = on_focus
            super().__init__(*args, **kwargs)

        def mouse_handler(self, mouse_event: Any) -> Any:
            if self._on_focus is not None:
                self._on_focus()
            if MouseEventType is not None:
                if mouse_event.event_type == MouseEventType.SCROLL_UP:
                    if self._on_scroll is not None:
                        self._on_scroll(-3)
                    return None
                if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                    if self._on_scroll is not None:
                        self._on_scroll(3)
                    return None
            return None

else:

    class _PaneFormattedTextControl:  # pragma: no cover - prompt_toolkit fallback
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("prompt_toolkit is required for interactive TUI mode")


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_event_time(value: Any) -> str:
    dt = _parse_timestamp(value)
    if dt is None:
        return "--:--"
    return dt.astimezone().strftime("%m-%d %H:%M")


def _trim_text(value: Any, max_len: int = 84) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + "…"


def _transcript_text(value: Any) -> str:
    text = str(value or "").strip()
    return text or "-"


def _normalize_transcript_for_compare(value: Any) -> str:
    return " ".join(str(value or "").split())


def _inline_markdown_segments(value: Any) -> list[tuple[str, bool]]:
    text = str(value or "")
    if not text:
        return [("-", False)]
    segments: list[tuple[str, bool]] = []
    position = 0
    for match in re.finditer(r"(\*\*|__)(.+?)\1", text):
        if match.start() > position:
            segments.append((text[position : match.start()], False))
        if match.group(2):
            segments.append((match.group(2), True))
        position = match.end()
    if position < len(text):
        segments.append((text[position:], False))
    return segments or [("-", False)]


def _pt_markdown_fragments(value: Any, base_style: str) -> StyleAndTextTuples:
    fragments: list[tuple[str, str]] = []
    for segment, bold in _inline_markdown_segments(value):
        style = f"{base_style} class:markdown.bold" if bold else base_style
        fragments.append((style, segment))
    return fragments


def _rich_markdown_text(value: Any, *, style: str) -> Text:
    text = Text()
    for segment, bold in _inline_markdown_segments(value):
        segment_style = f"{style} bold" if bold else style
        text.append(segment, style=segment_style)
    return text


def _append_work_item(
    rows: list[dict[str, Any]],
    *,
    role: str,
    recorded_at: Any,
    title: str,
    detail: str,
    result: str = "-",
    next_step: str = "-",
    source: str = "",
) -> None:
    if not title and not detail:
        return
    rows.append(
        {
            "role": role,
            "recorded_at": str(recorded_at or ""),
            "sort_at": _parse_timestamp(recorded_at) or datetime.min.replace(tzinfo=timezone.utc),
            "time_label": _format_event_time(recorded_at),
            "title": title or "-",
            "detail": _trim_text(detail),
            "result": _trim_text(result),
            "next": _trim_text(next_step),
            "source": source or "-",
            "is_handoff": str(source or "").startswith("handoff"),
        }
    )


def _format_framework_benchmark_banner(snapshot: dict[str, object]) -> str:
    bench = snapshot.get("framework_benchmark") if isinstance(snapshot.get("framework_benchmark"), dict) else {}
    status = str(bench.get("status") or bench.get("benchmark_status") or "").strip().lower()
    publish_ready = bool(bench.get("publish_ready") or bench.get("verified"))
    if not publish_ready and status not in {"verified", "complete", "published"}:
        return ""
    total_iterations = bench.get("total_iterations")
    if not total_iterations:
        return ""

    breakthrough_rate = bench.get("breakthrough_rate")
    cost_per_breakthrough = bench.get("cost_per_breakthrough")
    diversity_index = bench.get("diversity_index")
    iterations_per_hour = bench.get("iterations_per_hour")

    parts = [f"总迭代 {int(total_iterations)}"]
    if isinstance(breakthrough_rate, (int, float)):
        parts.append(f"突破率 {breakthrough_rate * 100:.1f}%")
    if isinstance(cost_per_breakthrough, (int, float)):
        parts.append(f"每次突破 {cost_per_breakthrough:.1f}轮")
    if isinstance(diversity_index, (int, float)):
        parts.append(f"多样性 {diversity_index:.2f}")
    if isinstance(iterations_per_hour, (int, float)):
        parts.append(f"吞吐 {iterations_per_hour:.1f}/h")
    return " | ".join(parts)


def build_agent_work_items(snapshot: dict[str, object], *, limit: int = 6) -> list[dict[str, Any]]:
    latest_retrieval = snapshot.get("latest_retrieval_packet") if isinstance(snapshot.get("latest_retrieval_packet"), dict) else {}
    latest_decision = snapshot.get("latest_decision_packet") if isinstance(snapshot.get("latest_decision_packet"), dict) else {}
    latest_judgment_updates = snapshot.get("latest_judgment_updates") if isinstance(snapshot.get("latest_judgment_updates"), list) else []
    recent_control_events = snapshot.get("recent_control_events") if isinstance(snapshot.get("recent_control_events"), list) else []
    autoresearch_status = snapshot.get("autoresearch_status") if isinstance(snapshot.get("autoresearch_status"), dict) else {}
    active_track_id = (
        str(autoresearch_status.get("active_track_id") or "").strip()
        or str(snapshot.get("current_track_id") or "").strip()
    )
    candidate = autoresearch_status.get("candidate") if isinstance(autoresearch_status.get("candidate"), dict) else {}
    track_states = autoresearch_status.get("track_states") if isinstance(autoresearch_status.get("track_states"), list) else []
    active_track_state = next(
        (
            item
            for item in track_states
            if isinstance(item, dict) and str(item.get("track_id") or "").strip() == active_track_id
        ),
        {},
    )
    current_problem = (
        str(latest_retrieval.get("current_problem_statement") or "").strip()
        or str(snapshot.get("last_research_judgment_update") or "").strip()
        or "当前还没有结构化关键问题。"
    )
    recommended_queue = [
        str(item).strip()
        for item in (latest_decision.get("recommended_queue") or [])
        if str(item).strip()
    ]
    latest_judgment = latest_judgment_updates[0] if latest_judgment_updates else {}

    rows: list[dict[str, Any]] = []
    if latest_decision:
        _append_work_item(
            rows,
            role="Director",
            recorded_at=latest_decision.get("recorded_at"),
            title="更新推荐执行队列",
            detail=str(latest_decision.get("research_judgment_delta") or "当前还没有新的队列判断。"),
            result="推荐队列：" + (" / ".join(recommended_queue[:3]) if recommended_queue else "当前还没有推荐队列。"),
            next_step=str(latest_judgment.get("next_recommended_action") or (recommended_queue[0] if recommended_queue else "等待下一轮判断。")),
            source="latest_decision_packet",
        )
        if recommended_queue or str(latest_judgment.get("next_recommended_action") or "").strip():
            _append_work_item(
                rows,
                role="Director",
                recorded_at=latest_decision.get("recorded_at"),
                title="方向选择 -> 执行沙盒",
                detail=(
                    f"已下发下一步：{recommended_queue[0]}"
                    if recommended_queue
                    else "已下发下一步判断，等待执行侧接手。"
                ),
                result="推荐队列：" + (" / ".join(recommended_queue[:3]) if recommended_queue else "当前还没有推荐队列。"),
                next_step=str(latest_judgment.get("next_recommended_action") or (recommended_queue[0] if recommended_queue else "等待执行侧接手。")),
                source="handoff_director_executor",
            )
    for item in latest_judgment_updates[:2]:
        if not isinstance(item, dict):
            continue
        _append_work_item(
            rows,
            role="Director",
            recorded_at=item.get("recorded_at"),
            title="写入 judgment",
            detail=str(item.get("reason") or "当前还没有新的 judgment。"),
            result=(
                "topic "
                + (str(item.get("topic_id") or "-"))
                + (f" · hypothesis {item.get('hypothesis_id')}" if str(item.get("hypothesis_id") or "").strip() else "")
            ),
            next_step=str(item.get("next_recommended_action") or "等待下一轮判断。"),
            source="latest_judgment_updates",
        )
    _append_work_item(
        rows,
        role="Executor",
        recorded_at=autoresearch_status.get("updated_at"),
        title="当前执行",
        detail=(
            str(snapshot.get("current_track_id") or "").strip()
            or str(autoresearch_status.get("active_track_id") or "").strip()
            or "当前还没有 active track。"
        ),
        result=(
            str(snapshot.get("stage") or "").strip()
            or str(autoresearch_status.get("stage") or "").strip()
            or "未知"
        ),
        next_step=(
            str(autoresearch_status.get("current_command") or "").strip()
            or (recommended_queue[0] if recommended_queue else "等待方向选择给出下一步。")
        ),
        source="autoresearch_status",
    )
    if candidate:
        candidate_track_id = str(candidate.get("track_id") or "").strip() or active_track_id or "当前还没有候选 track。"
        candidate_stage = str(candidate.get("stage") or "").strip() or "未知阶段"
        _append_work_item(
            rows,
            role="Executor",
            recorded_at=(
                candidate.get("last_materialization_at")
                or candidate.get("last_decision_at")
                or candidate.get("last_retrieval_at")
                or autoresearch_status.get("updated_at")
            ),
            title="候选阶段",
            detail=str(candidate.get("track_goal") or f"{candidate_track_id} 当前还没有目标摘要。"),
            result=f"阶段 {candidate_stage} · {candidate_track_id}",
            next_step=str(candidate.get("next_step") or "等待候选改动生成。"),
            source="autoresearch_status",
        )
    if active_track_state:
        track_stage = str(active_track_state.get("stage") or "").strip() or str(autoresearch_status.get("stage") or "").strip() or "未知阶段"
        track_goal = str(active_track_state.get("track_goal") or "").strip() or active_track_id or "当前还没有 active track。"
        _append_work_item(
            rows,
            role="Executor",
            recorded_at=active_track_state.get("updated_at") or autoresearch_status.get("updated_at"),
            title="执行主线",
            detail=track_goal,
            result=str(active_track_state.get("last_result_summary") or f"阶段 {track_stage}"),
            next_step=(
                str(autoresearch_status.get("current_command") or "").strip()
                or str(candidate.get("next_step") or "").strip()
                or "继续等待结果回写。"
            ),
            source="autoresearch_status",
        )
        _append_work_item(
            rows,
            role="Executor",
            recorded_at=active_track_state.get("updated_at") or autoresearch_status.get("updated_at"),
            title="执行沙盒 -> 方向选择",
            detail=(
                str(active_track_state.get("last_result_summary") or "").strip()
                or str(candidate.get("next_step") or "").strip()
                or "当前执行侧等待方向选择重新判断。"
            ),
            result=f"阶段 {track_stage} · {active_track_id or '-'}",
            next_step=(
                str(candidate.get("next_step") or "").strip()
                or str(latest_judgment.get("next_recommended_action") or "").strip()
                or "等待方向选择重新判断。"
            ),
            source="handoff_executor_director",
        )

    action_role_map = {
        "think": "Director",
        "execute": "Executor",
        "pause": "Executor",
        "resume": "Executor",
        "end": "Executor",
    }
    action_title_map = {
        "think": "提交思考动作",
        "execute": "执行队列动作",
        "pause": "暂停当前执行",
        "resume": "恢复当前执行",
        "end": "结束当前轮次",
    }
    for event in recent_control_events[:4]:
        if not isinstance(event, dict):
            continue
        action = str(event.get("action") or "event").strip().lower()
        _append_work_item(
            rows,
            role=action_role_map.get(action, "Research Memory"),
            recorded_at=event.get("recorded_at"),
            title=action_title_map.get(action, "记录控制事件"),
            detail=str(event.get("message") or "当前还没有控制消息。"),
            result="成功" if bool(event.get("ok")) else "失败",
            next_step=str(latest_judgment.get("next_recommended_action") or (recommended_queue[0] if recommended_queue else "等待下一步。")),
            source="control_event",
        )

    if len([item for item in rows if item["role"] in {"Director", "Executor"}]) < 4:
        _append_work_item(
            rows,
            role="Research Memory",
            recorded_at=latest_retrieval.get("recorded_at"),
            title="更新当前问题与证据",
            detail=current_problem,
            result=f"证据 {len(latest_retrieval.get('relevant_evidence') or [])} 条",
            next_step=str(latest_decision.get("research_judgment_delta") or (recommended_queue[0] if recommended_queue else "等待新的判断。")),
            source="latest_retrieval_packet",
        )

    source_priority = {
        "autoresearch_status": 5,
        "control_event": 4,
        "latest_decision_packet": 3,
        "latest_judgment_updates": 2,
        "latest_retrieval_packet": 1,
    }
    rows.sort(key=lambda item: (item["sort_at"], source_priority.get(str(item.get("source")), 0)), reverse=True)
    return rows[:limit]


def infer_active_agent(snapshot: dict[str, object], items: list[dict[str, Any]]) -> str | None:
    autoresearch_status = snapshot.get("autoresearch_status") if isinstance(snapshot.get("autoresearch_status"), dict) else {}
    current_command = str(autoresearch_status.get("current_command") or "").strip()
    latest_director = next((item for item in items if item.get("role") == "Director"), None)
    latest_executor = next((item for item in items if item.get("role") == "Executor"), None)
    latest_director_at = latest_director.get("sort_at") if isinstance(latest_director, dict) else None
    latest_executor_at = latest_executor.get("sort_at") if isinstance(latest_executor, dict) else None

    if current_command:
        if latest_executor_at is None or (latest_director_at is not None and latest_director_at > latest_executor_at):
            return "Director"
        return "Executor"
    if latest_director_at and (latest_executor_at is None or latest_director_at >= latest_executor_at):
        return "Director"
    if latest_executor_at:
        return "Executor"
    return None


def _thinking_label(active: bool, ui_tick: int) -> str:
    if not active:
        return ""
    return "  ✦ thinking..."


def _thinking_title_style(role: str, active: bool, ui_tick: int) -> str:
    if not active:
        return f"class:{role.lower()}.title.inactive"
    phase = ui_tick % 3
    return f"class:thinking.{role.lower()}.live{phase}"


def _thinking_title_fragments(role: str, active: bool, ui_tick: int) -> StyleAndTextTuples:
    role_key = role.lower()
    title_style = f"class:{role_key}.title.active" if active else f"class:{role_key}.title.inactive"
    fragments: list[tuple[str, str]] = [(title_style, f" {role}")]
    if not active:
        return fragments

    phase = ui_tick % 3
    fragments.extend(
        [
            ("class:panel.muted", "  "),
            (_thinking_title_style(role, True, ui_tick), "✦ thinking"),
            ("class:panel.muted", "."),
        ]
    )
    for dot_index in range(2):
        dot_style = "class:thinking.dot.active" if (phase + dot_index) % 3 == 0 else "class:thinking.dot.dim"
        fragments.append((dot_style, "."))
    return fragments


def _work_item_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("role") or ""),
            str(item.get("recorded_at") or ""),
            str(item.get("title") or ""),
            str(item.get("detail") or ""),
            str(item.get("result") or ""),
            str(item.get("next") or ""),
            str(item.get("source") or ""),
        ]
    )


def merge_agent_histories(
    current_histories: dict[str, list[dict[str, Any]]] | None,
    items: list[dict[str, Any]],
    *,
    limit: int = AGENT_HISTORY_LIMIT,
) -> dict[str, list[dict[str, Any]]]:
    histories = {
        "Director": list((current_histories or {}).get("Director") or []),
        "Executor": list((current_histories or {}).get("Executor") or []),
    }
    seen = {
        role: {_work_item_key(item) for item in rows if isinstance(item, dict)}
        for role, rows in histories.items()
    }
    for item in sorted(
        [entry for entry in items if entry.get("role") in {"Director", "Executor"}],
        key=lambda entry: entry.get("sort_at") or datetime.min.replace(tzinfo=timezone.utc),
    ):
        role = str(item.get("role") or "")
        key = _work_item_key(item)
        if key in seen[role]:
            continue
        histories[role].append(dict(item))
        seen[role].add(key)
        if len(histories[role]) > limit:
            histories[role] = histories[role][-limit:]
            seen[role] = {_work_item_key(row) for row in histories[role]}
    return histories


def _select_agent_items(items: list[dict[str, Any]], role: str, *, fallback_role: str | None = None, count: int = 3) -> list[dict[str, Any]]:
    direct = [item for item in items if item.get("role") == role][:count]
    if direct or not fallback_role:
        return direct
    fallback = [item for item in items if item.get("role") == fallback_role][:1]
    if not fallback:
        return []
    patched = dict(fallback[0])
    patched["title"] = f"{fallback_role} · {patched.get('title', '-')}"
    return [patched]


def _history_signature(items: list[dict[str, Any]]) -> str:
    return "||".join(_work_item_key(item) for item in items[:AGENT_HISTORY_LIMIT] if isinstance(item, dict))


def _apply_reveal_count(items: list[dict[str, Any]], *, active: bool, reveal_count: int | None) -> list[dict[str, Any]]:
    if not active or not items:
        return items
    visible = max(1, min(int(reveal_count or 1), len(items)))
    return items[:visible]


def _output_history_text(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("text") or "")
    return str(entry or "")


def _output_history_created_at(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("created_at") or entry.get("recorded_at") or "")
    return ""


def _make_output_history_entry(text: str, *, turn_id: str | None = None) -> dict[str, str]:
    entry = {"text": str(text), "created_at": _utc_now_label()}
    if turn_id:
        entry["turn_id"] = str(turn_id)
    return entry


def _turn_sort_key(value: object) -> int | None:
    text = str(value or "")
    match = re.search(r"turn-(\d+)$", text)
    if not match:
        return None
    return int(match.group(1))


def build_timeline_items(
    session_history: list[dict[str, Any]] | None,
    output_history: list[object] | None = None,
    *,
    limit: int = INTAKE_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    rows = [dict(item) for item in (session_history or []) if isinstance(item, dict)]
    if not rows:
        for index, entry in enumerate(output_history or []):
            text = _output_history_text(entry).strip()
            if not text:
                continue
            role = "user" if text.startswith("AutoBCI>") else "intake"
            rows.append(
                {
                    "created_at": _output_history_created_at(entry),
                    "time_label": "--:--",
                    "role": role,
                    "text": text,
                    "intent_kind": "shell_output",
                    "visibility": "intake_only",
                    "sort_index": index,
                }
            )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(rows[-limit:]):
        created_at = item.get("created_at") or item.get("recorded_at") or ""
        normalized.append(
            {
                "created_at": str(created_at),
                "time_label": _time_label(created_at),
                "role": str(item.get("role") or "intake"),
                "text": _transcript_text(item.get("text") or item.get("message") or ""),
                "turn_id": str(item.get("turn_id") or ""),
                "intent_kind": str(item.get("intent_kind") or item.get("user_intent_kind") or ""),
                "program_id": str(item.get("program_id") or ""),
                "run_id": str(item.get("run_id") or ""),
                "refs": list(item.get("refs") or []),
                "visibility": str(item.get("visibility") or "intake_only"),
                "sort_index": index,
            }
        )
    return normalized


def build_program_panel_model(snapshot: dict[str, object]) -> dict[str, Any]:
    program = snapshot.get("program_state") if isinstance(snapshot.get("program_state"), dict) else {}
    experiment = snapshot.get("experiment_state") if isinstance(snapshot.get("experiment_state"), dict) else {}
    experiment_program_id = str(experiment.get("program_id") or "").strip()
    experiment_program_status = str(experiment.get("program_status") or "not_started").strip() or "not_started"
    if (
        experiment
        and "program_status" in experiment
        and not experiment_program_id
        and experiment_program_status == "not_started"
    ):
        program = {}
    elif experiment_program_id and str(program.get("program_id") or "").strip() != experiment_program_id:
        program = {
            "program_id": experiment_program_id,
            "status": experiment_program_status,
            "task_type": "",
            "primary_metric": "",
            "path": "",
        }
    recent_messages = snapshot.get("recent_messages") if isinstance(snapshot.get("recent_messages"), list) else []
    latest_judge = next(
        (item for item in recent_messages if isinstance(item, dict) and item.get("message_type") == "judge_report"),
        {},
    )
    latest_guard = next(
        (item for item in recent_messages if isinstance(item, dict) and item.get("message_type") == "policy_decision"),
        {},
    )
    latest_amendment = next(
        (item for item in recent_messages if isinstance(item, dict) and item.get("message_type") == "amendment_request"),
        {},
    )
    return {
        "program_id": str(program.get("program_id") or "-"),
        "version": str(program.get("version") or "-"),
        "status": str(program.get("status") or "not_started"),
        "task_type": str(program.get("task_type") or "-"),
        "primary_metric": str(program.get("primary_metric") or "-"),
        "path": str(program.get("path") or "-"),
        "amendment_state": "pending" if latest_amendment else "-",
        "latest_judge_verdict": str(latest_judge.get("verdict") or "-"),
        "latest_guard_decision": str(latest_guard.get("decision") or "-"),
    }


def _safe_event_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if "scratchpad" not in str(key).lower()}


def _is_user_noise_control_event(item: dict[str, Any]) -> bool:
    action = str(item.get("action") or "").strip().lower()
    message = str(item.get("message") or "").strip()
    if action in {"quit", "exit"}:
        return True
    return message in {"AutoBCI 已退出。", "AutoBCI 已退出"}


def build_system_event_items(snapshot: dict[str, object], *, limit: int = SYSTEM_EVENT_LIMIT) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    recent_messages = snapshot.get("recent_messages") if isinstance(snapshot.get("recent_messages"), list) else []
    for item in recent_messages:
        if not isinstance(item, dict):
            continue
        safe = _safe_event_payload(item)
        message_type = str(safe.get("message_type") or "message")
        title = message_type
        if message_type == "policy_decision":
            title = f"边界检查 · {safe.get('decision') or '-'}"
        elif message_type == "judge_report":
            title = f"结果复核 · {safe.get('verdict') or '-'}"
        elif message_type == "amendment_request":
            title = "Amendment Request"
        elif message_type == "program_handoff":
            title = "Program Handoff"
        detail = (
            safe.get("reason")
            or safe.get("recommended_next_action")
            or safe.get("program_snapshot_path")
            or safe.get("message")
            or "-"
        )
        events.append(
            {
                "created_at": str(safe.get("created_at") or safe.get("recorded_at") or ""),
                "time_label": _time_label(safe.get("created_at") or safe.get("recorded_at")),
                "message_type": message_type,
                "title": str(title),
                "detail": _trim_text(detail, 180),
                "source_role": str(safe.get("source_role") or "-"),
                "target_role": str(safe.get("target_role") or "-"),
                "status": str(safe.get("decision") or safe.get("verdict") or safe.get("result_status") or "-"),
            }
        )
    recent_control_events = snapshot.get("recent_control_events") if isinstance(snapshot.get("recent_control_events"), list) else []
    for item in recent_control_events:
        if not isinstance(item, dict):
            continue
        if _is_user_noise_control_event(item):
            continue
        events.append(
            {
                "created_at": str(item.get("recorded_at") or ""),
                "time_label": _time_label(item.get("recorded_at")),
                "message_type": "control_event",
                "title": str(item.get("action") or "control_event"),
                "detail": _trim_text(item.get("message") or "-", 180),
                "source_role": "control_plane",
                "target_role": "tui",
                "status": "ok" if bool(item.get("ok")) else "failed",
            }
        )
    research_loop = snapshot.get("research_loop") if isinstance(snapshot.get("research_loop"), dict) else {}
    recent_research_events = research_loop.get("recent_events") if isinstance(research_loop.get("recent_events"), list) else []
    for item in recent_research_events[-limit:]:
        if not isinstance(item, dict):
            continue
        display = item.get("display") if isinstance(item.get("display"), dict) else {}
        detail_parts = [str(display.get("summary") or item.get("reason") or item.get("action") or "-")]
        details = display.get("details") if isinstance(display.get("details"), list) else []
        detail_parts.extend(str(detail) for detail in details[:2] if str(detail or "").strip())
        events.append(
            {
                "created_at": str(item.get("created_at") or ""),
                "time_label": _time_label(item.get("created_at")),
                "message_type": "research_trace",
                "title": str(display.get("title") or f"{item.get('actor') or '-'} · {item.get('event_type') or '-'}"),
                "detail": _trim_text("；".join(detail_parts), 220),
                "source_role": str(item.get("actor") or "-"),
                "target_role": "research_loop",
                "status": str(item.get("action") or "-"),
            }
        )
    return events[:limit]


def infer_ui_phase(program: dict[str, Any], events: list[dict[str, Any]], run_status: str, *, boot_mode: bool = False) -> str:
    if boot_mode:
        return "cold_start"
    if run_status == "live":
        return "run_live"
    if any(str(item.get("message_type") or "") == "judge_report" for item in events):
        return "review_pending"
    if str(program.get("status") or "") == "frozen":
        return "frozen_idle"
    if str(program.get("status") or "") == "draft":
        return "drafting_program"
    return "cold_start"


def _latest_role_stage(snapshot: dict[str, object], role: str) -> str:
    autoresearch_status = snapshot.get("autoresearch_status") if isinstance(snapshot.get("autoresearch_status"), dict) else {}
    if role == "Executor" and not str(autoresearch_status.get("current_command") or "").strip():
        return "-"
    items = build_agent_work_items(snapshot, limit=8)
    latest = next((item for item in items if item.get("role") == role), None)
    if not latest:
        return "-"
    title = str(latest.get("title") or "").strip()
    if role == "Executor":
        result = str(latest.get("result") or "").strip()
        if result and result != "-":
            return result
    return _trim_text(title, 28)


def _guard_status(program: dict[str, Any]) -> str:
    decision = str(program.get("latest_guard_decision") or "").strip()
    if not decision or decision == "-":
        return "clear"
    return decision


def _judge_status(program: dict[str, Any]) -> str:
    verdict = str(program.get("latest_judge_verdict") or "").strip()
    if not verdict or verdict == "-":
        return "-"
    if "warning" in verdict:
        return "warning"
    return verdict


def _experiment_status_label(snapshot: dict[str, object]) -> str:
    experiment = snapshot.get("experiment_state") if isinstance(snapshot.get("experiment_state"), dict) else {}
    if not experiment:
        return "Project:-  Session:-"
    title = str(experiment.get("title") or experiment.get("experiment_id") or "-").strip()
    status = str(experiment.get("status") or "active").strip()
    session_id = str(experiment.get("session_id") or experiment.get("intake_session_id") or "-").strip()
    return f"Project:{title} · {status}  Session:{session_id}"


def _intake_status_label(inflight_turn: dict[str, Any] | None) -> str:
    if not isinstance(inflight_turn, dict):
        return "ready"
    status = str(inflight_turn.get("status") or "thinking").strip()
    if status == "tool_calling":
        return "tool"
    if status == "failed":
        return "failed"
    return "thinking"


def _intake_model_label() -> str:
    status = _resolve_agent_model_status("intake")
    provider = str(status.get("provider") or "-").strip()
    model = str(status.get("model") or "-").strip()
    if not provider or provider == "-":
        return "-"
    return f"{provider}/{model or '-'}"


def _program_phase_label(program_status: str, pending_action: dict[str, Any] | None, run_status: str) -> str:
    if run_status == "live":
        return "运行中"
    if _is_active_program_plan(pending_action):
        return "计划中"
    if _pending_program_draft(pending_action):
        return "待冻结"
    if program_status in {"frozen", "amended"}:
        return "已冻结"
    if program_status == "draft":
        return "草案"
    return "计划中"


def build_status_rule_model(
    snapshot: dict[str, object],
    program: dict[str, Any],
    run_status: str,
    *,
    inflight_turn: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> str:
    program_status = str(program.get("status") or "not_started")
    if _is_active_program_plan(pending_action):
        program_status = "planning"
    experiment = snapshot.get("experiment_state") if isinstance(snapshot.get("experiment_state"), dict) else {}
    active_run_id = str(experiment.get("active_run_id") or "").strip()
    displayed_run_status = active_run_id or run_status
    return (
        f"AutoBCI  Phase:{_program_phase_label(program_status, pending_action, run_status)}  "
        f"Program:{program_status}  Run:{displayed_run_status}  "
        f"{_experiment_status_label(snapshot)}  "
        f"模型:{_intake_status_label(inflight_turn)} · {_intake_model_label()}"
    )


def _pending_program_draft(pending_action: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(pending_action, dict) or pending_action.get("user_intent_kind") != "draft_program":
        return None
    draft = pending_action.get("program_draft")
    return draft if isinstance(draft, dict) else None


def _is_program_plan_action(pending_action: dict[str, Any] | None) -> bool:
    if not isinstance(pending_action, dict):
        return False
    return bool(pending_action.get("plan_mode")) or str(pending_action.get("plan_status") or "") in {
        "drafting",
        "paused",
        "accepted",
    }


def _is_active_program_plan(pending_action: dict[str, Any] | None) -> bool:
    return _is_program_plan_action(pending_action) and bool(pending_action.get("plan_mode"))


def _new_program_plan_action() -> dict[str, Any]:
    return {
        "recognized": True,
        "user_intent_kind": "draft_program",
        "normalized_request": "",
        "target_scope": "Program",
        "proposed_action": "program_plan",
        "command_preview": "生成 Program -> 确认并开始运行",
        "requires_confirmation": False,
        "result_status": "planning",
        "summary": "进入 Program 起草。",
        "boundary_note": "Program 只整理研究任务和边界；确认前不冻结、不启动执行沙盒、不读取外部数据。",
        "plan_mode": True,
        "plan_status": "drafting",
        "revision": 0,
        "open_questions": ["请描述研究目标、可用数据、标签来源和成功指标。"],
        "plan_summary": "尚未形成 Program 草案。",
        "discussion_notes": [],
    }


def _format_program_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "-"
    return str(value or "-")


def _format_program_for_show(program: dict[str, Any], *, heading: str, path_hint: str = "") -> str:
    goal = program.get("research_goal") if isinstance(program.get("research_goal"), dict) else {}
    data_boundary = program.get("data_boundary") if isinstance(program.get("data_boundary"), dict) else {}
    label = program.get("label_definition") if isinstance(program.get("label_definition"), dict) else {}
    split = program.get("split_policy") if isinstance(program.get("split_policy"), dict) else {}
    metrics = program.get("metrics") if isinstance(program.get("metrics"), dict) else {}
    search = program.get("search_space") if isinstance(program.get("search_space"), dict) else {}
    artifact = program.get("artifact_contract") if isinstance(program.get("artifact_contract"), dict) else {}
    allowed_sessions = (
        data_boundary.get("allowed_sessions")
        if isinstance(data_boundary.get("allowed_sessions"), dict)
        else {}
    )
    lines = [
        heading,
        f"- program_id: {program.get('program_id') or '-'}",
        f"- version: {program.get('version') or '-'}",
        f"- status: {program.get('status') or '-'}",
        f"- task_type: {goal.get('task_type') or program.get('task_type') or '-'}",
        f"- scientific_question: {goal.get('scientific_question') or '-'}",
        f"- statement: {goal.get('statement') or '-'}",
        f"- primary_metric: {metrics.get('primary') or program.get('primary_metric') or '-'}",
        f"- secondary_metrics: {_format_program_list(metrics.get('secondary'))}",
        f"- dataset_name: {data_boundary.get('dataset_name') or '-'}",
        f"- dataset_root: {data_boundary.get('dataset_root') or '-'}",
        f"- raw_data_policy: {data_boundary.get('raw_data_policy') or '-'}",
        f"- forbidden_data_access: {_format_program_list(data_boundary.get('forbidden_data_access'))}",
        f"- label_source: {label.get('source') or '-'}",
        f"- label_risks: {_format_program_list(label.get('known_risks'))}",
        f"- acceptance_note: {label.get('acceptance_note') or '-'}",
        f"- split_unit: {split.get('unit') or '-'}",
        f"- train: {_format_program_list(split.get('frozen_train_sessions') or allowed_sessions.get('train'))}",
        f"- val: {_format_program_list(split.get('frozen_val_sessions') or allowed_sessions.get('val'))}",
        f"- test: {_format_program_list(split.get('frozen_test_sessions') or allowed_sessions.get('test'))}",
        f"- windows_seconds: {_format_program_list(search.get('windows_seconds'))}",
        f"- lags_ms: {_format_program_list(search.get('lags_ms'))}",
        f"- allowed_model_families: {_format_program_list(search.get('allowed_model_families'))}",
        f"- allowed_feature_families: {_format_program_list(search.get('allowed_feature_families'))}",
        f"- forbidden_actions: {_format_program_list(program.get('forbidden_actions'))}",
        f"- required_outputs: {_format_program_list(artifact.get('required_outputs'))}",
    ]
    uncertainties = program.get("uncertainties")
    if isinstance(uncertainties, list) and uncertainties:
        lines.append(f"- uncertainties: {_format_program_list(uncertainties)}")
    if path_hint:
        lines.append(f"- path: {path_hint}")
    return "\n".join(lines)


def _program_plan_open_questions(draft: dict[str, Any] | None) -> list[str]:
    if not isinstance(draft, dict):
        return ["请描述研究目标、可用数据、标签来源和成功指标。"]
    questions = [
        "确认数据目录和标签文件是否就是这批任务的唯一输入。",
        "确认数据划分是否允许按当前冻结 train / val / test 清单执行。",
        "确认是否只生成 Program，不启动执行沙盒或正式 AutoResearch。",
    ]
    return questions


def _program_plan_summary(draft: dict[str, Any] | None) -> str:
    if not isinstance(draft, dict):
        return "尚未形成 Program 草案。"
    goal = draft.get("research_goal") if isinstance(draft.get("research_goal"), dict) else {}
    metrics = draft.get("metrics") if isinstance(draft.get("metrics"), dict) else {}
    statement = str(goal.get("statement") or draft.get("program_id") or "Program 草案")
    task_type = str(goal.get("task_type") or draft.get("task_type") or "-")
    primary = str(metrics.get("primary") or draft.get("primary_metric") or "-")
    return f"{statement} · {task_type} · primary={primary}"


def _program_discussion_notes(plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(plan, dict):
        return []
    notes = plan.get("discussion_notes")
    if not isinstance(notes, list):
        return []
    return [str(item).strip() for item in notes if str(item).strip()]


def _program_discussion_summary(notes: list[str]) -> str:
    if not notes:
        return "正在讨论，还没有生成 Program 草案。"
    latest = notes[-1]
    compact = " ".join(latest.split())
    if len(compact) > 90:
        compact = compact[:87].rstrip() + "..."
    return f"正在讨论：{compact}"


def _record_program_discussion(command_text: str, session_state: dict[str, Any]) -> dict[str, Any]:
    existing = session_state.get("pending_action") if isinstance(session_state.get("pending_action"), dict) else None
    plan = dict(existing or _new_program_plan_action())
    notes = _program_discussion_notes(plan)
    current = str(command_text or "").strip()
    if current:
        notes.append(current)
    notes = notes[-20:]
    plan.update(
        {
            "recognized": True,
            "user_intent_kind": "draft_program",
            "normalized_request": "\n".join(notes),
            "target_scope": "Program",
            "proposed_action": "program_discussion",
            "command_preview": "继续讨论 -> 生成 Program",
            "requires_confirmation": False,
            "result_status": "planning",
            "summary": "记录 Program 讨论，不生成草案。",
            "plan_mode": True,
            "plan_status": "drafting",
            "discussion_notes": notes,
            "plan_summary": _program_discussion_summary(notes),
            "open_questions": [
                "继续讨论任务目标、数据、标签、指标和边界。",
                "确认方向后，说“现在生成 Program”再生成草案。",
            ],
        }
    )
    session_state["pending_action"] = plan
    return plan


def _format_program_discussion_message(plan: dict[str, Any]) -> str:
    notes = _program_discussion_notes(plan)
    lines = [
        "已记录为计划讨论，还没有生成 Program 草案。",
        f"- discussion_notes: {len(notes)}",
        f"- summary: {plan.get('plan_summary') or '-'}",
        "",
        "继续直接聊；等你拍板时说“现在生成 Program”或“按当前版本重写 Program”，我再生成草案。",
    ]
    return "\n".join(lines)


def _draft_program_for_plan(
    text: str,
    snapshot: dict[str, Any],
    *,
    repo_root: Path,
    use_model_agent: bool,
) -> dict[str, Any] | None:
    if use_model_agent and not is_tui_test_mode_enabled():
        intent = run_intake_agent_turn("program " + text, snapshot, repo_root=repo_root, use_model_agent=True)
        if str(intent.get("result_status") or "") == "failed":
            return {"__error_intent": intent}
        if intent.get("user_intent_kind") != "draft_program":
            return {"__passthrough_intent": intent}
    else:
        intent = classify_user_turn("program " + text, snapshot)
    draft = intent.get("program_draft") if isinstance(intent, dict) else None
    if isinstance(draft, dict):
        return apply_dataset_to_program_draft(repo_root, draft)
    return None


def _apply_local_data_config_to_intent(intent: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    draft = intent.get("program_draft")
    if isinstance(draft, dict):
        patched = dict(intent)
        patched["program_draft"] = apply_dataset_to_program_draft(repo_root, draft)
        return patched
    return intent


def _advance_program_plan(
    command_text: str,
    snapshot: dict[str, Any],
    *,
    repo_root: Path,
    session_state: dict[str, Any],
    use_model_agent: bool,
) -> dict[str, Any]:
    existing = session_state.get("pending_action") if isinstance(session_state.get("pending_action"), dict) else None
    existing_draft = _pending_program_draft(existing)
    previous = str(existing.get("normalized_request") or "").strip() if isinstance(existing, dict) else ""
    current = re.sub(
        r"(?i)\b(program|programmd|program md|program markdown)\b",
        " ",
        str(command_text or "").strip(),
    )
    current = re.sub(r"(现在|请|帮我|生成|写|重写|形成|输出|创建|落成|按现在聊的版本|按当前版本|可以了|差不多明白了)", " ", current)
    current = " ".join(current.split())
    combined = "\n".join(item for item in (previous, current) if item)
    draft = _draft_program_for_plan(combined or current, snapshot, repo_root=repo_root, use_model_agent=use_model_agent)
    if isinstance(draft, dict) and isinstance(draft.get("__error_intent"), dict):
        return cast(dict[str, Any], draft["__error_intent"])
    if isinstance(draft, dict) and isinstance(draft.get("__passthrough_intent"), dict):
        return cast(dict[str, Any], draft["__passthrough_intent"])
    if not isinstance(draft, dict):
        draft = existing_draft
    revision = int(existing.get("revision") or 0) + 1 if isinstance(existing, dict) else 1
    plan = dict(existing or _new_program_plan_action())
    plan.update(
        {
            "recognized": True,
            "user_intent_kind": "draft_program",
            "normalized_request": combined or current,
            "target_scope": "Program",
            "proposed_action": "program_plan",
            "command_preview": "确认并开始运行",
            "requires_confirmation": False,
            "result_status": "planning",
            "summary": "更新 Program。",
            "plan_mode": True,
            "plan_status": "drafting",
            "revision": revision,
            "open_questions": _program_plan_open_questions(draft),
            "plan_summary": _program_plan_summary(draft),
        }
    )
    if isinstance(draft, dict):
        plan["program_draft"] = draft
    return plan


def _format_program_plan_message(plan: dict[str, Any], *, opening: bool = False) -> str:
    draft = _pending_program_draft(plan)
    lines = [
        "Program 起草已开启。" if opening else "Program 已更新。",
        f"- revision: {int(plan.get('revision') or 0)}",
        f"- status: {plan.get('plan_status') or 'drafting'}",
        f"- summary: {plan.get('plan_summary') or '-'}",
    ]
    if isinstance(draft, dict):
        program_id = str(draft.get("program_id") or "").strip()
        lines.extend(
            [
                "",
                _format_program_for_show(
                    draft,
                    heading="Program：",
                    path_hint=f"确认运行后写入 programs/{program_id or '<program_id>'}/Program.md",
                ),
            ]
        )
        questions = plan.get("open_questions") if isinstance(plan.get("open_questions"), list) else []
        if questions:
            lines.extend(["", "待你确认或修改："])
            lines.extend(f"- {item}" for item in questions[:3])
        lines.extend(["", "下一步：用下方菜单选择“确认并开始运行”“补充修改意见”或“暂不执行”。"])
        goal = draft.get("research_goal") if isinstance(draft.get("research_goal"), dict) else {}
        metrics = draft.get("metrics") if isinstance(draft.get("metrics"), dict) else {}
        task_type = str(goal.get("task_type") or draft.get("task_type") or "-")
        primary_metric = str(metrics.get("primary") or draft.get("primary_metric") or "-")
        lines.extend(
            [
                "",
                "研究计划 / Program：已生成",
                "禁止动作：不改任务类型/主指标/下载数据；不读取脑电；不声称脑电比较。",
                f"Program：{program_id or '-'} · {task_type} · primary={primary_metric}",
            ]
        )
    else:
        questions = plan.get("open_questions") if isinstance(plan.get("open_questions"), list) else []
        if questions:
            lines.append("- 还需要你补充：" + "；".join(str(item) for item in questions[:3]))
        lines.append("继续直接聊；等你说“现在生成 Program”或“按当前版本重写 Program”，我再生成。")
    return "\n".join(lines)


def _format_program_plan_show(plan: dict[str, Any], paths: Any) -> str:
    draft = _pending_program_draft(plan)
    lines = [
        "当前 Program：",
        f"- revision: {int(plan.get('revision') or 0)}",
        f"- plan_status: {plan.get('plan_status') or '-'}",
        f"- plan_mode: {bool(plan.get('plan_mode'))}",
        f"- plan_summary: {plan.get('plan_summary') or '-'}",
    ]
    questions = plan.get("open_questions") if isinstance(plan.get("open_questions"), list) else []
    lines.append("- open_questions: " + ("；".join(str(item) for item in questions) if questions else "-"))
    if isinstance(draft, dict):
        program_id = str(draft.get("program_id") or "").strip()
        approve_path = str(paths.programs_dir / program_id / "Program.md") if program_id else ""
        lines.extend(
            [
                "",
                _format_program_for_show(
                    draft,
                    heading="Program：",
                    path_hint=f"{approve_path}（/approve 后写入）" if approve_path else "/approve 后写入",
                ),
            ]
        )
    else:
        lines.append("- program_draft: 尚未形成")
    return "\n".join(lines)


def _plan_command_payload() -> dict[str, Any]:
    return {
        "user_intent_kind": "draft_program",
        "normalized_request": "plan",
        "target_scope": "Program",
        "proposed_action": "program_plan",
        "command_preview": "/plan",
        "requires_confirmation": False,
    }


PROGRAM_PLAN_INPUT_HINTS = (
    "programmd",
    "任务",
    "研究目标",
    "数据",
    "标签",
    "指标",
    "脑电",
    "eeg",
    "二分类",
    "分类",
    "步态",
    "只用",
    "不用",
)


def _looks_like_program_discussion_input(command: str) -> bool:
    text = normalize_request(command)
    if not text or text.startswith("/"):
        return False
    lowered = text.lower()
    if lowered in {"hi", "hello", "hey"} or text in {"你好", "您好", "哈喽"}:
        return False
    if lowered.startswith(("run ", "propose ", "amend ", "status ", "help ")):
        return False
    if any(hint in text or hint in lowered for hint in ("smoke", "track", "feature_", "路线", "候选")) and not any(
        hint in text or hint in lowered for hint in ("programmd", "任务", "研究目标", "从零", "脑电", "bci")
    ):
        return False
    return any(hint in text or hint in lowered for hint in PROGRAM_PLAN_INPUT_HINTS)


def _looks_like_program_draft_request(command: str) -> bool:
    text = normalize_request(command)
    if not text or text.startswith("/"):
        return False
    lowered = text.lower()
    negations = (
        "不要生成",
        "不要写",
        "别生成",
        "别写",
        "先不生成",
        "先别生成",
        "还没生成",
        "还没有生成",
        "还没让",
        "还没有让",
        "没有让",
        "不生成 program",
        "不写 program",
        "don't generate",
        "do not generate",
    )
    if any(token in text or token in lowered for token in negations):
        return False
    has_program_word = any(
        token in text or token in lowered
        for token in (
            "programmd",
            "program md",
            "program markdown",
            "program 文件",
            "program 草案",
            "program",
            "任务契约",
            "研究契约",
        )
    )
    has_write_action = any(
        token in text or token in lowered
        for token in (
            "生成",
            "写",
            "重写",
            "形成",
            "输出",
            "创建",
            "落成",
            "按现在",
            "按当前",
            "可以了",
            "差不多明白",
            "generate",
            "write",
            "rewrite",
            "create",
        )
    )
    if lowered.startswith("program "):
        return True
    return has_program_word and has_write_action


def _looks_like_active_program_context_input(command: str) -> bool:
    text = normalize_request(command)
    if not text or text.startswith("/"):
        return False
    lowered = text.lower()
    if lowered in {"hi", "hello", "hey"} or text in {"你好", "您好", "哈喽"}:
        return False
    if lowered.startswith(("run ", "status ", "help ", "quit ", "exit ", "report ", "dashboard ")):
        return False
    return True


def _has_secondary_selection_context(session_state: dict[str, Any]) -> bool:
    context = session_state.get("selection_context")
    if not isinstance(context, dict):
        return False
    kind = str(context.get("kind") or "")
    return bool(kind)


def _program_revision_text(command: str, session_state: dict[str, Any]) -> str | None:
    context = session_state.get("selection_context")
    if not isinstance(context, dict) or context.get("kind") != "program_revision":
        return None
    stripped = str(command or "").strip()
    if stripped.startswith("/"):
        session_state.pop("selection_context", None)
        return None
    if stripped.lower() in {"cancel", "取消", "返回", "退出", "no", "n"}:
        session_state.pop("selection_context", None)
        return "__cancel__"
    return stripped


def _snapshot_program_status(snapshot: dict[str, object]) -> tuple[str, str]:
    experiment = snapshot.get("experiment_state") if isinstance(snapshot.get("experiment_state"), dict) else {}
    program_state = snapshot.get("program_state") if isinstance(snapshot.get("program_state"), dict) else {}
    program_id = str(experiment.get("program_id") or program_state.get("program_id") or "").strip()
    status = str(experiment.get("program_status") or program_state.get("status") or "not_started").strip()
    return program_id, status or "not_started"


def _should_create_default_program_plan(snapshot: dict[str, object], session_state: dict[str, Any]) -> bool:
    if isinstance(session_state.get("pending_action"), dict):
        return False
    program_id, status = _snapshot_program_status(snapshot)
    if program_id:
        return False
    return status in {"", "-", "not_started"}


def _ensure_default_program_plan(
    paths: Any,
    session_state: dict[str, Any],
    *,
    snapshot: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    current_snapshot = snapshot if isinstance(snapshot, dict) else _attach_experiment_state(build_status_snapshot(paths), paths, session_state)
    if not _should_create_default_program_plan(current_snapshot, session_state):
        return None
    plan = _new_program_plan_action()
    session_state["pending_action"] = plan
    _sync_experiment_manifest(paths, session_state, snapshot=current_snapshot)
    return plan


def _next_action_items(snapshot: dict[str, object], pending_action: dict[str, Any] | None) -> list[dict[str, str]]:
    if _is_active_program_plan(pending_action):
        if not _pending_program_draft(pending_action):
            return []
        return [
            {
                "label": "确认并开始运行",
                "command": "/plan run",
                "description": "冻结 Program，并打开第一轮研究确认",
            },
            {
                "label": "补充修改意见",
                "command": "/plan revise",
                "description": "下一条输入会作为修改意见重写 Program",
            },
            {
                "label": "暂不执行",
                "command": "/plan exit",
                "description": "保留 Program，稍后继续",
            },
        ]
    pending_draft = _pending_program_draft(pending_action)
    if pending_draft and not _is_active_program_plan(pending_action):
        return [
            {"label": "冻结 Program", "command": "/approve"},
            {"label": "查看草案", "command": "/program show"},
            {"label": "取消", "command": "/cancel"},
        ]
    program_id, status = _snapshot_program_status(snapshot)
    autoresearch_status = snapshot.get("autoresearch_status") if isinstance(snapshot.get("autoresearch_status"), dict) else {}
    run_live = bool(str(autoresearch_status.get("current_command") or "").strip())
    if program_id and status in {"frozen", "amended"} and not run_live:
        return []
    return []


def _refresh_next_actions(
    session_state: dict[str, Any],
    snapshot: dict[str, object],
) -> list[dict[str, str]]:
    pending = session_state.get("pending_action") if isinstance(session_state.get("pending_action"), dict) else None
    actions = _next_action_items(snapshot, pending)
    if actions:
        session_state["next_actions"] = actions
    else:
        session_state.pop("next_actions", None)
    return actions


def _next_action_selection_number(command: str, session_state: dict[str, Any]) -> int | None:
    if _has_secondary_selection_context(session_state):
        return None
    stripped = command.strip()
    if not stripped.isdigit():
        return None
    actions = session_state.get("next_actions")
    if not isinstance(actions, list) or not actions:
        return None
    return int(stripped)


def _resolve_next_action_command(session_state: dict[str, Any], index: int) -> str | None:
    actions = session_state.get("next_actions")
    if not isinstance(actions, list) or index < 1 or index > len(actions):
        return None
    selected = actions[index - 1]
    if not isinstance(selected, dict):
        return None
    command = str(selected.get("command") or "").strip()
    return command or None


def should_show_program_card(
    program: dict[str, Any],
    session_history: list[dict[str, Any]] | None,
    *,
    pending_action: dict[str, Any] | None = None,
) -> bool:
    if _pending_program_draft(pending_action):
        return True
    if str(program.get("status") or "not_started") != "not_started":
        return True
    for item in session_history or []:
        if str(item.get("intent_kind") or "") in {"draft_program", "freeze_program", "draft_amendment"}:
            return True
    return False


def build_program_card_model(
    program: dict[str, Any],
    *,
    draft_program: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
    next_actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    source = draft_program if isinstance(draft_program, dict) else program
    goal = source.get("research_goal") if isinstance(source.get("research_goal"), dict) else {}
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    status = str(source.get("status") or program.get("status") or "not_started")
    plan_mode = _is_program_plan_action(pending_action) and str(pending_action.get("plan_status") or "") != "accepted"
    title = "研究计划 / Program"
    missing: list[str] = []
    rows = [
        ("当前判断", str(pending_action.get("plan_summary") or _program_plan_summary(source) if plan_mode else _program_plan_summary(source))),
        ("研究目标", str(source.get("program_id") or "待确认")),
        ("任务类型", str(goal.get("task_type") or source.get("task_type") or "待确认")),
        ("主指标", str(metrics.get("primary") or source.get("primary_metric") or "待确认")),
        ("状态", status),
    ]
    if plan_mode:
        rows.append(("计划版本", str(pending_action.get("revision") or 0)))
        if isinstance(draft_program, dict):
            data_boundary = source.get("data_boundary") if isinstance(source.get("data_boundary"), dict) else {}
            label = source.get("label_definition") if isinstance(source.get("label_definition"), dict) else {}
            split = source.get("split_policy") if isinstance(source.get("split_policy"), dict) else {}
            search = source.get("search_space") if isinstance(source.get("search_space"), dict) else {}
            artifact = source.get("artifact_contract") if isinstance(source.get("artifact_contract"), dict) else {}
            rows.extend(
                [
                    ("数据", str(data_boundary.get("dataset_name") or "待确认")),
                    ("标签", str(label.get("source") or "待确认")),
                    (
                        "数据划分",
                        " / ".join(
                            item
                            for item in (
                                _format_program_list(split.get("frozen_train_sessions")),
                                _format_program_list(split.get("frozen_val_sessions")),
                                _format_program_list(split.get("frozen_test_sessions")),
                            )
                            if item and item != "-"
                        )
                        or "待确认",
                    ),
                    ("允许方向", _format_program_list(search.get("allowed_model_families"))),
                    ("禁止动作", _format_program_list(source.get("forbidden_actions"))),
                    ("输出", _format_program_list(artifact.get("required_outputs"))),
                ]
            )
    if not source.get("program_id") or source.get("program_id") == "-":
        missing.append("研究目标")
    if not goal.get("task_type") and (not source.get("task_type") or source.get("task_type") == "-"):
        missing.append("任务类型")
    if not metrics.get("primary") and (not source.get("primary_metric") or source.get("primary_metric") == "-"):
        missing.append("成功指标")
    if plan_mode:
        questions = pending_action.get("open_questions") if isinstance(pending_action.get("open_questions"), list) else []
        rows.append(("开放问题", "；".join(str(item) for item in questions[:2]) if questions else "-"))
        next_step = str(questions[0]) if questions else "继续补充需求，确认后选择“确认并开始运行”"
        notes = _program_discussion_notes(pending_action)
        if notes and not _pending_program_draft(pending_action):
            rows.append(("讨论记录", f"{len(notes)} 条"))
    elif status in {"frozen", "amended"}:
        next_step = "输入 /run step 开始 Owner Debug Mode；高风险动作会显示 Yes / No / Details。"
    else:
        next_step = "确认数据包、数据划分、成功指标" if missing else "可以 freeze / approve 或申请 amendment"
    actions = list(next_actions or [])
    if actions:
        next_step = "；".join(f"{index}. {item.get('label') or '-'}" for index, item in enumerate(actions, start=1))
    return {
        "title": title,
        "status": status,
        "rows": rows,
        "missing": missing,
        "next_step": next_step,
        "next_actions": actions,
    }


def build_system_trail_model(
    events: list[dict[str, Any]],
    *,
    legacy_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expanded: list[str] = []
    guard_denies = 0
    judge_warning = False
    for item in events:
        message_type = str(item.get("message_type") or "event")
        title = str(item.get("title") or message_type)
        status = str(item.get("status") or "-")
        detail = str(item.get("detail") or "-")
        if message_type == "policy_decision" and status == "deny":
            guard_denies += 1
        if message_type == "judge_report" and "warning" in status:
            judge_warning = True
        expanded.append(f"┊ {item.get('time_label') or '--:--'} {title} / {message_type} [{status}] {detail}")
    for item in legacy_items or []:
        role = str(item.get("role") or "system")
        expanded.append(
            f"┊ {item.get('time_label') or '--:--'} {role}: "
            f"{item.get('title') or '-'} · {item.get('detail') or '-'}"
        )
    event_count = len(events) + len(legacy_items or [])
    if not event_count:
        collapsed = "┊ run trail: no events"
    else:
        parts = [f"┊ run trail: {event_count} events"]
        if guard_denies:
            parts.append(f"{guard_denies} guard deny")
        if judge_warning:
            parts.append("judge warning")
        collapsed = " · ".join(parts)
    return {
        "collapsed": collapsed,
        "expanded": expanded,
        "event_count": event_count,
        "guard_denies": guard_denies,
        "judge_warning": judge_warning,
    }


def build_inflight_turn(
    command_text: str,
    *,
    turn_id: str | None = None,
    status: str = "thinking",
    status_text: str = "",
) -> dict[str, Any]:
    return {
        "turn_id": turn_id or f"inflight-{uuid.uuid4().hex[:12]}",
        "created_at": _utc_now_label(),
        "started_at": time.monotonic(),
        "role": "inflight",
        "text": str(command_text or "").strip(),
        "status": status,
        "status_text": str(status_text or "").strip(),
        "visibility": "intake_only",
    }


def format_intake_activity_label(inflight_turn: dict[str, Any] | None, ui_tick: int = 0) -> str:
    if not isinstance(inflight_turn, dict):
        return ""
    explicit = str(inflight_turn.get("status_text") or "").strip()
    if explicit:
        return explicit
    status = str(inflight_turn.get("status") or "thinking").strip()
    if status == "tool_calling":
        base = "正在调用工具"
    elif status == "failed":
        return "这一轮处理失败"
    else:
        base = "正在整理"
    dots = ("·  ", "·· ", "···")[int(ui_tick or 0) % 3]
    return f"{base}{dots}"


def append_inflight_rows(
    rows: list[dict[str, Any]],
    inflight_turn: dict[str, Any] | None,
    *,
    ui_tick: int = 0,
) -> list[dict[str, Any]]:
    if not isinstance(inflight_turn, dict):
        return rows
    text = str(inflight_turn.get("text") or "").strip()
    if not text:
        return rows

    def _normalized(value: object) -> str:
        return " ".join(str(value or "").strip().split())

    normalized_text = _normalized(text)
    user_already_visible = any(
        str(row.get("role") or "") == "user" and _normalized(row.get("text")) == normalized_text
        for row in rows[-6:]
    )
    if not user_already_visible:
        rows.append(
            {
                "turn_id": str(inflight_turn.get("turn_id") or ""),
                "time_label": _time_label(inflight_turn.get("created_at")),
                "role": "user",
                "text": text,
                "intent_kind": "inflight_user",
                "visibility": "intake_only",
            }
        )
    rows.append(
        {
            "turn_id": str(inflight_turn.get("turn_id") or ""),
            "time_label": "--:--",
            "role": "intake",
            "text": format_intake_activity_label(inflight_turn, ui_tick),
            "intent_kind": "inflight",
            "visibility": "intake_only",
        }
    )
    return rows


def build_transcript_rows(
    session_history: list[dict[str, Any]] | None,
    *,
    output_history: list[object] | None = None,
    program_card: dict[str, Any] | None = None,
    inflight_turn: dict[str, Any] | None = None,
    ui_tick: int = 0,
) -> list[dict[str, Any]]:
    rows = build_timeline_items(session_history, [])
    latest_persisted_text = str(rows[-1].get("text") or "").strip() if rows else ""
    persisted_texts = {
        _normalize_transcript_for_compare(item.get("text"))
        for item in rows
        if str(item.get("text") or "").strip()
    }

    def _same_as_latest_persisted(line: object) -> bool:
        if not latest_persisted_text:
            return False
        normalized_line = _normalize_transcript_for_compare(line)
        normalized_latest = _normalize_transcript_for_compare(latest_persisted_text)
        return normalized_line == normalized_latest

    def _same_as_any_persisted(line: object) -> bool:
        normalized_line = _normalize_transcript_for_compare(line)
        return bool(normalized_line and normalized_line in persisted_texts)

    if any(str(row.get("created_at") or "").strip() for row in rows):
        rows.sort(
            key=lambda row: (
                _parse_timestamp(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                _turn_sort_key(row.get("turn_id")) if _turn_sort_key(row.get("turn_id")) is not None else 10**12,
                int(row.get("sort_index") or 0),
            )
        )
    if program_card is not None:
        rows.append(
            {
                "time_label": "--:--",
                "role": "card",
                "text": str(program_card.get("title") or "Program"),
                "intent_kind": "program_card",
                "visibility": "public_event",
                "card": program_card,
            }
        )

    for index, entry in enumerate((output_history or [])[-TUI_STREAM_OUTPUT_VISIBLE_LIMIT:]):
        text = _output_history_text(entry)
        if (
            not text.strip()
            or text.strip() in {"输入 help 查看命令。", "已接入当前研究态。输入 help 查看命令。"}
            or _same_as_latest_persisted(text)
            or _same_as_any_persisted(text)
        ):
            continue
        created_at = _output_history_created_at(entry)
        rows.append(
            {
                "created_at": created_at,
                "time_label": _time_label(created_at) if created_at else "--:--",
                "role": "tool" if _is_tool_output_text(text) else "intake",
                "text": text,
                "turn_id": "",
                "intent_kind": "ephemeral",
                "visibility": "intake_only",
                "sort_index": len(rows) + index,
            }
        )
    if any(str(row.get("created_at") or "").strip() for row in rows):
        rows.sort(
            key=lambda row: (
                _parse_timestamp(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                _turn_sort_key(row.get("turn_id")) if _turn_sort_key(row.get("turn_id")) is not None else 10**12,
                int(row.get("sort_index") or 0),
            )
        )
    rows = append_inflight_rows(rows, inflight_turn, ui_tick=ui_tick)
    return rows


def _is_tool_output_text(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    prefixes = (
        "🧭 AutoBci 内置控制面",
        "模型设置",
        "模型与 Provider 状态",
        "首次配置",
        "研究方向调度",
        "研究方向队列",
        "研究证据包",
        "研究事件流",
        "Continue?",
        "Allow structure sandbox edit?",
        "Keep this candidate?",
        "Promote this result?",
        "Continue after risk review?",
        "已暂停当前研究步骤",
        "已记录复核选择",
        "当前 Program",
        "当前 Program",
        "已生成研究方向",
        "研究闭环已推进",
        "已冻结 Program",
        "Program 已确认",
        "已全新开始",
        "已开始新的实验工作区",
        "已归档",
        "已恢复",
        "已切换",
        "选择要",
        "实验工作区列表",
    )
    return stripped.startswith(prefixes)


def _visible_transcript_tail(rows: list[dict[str, Any]], *, max_items: int = 4) -> list[dict[str, Any]]:
    if len(rows) <= max_items:
        return rows
    return rows[-max_items:]


def build_intake_chat_view_model(
    snapshot: dict[str, object],
    *,
    session_history: list[dict[str, Any]] | None = None,
    output_history: list[str] | None = None,
    pending_action: dict[str, Any] | None = None,
    inflight_turn: dict[str, Any] | None = None,
    ui_tick: int = 0,
    boot_mode: bool = False,
) -> dict[str, Any]:
    program = build_program_panel_model(snapshot)
    events = build_system_event_items(snapshot)
    autoresearch_status = snapshot.get("autoresearch_status") if isinstance(snapshot.get("autoresearch_status"), dict) else {}
    run_status = "syncing" if boot_mode else ("live" if str(autoresearch_status.get("current_command") or "").strip() else "idle")
    dashboard_url = str(snapshot.get("dashboard_url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/")
    ui_phase = infer_ui_phase(program, events, run_status, boot_mode=boot_mode)
    status_rule = build_status_rule_model(
        snapshot,
        program,
        run_status,
        inflight_turn=inflight_turn,
        pending_action=pending_action,
    )
    draft_program = _pending_program_draft(pending_action)
    next_actions = _next_action_items(snapshot, pending_action)
    program_card = (
        build_program_card_model(program, draft_program=draft_program, pending_action=pending_action, next_actions=next_actions)
        if should_show_program_card(program, session_history, pending_action=pending_action)
        else None
    )
    system_trail = build_system_trail_model(events, legacy_items=[])
    system_trail["show_default"] = ui_phase in {"run_live", "review_pending", "frozen_idle"}
    header_text = status_rule
    banner = _format_framework_benchmark_banner(snapshot)
    return {
        "header_text": header_text,
        "status_rule": status_rule,
        "benchmark_banner": banner,
        "ui_phase": ui_phase,
        "program": program,
        "program_card": program_card,
        "conversation_items": build_timeline_items(session_history, []),
        "transcript_rows": build_transcript_rows(
            session_history,
            output_history=output_history,
            program_card=program_card,
            inflight_turn=inflight_turn,
            ui_tick=ui_tick,
        ),
        "system_event_items": events,
        "system_trail": system_trail,
        "output_history": (output_history or [])[-TUI_OUTPUT_HISTORY_LIMIT:],
        "run_status": run_status,
        "dashboard_url": dashboard_url,
        "boot_mode": boot_mode,
        "commands": list(SLASH_MENU_COMMANDS),
        "next_actions": next_actions,
    }


def build_intake_workspace_view_model(
    snapshot: dict[str, object],
    *,
    session_history: list[dict[str, Any]] | None = None,
    output_history: list[str] | None = None,
    inflight_turn: dict[str, Any] | None = None,
    ui_tick: int = 0,
    boot_mode: bool = False,
) -> dict[str, Any]:
    return build_intake_chat_view_model(
        snapshot,
        session_history=session_history,
        output_history=output_history,
        inflight_turn=inflight_turn,
        ui_tick=ui_tick,
        boot_mode=boot_mode,
    )


def build_shell_view_model(
    snapshot: dict[str, object],
    *,
    boot_mode: bool,
    output_history: list[str],
    ui_tick: int,
    director_history: list[dict[str, Any]] | None = None,
    executor_history: list[dict[str, Any]] | None = None,
    reveal_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    model = build_intake_chat_view_model(
        snapshot,
        boot_mode=boot_mode,
        session_history=[],
        output_history=output_history,
    )
    legacy_items = [
        item
        for item in list(director_history or []) + list(executor_history or [])
        if isinstance(item, dict)
    ]
    if legacy_items:
        model["system_trail"] = build_system_trail_model(model["system_event_items"], legacy_items=legacy_items)
    return model


def _compute_reveal_state(
    snapshot: dict[str, object],
    *,
    boot_mode: bool,
    output_history: list[str],
    ui_tick: int,
    director_history: list[dict[str, Any]] | None,
    executor_history: list[dict[str, Any]] | None,
    previous_counts: dict[str, int] | None,
    previous_signatures: dict[str, str] | None,
    previous_active_role: str | None,
    last_reveal_at: float,
    now: float,
) -> dict[str, object]:
    base_view = build_shell_view_model(
        snapshot,
        boot_mode=boot_mode,
        output_history=output_history,
        ui_tick=ui_tick,
        director_history=director_history,
        executor_history=executor_history,
        reveal_counts=None,
    )
    director_items_full = list(base_view.get("director_items_full") or [])
    executor_items_full = list(base_view.get("executor_items_full") or [])
    active_role = base_view.get("active_role")
    counts = {
        "Director": len(director_items_full),
        "Executor": len(executor_items_full),
    }
    signatures = {
        "Director": _history_signature(director_items_full),
        "Executor": _history_signature(executor_items_full),
    }
    if boot_mode or active_role not in {"Director", "Executor"}:
        return {
            "counts": counts,
            "signatures": signatures,
            "active_role": active_role,
            "last_reveal_at": last_reveal_at,
        }

    active_role = str(active_role)
    active_items = director_items_full if active_role == "Director" else executor_items_full
    max_visible = max(1, len(active_items))
    previous_counts = previous_counts or {}
    previous_signatures = previous_signatures or {}
    current_count = max(1, min(int(previous_counts.get(active_role, 1) or 1), max_visible))
    signature_changed = previous_signatures.get(active_role) != signatures[active_role]
    role_changed = previous_active_role != active_role

    if signature_changed or role_changed:
        current_count = 1
        last_reveal_at = now
    elif current_count < max_visible and (now - last_reveal_at) >= ACTIVE_REVEAL_INTERVAL_SECONDS:
        current_count = min(current_count + 1, max_visible)
        last_reveal_at = now

    counts[active_role] = current_count
    return {
        "counts": counts,
        "signatures": signatures,
        "active_role": active_role,
        "last_reveal_at": last_reveal_at,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autobci")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    subparsers = parser.add_subparsers(dest="command")

    provider = subparsers.add_parser("provider", help="管理本地模型 provider")
    provider_subparsers = provider.add_subparsers(dest="provider_action", required=True)
    provider_list = provider_subparsers.add_parser("list", help="列出可用 provider")
    provider_list.add_argument("--json", action="store_true")
    provider_test = provider_subparsers.add_parser("test", help="测试 provider")
    provider_test.add_argument("name")
    provider_test.add_argument("--model", default=None)
    provider_test.add_argument("--json", action="store_true")
    provider_set = provider_subparsers.add_parser("set", help="设置默认 provider")
    provider_set.add_argument("name")
    provider_set.add_argument("--model", default=None)
    provider_set.add_argument("--json", action="store_true")

    model_cmd = subparsers.add_parser("model", help="管理模块模型")
    model_subparsers = model_cmd.add_subparsers(dest="model_action", required=True)
    model_current = model_subparsers.add_parser("current", help="查看当前模块模型")
    model_current.add_argument("--agent", default="intake")
    model_current.add_argument("--json", action="store_true")
    model_list = model_subparsers.add_parser("list", help="列出模块和 Provider 模型")
    model_list.add_argument("--json", action="store_true")
    model_set = model_subparsers.add_parser("set", help="设置模块模型")
    model_set.add_argument("--agent", default="intake")
    model_set.add_argument("--provider", required=True)
    model_set.add_argument("--model", required=True)
    model_set.add_argument("--json", action="store_true")
    model_key = model_subparsers.add_parser("key", help="保存 Provider API key")
    model_key.add_argument("provider")
    model_key.add_argument("--api-key", default=None)
    model_key.add_argument("--json", action="store_true")
    model_test = model_subparsers.add_parser("test", help="测试 Provider")
    model_test.add_argument("provider")
    model_test.add_argument("--model", default=None)
    model_test.add_argument("--json", action="store_true")

    smoke = subparsers.add_parser("smoke", help="运行产品级 smoke 验收")
    smoke_subparsers = smoke.add_subparsers(dest="smoke_action", required=True)
    intake_smoke = smoke_subparsers.add_parser("intake-llm", help="用真实 Intake provider 跑常见对话和 plan 场景")
    intake_smoke.add_argument("--provider", default=None)
    intake_smoke.add_argument("--model", default=None)
    intake_smoke.add_argument("--use-current-repo", action="store_true")
    intake_smoke.add_argument("--json", action="store_true")

    doctor = subparsers.add_parser("doctor", help="检查本机运行环境")
    doctor.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="查看当前控制面状态")
    status.add_argument("--json", action="store_true")

    goal = subparsers.add_parser("goal", help="管理一次完成即停止的研究目标")
    goal_subparsers = goal.add_subparsers(dest="goal_action", required=True)
    goal_start = goal_subparsers.add_parser("start", help="创建 active goal")
    goal_start.add_argument("objective", nargs="+")
    goal_start.add_argument("--success", default="", help="完成检查口径")
    goal_start.add_argument("--constraint", action="append", default=[], help="约束，可重复")
    goal_start.add_argument("--replace", action="store_true")
    goal_start.add_argument("--json", action="store_true")
    goal_status = goal_subparsers.add_parser("status", help="查看 goal")
    goal_status.add_argument("--json", action="store_true")
    goal_complete = goal_subparsers.add_parser("complete", help="用证据完成 goal")
    goal_complete.add_argument("--evidence", required=True)
    goal_complete.add_argument("--json", action="store_true")
    goal_clear = goal_subparsers.add_parser("clear", help="清除 goal 状态")
    goal_clear.add_argument("--json", action="store_true")

    perp = subparsers.add_parser("perp", help="管理长期运行的永续研究目标")
    perp_subparsers = perp.add_subparsers(dest="perp_action", required=True)
    perp_start = perp_subparsers.add_parser("start", help="创建 active perp")
    perp_start.add_argument("objective", nargs="+")
    perp_start.add_argument("--cadence", default="owner_or_gateway_tick")
    perp_start.add_argument("--scope", default="local_harness")
    perp_start.add_argument("--replace", action="store_true")
    perp_start.add_argument("--json", action="store_true")
    perp_status = perp_subparsers.add_parser("status", help="查看 perp")
    perp_status.add_argument("--json", action="store_true")
    perp_stop = perp_subparsers.add_parser("stop", help="停止 perp")
    perp_stop.add_argument("--reason", default="")
    perp_stop.add_argument("--json", action="store_true")

    tree = subparsers.add_parser("research-tree", help="查看 Goal/Perp/事件组成的研究树")
    tree_subparsers = tree.add_subparsers(dest="tree_action", required=True)
    tree_show = tree_subparsers.add_parser("show", help="输出研究树")
    tree_show.add_argument("--json", action="store_true")
    tree_status = tree_subparsers.add_parser("status", help="输出研究树摘要")
    tree_status.add_argument("--json", action="store_true")

    data = subparsers.add_parser("data", help="管理本地数据目录配置")
    data_subparsers = data.add_subparsers(dest="data_action", required=True)
    data_set = data_subparsers.add_parser("set", help="保存本地数据目录")
    data_set.add_argument("path")
    data_subparsers.add_parser("show", help="显示当前本地数据目录")
    data_subparsers.add_parser("clear", help="清除当前本地数据目录")

    storage = subparsers.add_parser("storage", help="审计本地记录和产物占用")
    storage_subparsers = storage.add_subparsers(dest="storage_action", required=True)
    storage_audit = storage_subparsers.add_parser("audit", help="扫描重复文件和可压缩记录")
    storage_audit.add_argument("--json", action="store_true")
    storage_audit.add_argument("--min-duplicate-bytes", type=int, default=5 * 1024 * 1024)
    storage_audit.add_argument("--min-compressible-bytes", type=int, default=1024 * 1024)

    ask = subparsers.add_parser("ask", help="处理一次 headless 自然语言/白名单命令 turn")
    ask.add_argument("message", nargs="+", help="要交给 AutoBCI 的一句话，例如：现在进展如何？")
    ask.add_argument("--json", action="store_true")
    ask.add_argument(
        "--use-model-agent",
        action="store_true",
        help="允许调用已配置的 live intake 模型；默认只走确定性命令路由。",
    )

    dashboard = subparsers.add_parser("dashboard", help="打开 Dashboard 运行态投影")
    dashboard.add_argument(
        "--task",
        default=None,
        help="打开指定任务视图；默认打开控制面概览",
    )

    demo = subparsers.add_parser("demo", help="运行可交付现场 demo")
    demo_subparsers = demo.add_subparsers(dest="demo_action", required=True)
    onsite = demo_subparsers.add_parser("onsite", help="现场交付检查")
    onsite.add_argument("--provider", default=None, help="live intake provider，例如 openai")
    onsite.add_argument("--model", default=None, help="live intake model，例如 gpt-5.5")
    onsite.add_argument("--task", default=None, help="Dashboard 任务视图；默认打开控制面概览")
    onsite.add_argument("--skip-smoke", action="store_true", help="只启动 dashboard/status，不跑 live provider smoke")
    onsite.add_argument("--json", action="store_true")

    windows = subparsers.add_parser("windows", help="Windows 兼容性检查")
    windows_subparsers = windows.add_subparsers(dest="windows_action", required=True)
    windows_doctor = windows_subparsers.add_parser("doctor", help="检查 Windows readiness")
    windows_doctor.add_argument("--json", action="store_true")
    linux = subparsers.add_parser("linux", help="Linux 兼容性检查")
    linux_subparsers = linux.add_subparsers(dest="linux_action", required=True)
    linux_doctor = linux_subparsers.add_parser("doctor", help="检查 Linux readiness")
    linux_doctor.add_argument("--json", action="store_true")
    return parser


def _pick_current_best(snapshot: dict[str, object]) -> str:
    family_bests = snapshot.get("algorithm_family_bests")
    if isinstance(family_bests, list):
        ranked = [item for item in family_bests if isinstance(item, dict)]
        promotable = [item for item in ranked if item.get("best_promotable")]
        candidates = promotable or ranked
        if candidates:
            best = max(candidates, key=lambda item: float(item.get("best_val_r") or -1e9))
            label = str(best.get("best_method_display_label") or best.get("algorithm_label") or "-")
            score = str(best.get("best_val_r_label") or "-")
            return f"{label} · val r {score}"
    return "-"


def _panel(title: str, rows: list[str], width: int = 88) -> str:
    inner = max(width - 4, 20)
    content = [f"│ {_pad_display(title, inner)} │"]
    content.append(f"├{'─' * (width - 2)}┤")
    for row in rows:
        for line in (row.splitlines() or [""]):
            content.append(f"│ {_pad_display(line, inner)} │")
    border = f"┌{'─' * (width - 2)}┐"
    bottom = f"└{'─' * (width - 2)}┘"
    return "\n".join([border, *content, bottom])


def build_tui_screen(
    snapshot: dict[str, object],
    *,
    last_message: str = "",
    session_history: list[dict[str, Any]] | None = None,
    pending_action: dict[str, Any] | None = None,
    inflight_turn: dict[str, Any] | None = None,
    ui_tick: int = 0,
    show_events: bool = False,
) -> str:
    view = build_intake_chat_view_model(
        snapshot,
        session_history=session_history,
        output_history=[last_message] if last_message else [],
        pending_action=pending_action,
        inflight_turn=inflight_turn,
        ui_tick=ui_tick,
    )
    transcript: list[str] = [view["status_rule"]]
    banner = str(view.get("benchmark_banner") or "").strip()
    if banner:
        transcript.append(f"Framework Benchmark: {banner}")
    transcript.extend(["", INTAKE_WELCOME])
    for item in view["transcript_rows"]:
        role = str(item.get("role") or "intake")
        if role == "user":
            transcript.extend(["", f"› {item.get('text') or '-'}"])
        elif role == "tool":
            transcript.extend(["", _panel("工具调用", str(item.get("text") or "-").splitlines())])
        elif role == "card":
            card = item.get("card") if isinstance(item.get("card"), dict) else {}
            card_rows = [f"{label}：{value}" for label, value in card.get("rows", [])]
            missing = card.get("missing") if isinstance(card.get("missing"), list) else []
            if missing:
                card_rows.append("缺失字段：" + "、".join(str(value) for value in missing))
            card_rows.append(f"下一步：{card.get('next_step') or '-'}")
            transcript.extend(["", _panel(str(card.get("title") or "Program Draft"), card_rows)])
        else:
            transcript.extend(["", f"· {item.get('text') or '-'}"])
    trail = view["system_trail"]
    if show_events and trail["expanded"]:
        transcript.extend(["", *trail["expanded"]])
    elif bool(trail.get("show_default", True)):
        transcript.extend(["", str(trail["collapsed"])])
    transcript.extend(["", f"› {INTAKE_COMPOSER_PLACEHOLDER}"])
    parts = transcript
    return "\n".join(parts)


def build_rich_startup_screen() -> RenderableType:
    return _build_rich_shell_layout(
        {},
        boot_mode=True,
        last_message="正在接入当前研究态…",
        last_command="",
    )


def _build_top_status(
    snapshot: dict[str, object] | None,
    *,
    boot_mode: bool = False,
    inflight_turn: dict[str, Any] | None = None,
) -> RenderableType:
    view = build_intake_chat_view_model(
        snapshot or {},
        boot_mode=boot_mode,
        output_history=[],
        inflight_turn=inflight_turn,
    )
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1, justify="right")
    grid.add_row(
        _rich_text(view["status_rule"], style=f"bold {PALETTE['text']}", no_wrap=True),
        _rich_text(view["dashboard_url"], style=PALETTE["muted"], no_wrap=True),
    )
    if view.get("benchmark_banner"):
        grid.add_row(_rich_text(f"框架基准 / Framework Benchmark  {view['benchmark_banner']}", style=PALETTE["muted"], no_wrap=True), "")
    return Panel(
        grid,
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_alt']}",
        padding=(0, 1),
    )


def _build_state_panel(snapshot: dict[str, object], *, boot_mode: bool = False) -> RenderableType:
    view = build_intake_workspace_view_model(snapshot, boot_mode=boot_mode, output_history=["正在接入当前研究态…"] if boot_mode else None)
    program = view["program"]
    rows = Table.grid(padding=(0, 1))
    rows.add_column(style=PALETTE["accent"], no_wrap=True)
    rows.add_column(style=PALETTE["text"], ratio=1)
    rows.add_row("program_id", _rich_text(program["program_id"], style=PALETTE["text"], no_wrap=True))
    rows.add_row("status", _rich_text(program["status"], style=PALETTE["warning"], no_wrap=True))
    rows.add_row("task_type", _rich_text(program["task_type"], style=PALETTE["text"], no_wrap=True))
    rows.add_row("primary_metric", _rich_text(program["primary_metric"], style=PALETTE["text"], no_wrap=True))
    rows.add_row("amendment", _rich_text(program["amendment_state"], style=PALETTE["accent"], no_wrap=True))
    rows.add_row("judge", _rich_text(program["latest_judge_verdict"], style=PALETTE["success"], no_wrap=True))
    rows.add_row("guard", _rich_text(program["latest_guard_decision"], style=PALETTE["success"], no_wrap=True))
    return Panel(
        rows,
        title=_rich_text("Program / Amendment", style=PALETTE["accent"]),
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_bg']}",
        padding=(0, 1),
    )


def _build_conversation_panel(
    snapshot: dict[str, object],
    *,
    session_history: list[dict[str, Any]] | None = None,
    output_history: list[str] | None = None,
    pending_action: dict[str, Any] | None = None,
    inflight_turn: dict[str, Any] | None = None,
    boot_mode: bool = False,
) -> RenderableType:
    view = build_intake_chat_view_model(
        snapshot,
        session_history=session_history,
        output_history=output_history or (["正在接入当前研究态…"] if boot_mode else []),
        pending_action=pending_action,
        inflight_turn=inflight_turn,
        boot_mode=boot_mode,
    )
    content = Table.grid(expand=True)
    content.add_column(ratio=1)
    content.add_row(_rich_text(INTAKE_WELCOME, style=PALETTE["muted"]))
    for item in list(view["transcript_rows"])[-INTAKE_HISTORY_LIMIT:]:
        role = str(item.get("role") or "intake")
        if role == "card":
            card = item.get("card") if isinstance(item.get("card"), dict) else {}
            table = Table.grid(padding=(0, 1))
            table.add_column(style=PALETTE["accent"], no_wrap=True)
            table.add_column(style=PALETTE["text"], ratio=1)
            for label, value in card.get("rows", []):
                table.add_row(str(label), str(value))
            missing = card.get("missing") if isinstance(card.get("missing"), list) else []
            if missing:
                table.add_row("缺失字段", "、".join(str(value) for value in missing))
            table.add_row("下一步", str(card.get("next_step") or "-"))
            content.add_row(
                Panel(
                    table,
                    title=_rich_text(str(card.get("title") or "Program Draft"), style=PALETTE["accent"]),
                    box=ROUNDED,
                    border_style=PALETTE["border"],
                    padding=(0, 1),
                )
            )
            continue
        if role == "tool":
            content.add_row(
                Panel(
                    _rich_markdown_text(item.get("text") or "-", style=PALETTE["text"]),
                    title=_rich_text("工具调用", style=PALETTE["accent"]),
                    box=ROUNDED,
                    border_style=PALETTE["border"],
                    padding=(0, 1),
                )
            )
            continue
        if role == "user":
            row = Text.assemble(("› ", f"bold {PALETTE['accent']}"))
            row.append(_rich_markdown_text(item.get("text") or "-", style=PALETTE["user_text"]))
            content.add_row(row)
        else:
            row = Text.assemble(("· ", f"bold {PALETTE['success']}"))
            row.append(_rich_markdown_text(item.get("text") or "-", style=PALETTE["agent_text"]))
            content.add_row(row)
    trail_model = view["system_trail"] if isinstance(view.get("system_trail"), dict) else {}
    trail = str(trail_model.get("collapsed") or "")
    if trail and bool(trail_model.get("show_default", True)):
        content.add_row(_rich_text(trail, style=PALETTE["muted"]))
    return Panel(
        content,
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_bg']}",
        padding=(0, 1),
    )


def _build_system_events_panel(snapshot: dict[str, object], *, boot_mode: bool = False) -> RenderableType:
    view = build_intake_workspace_view_model(snapshot, boot_mode=boot_mode)
    content = Table.grid(expand=True)
    content.add_column(ratio=1)
    for item in view["system_event_items"][:8]:
        content.add_row(
            Text.assemble(
                (f"{item.get('time_label') or '--:--'} ", PALETTE["muted"]),
                (f"{item.get('message_type') or 'event'} ", f"bold {PALETTE['accent']}"),
                (f"[{item.get('status') or '-'}] ", PALETTE["warning"]),
                (str(item.get("detail") or "-"), PALETTE["text"]),
            )
        )
    if not view["system_event_items"]:
        content.add_row(_rich_text("还没有 program_handoff / policy_decision / judge_report。", style=PALETTE["muted"]))
    return Panel(
        content,
        title=_rich_text("System Events", style=PALETTE["accent"]),
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_bg']}",
        padding=(0, 1),
    )


def _build_commands_panel(*, boot_mode: bool = False) -> RenderableType:
    commands = Table.grid(expand=True)
    commands.add_column()
    if boot_mode:
        for line in (
            "new · 新起一个任务",
            "run · 开始或继续研究",
            "model · 切换模型",
            "tasks · 切换任务",
            "dashboard · 打开面板",
            "remote · 手机远程续聊",
        ):
            commands.add_row(_rich_text(line, style=PALETTE["muted"]))
    else:
        for command in ("new", "run", "model", "tasks", "dashboard", "remote"):
            commands.add_row(_rich_text(command, style=PALETTE["text"]))
    return Panel(
        commands,
        title=_rich_text("可用命令", style=PALETTE["accent"]),
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_bg']}",
        padding=(0, 1),
    )


def _build_output_panel(last_message: str, *, last_command: str = "", boot_mode: bool = False) -> RenderableType:
    content = Table.grid(expand=True)
    content.add_column(ratio=1)
    if boot_mode:
        content.add_row(_rich_text("正在同步当前研究态 / 正在读取当前主线 / dashboard ready", style=PALETTE["muted"]))
    elif last_command:
        content.add_row(
            Text.assemble(
                ("› ", f"bold {PALETTE['accent']}"),
                (last_command, PALETTE["text"]),
            )
        )
    content.add_row(_rich_text(last_message or "输入 help 查看命令。", style=PALETTE["text"]))
    return Panel(
        content,
        title=_rich_text("输出", style=PALETTE["accent"]),
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_bg']}",
        padding=(0, 1),
    )


def _build_input_panel(last_command: str, *, startup_mode: bool = False) -> RenderableType:
    command_text = last_command.strip()
    content = Table.grid(expand=True)
    content.add_column(ratio=1)
    prompt_line = Text.assemble(
        ("› ", f"bold {PALETTE['accent']}"),
        (command_text if command_text else INTAKE_COMPOSER_PLACEHOLDER, PALETTE["text"] if command_text else PALETTE["muted"]),
    )
    content.add_row(prompt_line)
    return Panel(
        content,
        box=ROUNDED,
        border_style=PALETTE["border"],
        style=f"on {PALETTE['panel_alt']}",
        padding=(0, 1),
    )


def build_rich_main_screen(
    snapshot: dict[str, object],
    *,
    last_message: str = "",
    last_command: str = "",
    session_history: list[dict[str, Any]] | None = None,
    pending_action: dict[str, Any] | None = None,
    inflight_turn: dict[str, Any] | None = None,
) -> RenderableType:
    return _build_rich_shell_layout(
        snapshot,
        last_message=last_message,
        last_command=last_command,
        session_history=session_history,
        pending_action=pending_action,
        inflight_turn=inflight_turn,
    )


def _build_rich_shell_layout(
    snapshot: dict[str, object],
    *,
    last_message: str = "",
    last_command: str = "",
    boot_mode: bool = False,
    session_history: list[dict[str, Any]] | None = None,
    pending_action: dict[str, Any] | None = None,
    inflight_turn: dict[str, Any] | None = None,
) -> RenderableType:
    layout = Layout(name="root")
    layout.split_column(
        Layout(_build_top_status(snapshot, boot_mode=boot_mode, inflight_turn=inflight_turn), name="header", size=3),
        Layout(
            _build_conversation_panel(
                snapshot,
                session_history=session_history,
                output_history=[last_message] if last_message else [],
                pending_action=pending_action,
                inflight_turn=inflight_turn,
                boot_mode=boot_mode,
            ),
            name="transcript",
            ratio=1,
        ),
        Layout(_build_input_panel(last_command, startup_mode=boot_mode), name="composer", size=3),
    )
    return layout


def _pt_header_fragments(
    snapshot: dict[str, object],
    *,
    boot_mode: bool = False,
    inflight_turn: dict[str, Any] | None = None,
    ui_tick: int = 0,
) -> StyleAndTextTuples:
    view = build_intake_workspace_view_model(
        snapshot,
        boot_mode=boot_mode,
        output_history=["输入 help 查看命令。"],
        inflight_turn=inflight_turn,
        ui_tick=ui_tick,
    )
    fragments: list[tuple[str, str]] = [
        ("class:header.brand", view["header_text"]),
    ]
    banner = str(view.get("benchmark_banner") or "").strip()
    if banner:
        fragments.extend(
            [
                ("class:header.muted", "\n"),
                ("class:header.banner.label", " 框架基准 / Framework Benchmark "),
                ("class:header.banner.value", f" {banner} "),
            ]
        )
    return fragments


def _pt_agent_fragments(
    items: list[dict[str, Any]],
    *,
    role: str,
    title: str,
    boot_mode: bool,
    active: bool | None = None,
    reveal_step: int | None = None,
) -> StyleAndTextTuples:
    title_style = "class:item.title.director" if role == "Director" else "class:item.title.executor"
    fragments: list[tuple[str, str]] = []
    if boot_mode and not items:
        placeholder = "正在读取最近判断…" if role == "Director" else "正在同步当前执行…"
        fragments.append(("class:item.empty", placeholder))
        return fragments
    if not items:
        fragments.append(("class:item.empty", f"当前还没有 {role} 侧的结构化动作。"))
        return fragments
    for index, item in enumerate(items):
        result_line = f"结果：{item.get('result') or '-'}"
        next_line = f"下一步：{item.get('next') or '-'}"
        item_title_style = "class:item.title.handoff" if item.get("is_handoff") else title_style
        fragments.extend(
            [
                ("class:item.time", f"{item.get('time_label', '--:--')}  "),
                (item_title_style, str(item.get("title") or "-")),
                ("class:panel.muted", "\n"),
                ("class:item.detail", f"    {item.get('detail') or '-'}"),
                ("class:panel.muted", "\n"),
                ("class:item.result", f"    {result_line}"),
                ("class:item.next", f" · {next_line}"),
            ]
        )
        if index != len(items) - 1:
            fragments.append(("class:panel.muted", "\n"))
    return fragments


def _pt_timeline_fragments(items: list[dict[str, Any]], *, boot_mode: bool = False) -> StyleAndTextTuples:
    fragments: list[tuple[str, str]] = []
    if boot_mode and not items:
        fragments.append(("class:item.empty", "正在读取计划对话历史…"))
        return fragments
    if not items:
        fragments.append(("class:item.empty", "等待用户描述研究问题。"))
        return fragments
    for index, item in enumerate(items[-18:]):
        role = str(item.get("role") or "intake")
        role_label = "User" if role == "user" else "计划"
        role_style = "class:message.user.prefix" if role == "user" else "class:message.agent.prefix"
        text_style = "class:message.user.text" if role == "user" else "class:message.agent.text"
        fragments.extend(
            [
                ("class:item.time", f"{item.get('time_label', '--:--')}  "),
                (role_style, role_label),
                ("class:panel.muted", f" · {item.get('intent_kind') or '-'}\n"),
                (text_style, f"    {item.get('text') or '-'}"),
            ]
        )
        if index != len(items[-18:]) - 1:
            fragments.append(("class:panel.muted", "\n"))
    return fragments


def _pt_fragment_line_count(fragments: StyleAndTextTuples) -> int:
    text = "".join(fragment for _style, fragment in fragments)
    try:
        from prompt_toolkit.application import get_app
        from prompt_toolkit.utils import get_cwidth

        available_width = int(get_app().output.get_size().columns) - 4
    except Exception:
        from prompt_toolkit.utils import get_cwidth

        available_width = int(shutil.get_terminal_size((88, 24)).columns) - 4
    available_width = max(20, available_width)
    visual_lines = 0
    for line in text.split("\n"):
        width = get_cwidth(line)
        visual_lines += max(1, -(-max(width, 1) // available_width))
    return max(1, visual_lines)


def _pt_conversation_viewport_rows() -> int:
    try:
        from prompt_toolkit.application import get_app

        rows = int(get_app().output.get_size().rows)
    except Exception:
        rows = int(shutil.get_terminal_size((88, 24)).lines)
    # Header: 2, input frame: usually 3-10, conversation frame borders: 2.
    # Keep this conservative so short conversations sit near the input box
    # without forcing long conversations to waste vertical space.
    return max(4, rows - 7)


def _pt_bottom_align_fragments(fragments: StyleAndTextTuples) -> StyleAndTextTuples:
    line_count = _pt_fragment_line_count(fragments)
    padding = max(0, _pt_conversation_viewport_rows() - line_count)
    if padding <= 0:
        return fragments
    return [("class:panel.muted", "\n" * padding), *fragments]


def _pt_transcript_fragments(view: dict[str, Any], *, boot_mode: bool = False, show_events: bool = False) -> StyleAndTextTuples:
    fragments: list[tuple[str, str]] = [
        ("class:item.detail", INTAKE_WELCOME),
    ]
    rows = list(view.get("transcript_rows") or [])
    if boot_mode and not rows:
        rows = [
            {
                "role": "intake",
                "text": "正在接入当前研究态…",
                "intent_kind": "boot",
            }
        ]
    for item in rows[-INTAKE_HISTORY_LIMIT:]:
        role = str(item.get("role") or "intake")
        fragments.append(("class:panel.muted", "\n\n"))
        if role == "user":
            fragments.extend(
                [
                    ("class:message.user.prefix", "› "),
                ]
            )
            fragments.extend(_pt_markdown_fragments(item.get("text") or "-", "class:message.user.text"))
        elif role == "card":
            card = item.get("card") if isinstance(item.get("card"), dict) else {}
            fragments.extend(
                [
                    ("class:item.title.handoff", str(card.get("title") or "Program Draft")),
                    ("class:panel.muted", "\n"),
                ]
            )
            for label, value in card.get("rows", []):
                fragments.extend(
                    [
                        ("class:item.title.director", f"  {label}："),
                    ]
                )
                fragments.extend(_pt_markdown_fragments(value, "class:item.detail"))
                fragments.append(("class:panel.muted", "\n"))
            missing = card.get("missing") if isinstance(card.get("missing"), list) else []
            if missing:
                fragments.extend(
                    [
                        ("class:item.title.director", "  缺失字段："),
                    ]
                )
                fragments.extend(_pt_markdown_fragments("、".join(str(value) for value in missing), "class:item.detail"))
                fragments.append(("class:panel.muted", "\n"))
            fragments.extend(
                [
                    ("class:item.title.director", "  下一步："),
                ]
            )
            fragments.extend(_pt_markdown_fragments(card.get("next_step") or "-", "class:item.detail"))
        elif role == "tool":
            fragments.extend(
                [
                    ("class:item.title.handoff", "╭─ 工具调用"),
                    ("class:panel.muted", "\n"),
                ]
            )
            lines = str(item.get("text") or "-").splitlines() or ["-"]
            if len(lines) > TUI_TOOL_OUTPUT_LINE_LIMIT:
                head_count = 10
                tail_count = max(4, TUI_TOOL_OUTPUT_LINE_LIMIT - head_count - 1)
                omitted = len(lines) - head_count - tail_count
                lines = [
                    *lines[:head_count],
                    f"... 已折叠 {omitted} 行，完整内容见上方滚动历史或对应文件 ...",
                    *lines[-tail_count:],
                ]
            for line in lines:
                fragments.extend(
                    [
                        ("class:item.title.director", "│ "),
                    ]
                )
                fragments.extend(_pt_markdown_fragments(line, "class:message.tool.text"))
                fragments.append(("class:panel.muted", "\n"))
            fragments.append(("class:item.title.handoff", "╰─"))
        else:
            fragments.extend(
                [
                    ("class:message.agent.prefix", "· "),
                ]
            )
            fragments.extend(_pt_markdown_fragments(item.get("text") or "-", "class:message.agent.text"))
    trail = view.get("system_trail") if isinstance(view.get("system_trail"), dict) else {}
    expanded = list(trail.get("expanded") or [])
    if show_events and expanded:
        fragments.append(("class:panel.muted", "\n\n"))
        for index, line in enumerate(expanded):
            fragments.append(("class:item.next", str(line)))
            if index != len(expanded) - 1:
                fragments.append(("class:panel.muted", "\n"))
    elif bool(trail.get("show_default", True)):
        fragments.append(("class:panel.muted", "\n\n"))
        fragments.append(("class:item.next", str(trail.get("collapsed") or "┊ run trail: no events")))
    return _pt_bottom_align_fragments(fragments)


def _pt_program_fragments(program: dict[str, Any]) -> StyleAndTextTuples:
    rows = [
        ("program_id", program.get("program_id")),
        ("version", program.get("version")),
        ("status", program.get("status")),
        ("task_type", program.get("task_type")),
        ("primary_metric", program.get("primary_metric")),
        ("amendment", program.get("amendment_state")),
        ("judge", program.get("latest_judge_verdict")),
        ("guard", program.get("latest_guard_decision")),
    ]
    fragments: list[tuple[str, str]] = []
    for index, (label, value) in enumerate(rows):
        fragments.extend(
            [
                ("class:item.title.director", f"{label}: "),
                ("class:item.detail", str(value or "-")),
            ]
        )
        if index != len(rows) - 1:
            fragments.append(("class:panel.muted", "\n"))
    return fragments


def _pt_system_event_fragments(items: list[dict[str, Any]]) -> StyleAndTextTuples:
    if not items:
        return [("class:item.empty", "还没有 program_handoff / policy_decision / judge_report。")]
    fragments: list[tuple[str, str]] = []
    for index, item in enumerate(items[:SYSTEM_EVENT_LIMIT]):
        fragments.extend(
            [
                ("class:item.time", f"{item.get('time_label', '--:--')}  "),
                ("class:item.title.handoff", str(item.get("message_type") or "event")),
                ("class:item.result", f" [{item.get('status') or '-'}]\n"),
                ("class:item.detail", f"    {item.get('detail') or '-'}"),
            ]
        )
        if index != len(items[:SYSTEM_EVENT_LIMIT]) - 1:
            fragments.append(("class:panel.muted", "\n"))
    return fragments


def _pt_output_fragments(history: list[object], *, boot_mode: bool = False) -> StyleAndTextTuples:
    entries = history[-6:] if history else ["输入 help 查看命令。"]
    if boot_mode and entries == ["输入 help 查看命令。"]:
        entries = ["正在接入当前研究态…"]
    fragments: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        line = _output_history_text(entry)
        style = "class:panel.value"
        if line.startswith("AutoBCI>"):
            fragments.extend(
                [
                    ("class:prompt.label", "AutoBCI"),
                    ("class:prompt.arrow", "> "),
                    ("class:panel.value", line.split(">", 1)[1].lstrip()),
                ]
            )
        else:
            fragments.append((style, line))
        if index != len(entries) - 1:
            fragments.append(("class:panel.muted", "\n"))
    return fragments


def _should_use_prompt_toolkit(*, input_fn: Callable[[str], str] | None, output: TextIO | None) -> bool:
    engine = _requested_tui_engine()
    return bool(
        engine in {"auto", "prompt_toolkit", "prompt-toolkit", "pt", "legacy"}
        and PROMPT_TOOLKIT_AVAILABLE
        and input_fn is None
        and output is None
        and sys.stdout.isatty()
    )


def _requested_tui_engine() -> str:
    value = str(os.environ.get(AUTOBCI_TUI_ENGINE_ENV) or "auto").strip().lower()
    return value or "auto"


def _textual_available() -> bool:
    try:
        return importlib.util.find_spec("textual") is not None
    except Exception:
        return False


def _should_use_textual(*, input_fn: Callable[[str], str] | None, output: TextIO | None) -> bool:
    engine = _requested_tui_engine()
    if engine not in {"auto", "textual"}:
        return False
    return bool(input_fn is None and output is None and sys.stdout.isatty() and _textual_available())


def _run_textual_tui(
    *,
    repo_root: Path,
    host: str,
    port: int,
    python_executable: str | None = None,
) -> int:
    try:
        from bci_autoresearch.product_shell.textual_tui import run_textual_tui
    except ImportError as exc:
        raise RuntimeError("Textual TUI requested but textual is not installed. Run: python -m pip install -e '.[dev]'") from exc
    return run_textual_tui(
        repo_root=repo_root,
        host=host,
        port=port,
        python_executable=python_executable,
    )


def _env_flag_enabled(name: str) -> bool:
    value = str(os.environ.get(name) or "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def is_tui_test_mode_enabled() -> bool:
    return _env_flag_enabled(AUTOBCI_TUI_TEST_MODE_ENV)


def should_use_tui_model_agent() -> bool:
    return not is_tui_test_mode_enabled()


def _run_prompt_toolkit_tui(
    *,
    repo_root: Path,
    host: str,
    port: int,
    python_executable: str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    if not PROMPT_TOOLKIT_AVAILABLE:
        raise RuntimeError("prompt_toolkit is required for interactive TUI mode")

    _maybe_enable_readline()
    runtime_profile = _terminal_runtime_profile()
    paths = get_control_plane_paths(repo_root)
    shell_session: dict[str, Any] = {}
    snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, shell_session)
    _ensure_default_program_plan(paths, shell_session, snapshot=snapshot)
    snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, shell_session)
    initial_setup_message = _maybe_open_initial_model_setup(shell_session)
    state_lock = threading.Lock()
    state: dict[str, object] = {
        "snapshot": snapshot,
        "boot_mode": True,
        "output_history": [initial_setup_message] if initial_setup_message else ["正在同步当前研究态…"],
        "inflight_turn": None,
        "ui_tick": 0,
        "session_history": read_current_intake_history(paths),
    }
    history_path = Path.home() / ".autobci_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    class _SecretAwareHistory(PTHistory):  # type: ignore[misc,valid-type]
        def __init__(self, base_history: Any, is_secret: Callable[[], bool]) -> None:
            super().__init__()
            self._base_history = base_history
            self._is_secret = is_secret

        def load_history_strings(self) -> Any:
            return self._base_history.load_history_strings()

        def store_string(self, string: str) -> None:
            if not self._is_secret():
                self._base_history.store_string(string)

        def append_string(self, string: str) -> None:
            if not self._is_secret():
                self._loaded_strings.insert(0, string)
                self._base_history.append_string(string)

    def _refresh_snapshot() -> dict[str, object]:
        current = _attach_experiment_state(build_status_snapshot(paths), paths, shell_session)
        with state_lock:
            state["snapshot"] = current
            state["session_history"] = read_current_intake_history(paths)
        return current

    def _current_snapshot() -> dict[str, object]:
        with state_lock:
            current = state.get("snapshot")
        return current if isinstance(current, dict) else {}

    def _current_inflight_turn() -> dict[str, Any] | None:
        with state_lock:
            current = state.get("inflight_turn")
        return current if isinstance(current, dict) else None

    def _current_ui_tick() -> int:
        with state_lock:
            return int(state.get("ui_tick", 0))

    def _current_view() -> dict[str, Any]:
        with state_lock:
            output_history = list(state.get("output_history") or [])
            boot_mode = bool(state.get("boot_mode"))
            session_history = list(state.get("session_history") or [])
            show_events = bool(state.get("show_events"))
            inflight_turn = state.get("inflight_turn")
            ui_tick = int(state.get("ui_tick", 0))
        pending_action = shell_session.get("pending_action") if isinstance(shell_session.get("pending_action"), dict) else None
        view = build_intake_chat_view_model(
            _current_snapshot(),
            boot_mode=boot_mode,
            output_history=output_history,
            session_history=session_history,
            pending_action=pending_action,
            inflight_turn=inflight_turn if isinstance(inflight_turn, dict) else None,
            ui_tick=ui_tick,
        )
        view["show_events"] = show_events
        return view

    def _header_control() -> PTFormattedTextControl:
        return PTFormattedTextControl(
            lambda: _pt_header_fragments(
                _current_snapshot(),
                boot_mode=bool(state.get("boot_mode")),
                inflight_turn=_current_inflight_turn(),
                ui_tick=_current_ui_tick(),
            )
        )

    def _conversation_control() -> PTFormattedTextControl:
        return _PaneFormattedTextControl(
            lambda: _pt_transcript_fragments(_current_view(), boot_mode=bool(_current_view()["boot_mode"]), show_events=bool(_current_view().get("show_events"))),
            on_scroll=lambda delta: _scroll_pane("conversation", delta),
            on_focus=lambda: _focus_pane("conversation"),
            focusable=True,
            show_cursor=False,
        )

    def _program_control() -> PTFormattedTextControl:
        return _PaneFormattedTextControl(
            lambda: _pt_program_fragments(_current_view()["program"]),
            on_scroll=lambda delta: _scroll_pane("program", delta),
            on_focus=lambda: _focus_pane("program"),
            focusable=True,
            show_cursor=False,
        )

    def _events_control() -> PTFormattedTextControl:
        return _PaneFormattedTextControl(
            lambda: _pt_system_event_fragments(_current_view()["system_event_items"]),
            on_scroll=lambda delta: _scroll_pane("events", delta),
            on_focus=lambda: _focus_pane("events"),
            focusable=True,
            show_cursor=False,
        )

    def _output_control() -> PTFormattedTextControl:
        return PTFormattedTextControl(lambda: _pt_output_fragments(_current_view()["output_history"], boot_mode=bool(_current_view()["boot_mode"])))

    app_ref: list[PTApplication | None] = [None]
    stop_event = threading.Event()
    last_input_activity_at = [0.0]
    def _secret_input_context() -> dict[str, Any] | None:
        current = shell_session.get("secret_input")
        return current if isinstance(current, dict) else None

    def _prompt_fragments() -> StyleAndTextTuples:
        secret_input = _secret_input_context()
        if secret_input is not None:
            provider = str(secret_input.get("provider") or "provider")
            return [
                ("class:prompt.label", f"{provider} API key"),
                ("class:prompt.arrow", " › "),
            ]
        return [
            ("class:prompt.label", "› "),
        ]

    def _apply_command_view_flags(normalized: str) -> None:
        if normalized in {"/events", "events", "/details", "details"}:
            state["show_events"] = True
        if normalized in {"/new", "new", "/new clean", "new clean"}:
            state["show_events"] = False

    def _finish_inflight_command(command: str, should_quit: bool, message: str) -> None:
        normalized = command.strip().lower()
        if _current_reasoning_mode(shell_session) == "raw" and "推理调试" not in message:
            message = message + "\n\n推理调试：provider 未返回原始 CoT；本轮已写入本地可审计判断链。"
        turn_id = str(shell_session.get("_last_command_turn_id") or "")
        with state_lock:
            history = list(state.get("output_history") or [])
            history.append(_make_output_history_entry(message, turn_id=turn_id or None))
            state["output_history"] = history[-TUI_OUTPUT_HISTORY_LIMIT:]
            state["inflight_turn"] = None
            _apply_command_view_flags(normalized)
        _refresh_snapshot()
        _sync_pane_visual_state()
        if app_ref[0] is not None:
            _request_conversation_tail()
        if should_quit and app_ref[0] is not None:
            stop_event.set()
            app_ref[0].exit(result=0)

    def _handle_command_worker(command: str) -> None:
        try:
            command_lock = shell_session.setdefault("_remote_command_lock", threading.RLock())
            with command_lock:
                should_quit, message = handle_command(
                    command,
                    repo_root=repo_root,
                    host=host,
                    port=port,
                    python_executable=python_executable,
                    session_state=shell_session,
                    use_model_agent=should_use_tui_model_agent(),
                )
        except Exception as exc:
            should_quit = False
            message = f"计划对话这一轮处理失败：{type(exc).__name__}。你的消息已经收到，没有丢失。"
        _finish_inflight_command(command, should_quit, message)

    def _finish_secret_input(message: str) -> None:
        with state_lock:
            history = list(state.get("output_history") or [])
            history.append(_make_output_history_entry(message))
            state["output_history"] = history[-TUI_OUTPUT_HISTORY_LIMIT:]
            state["inflight_turn"] = None
        _refresh_snapshot()
        _sync_pane_visual_state()
        if app_ref[0] is not None:
            _request_conversation_tail()

    def _handle_secret_worker(secret_input: dict[str, Any], secret_value: str) -> None:
        provider = str(secret_input.get("provider") or "").strip().lower()
        try:
            message = _save_provider_secret_from_input(
                provider,
                secret_value,
                after_save_agent=str(secret_input.get("after_save_agent") or "").strip().lower() or None,
                after_save_model=str(secret_input.get("after_save_model") or "").strip() or None,
            )
        except Exception as exc:
            message = f"{provider} API key 保存失败：{type(exc).__name__}。"
        _finish_secret_input(message)

    def _accept(buffer: Any) -> bool:
        raw_command = str(buffer.text or "")
        secret_input = _secret_input_context()
        if secret_input is not None:
            provider = str(secret_input.get("provider") or "").strip().lower()
            secret_payload = dict(secret_input)
            shell_session.pop("secret_input", None)
            buffer.text = ""
            with state_lock:
                state["boot_mode"] = False
                state["output_history"] = [f"正在保存 {provider} API key…"]
                state["inflight_turn"] = None
            if app_ref[0] is not None:
                app_ref[0].invalidate()
            threading.Thread(target=_handle_secret_worker, args=(secret_payload, raw_command), daemon=True).start()
            return False
        command = raw_command.strip()
        last_input_activity_at[0] = time.monotonic()
        with state_lock:
            state["boot_mode"] = False
        if not command:
            buffer.text = ""
            with state_lock:
                history = list(state.get("output_history") or [])
                history.extend([_make_output_history_entry("AutoBCI> "), _make_output_history_entry("请输入命令。")])
                state["output_history"] = history[-TUI_OUTPUT_HISTORY_LIMIT:]
        else:
            with state_lock:
                if isinstance(state.get("inflight_turn"), dict):
                    history = list(state.get("output_history") or [])
                    history.append(_make_output_history_entry("上一条消息还在处理，请稍等。"))
                    state["output_history"] = history[-TUI_OUTPUT_HISTORY_LIMIT:]
                    if app_ref[0] is not None:
                        app_ref[0].invalidate()
                    return False
                state["inflight_turn"] = build_inflight_turn(command)
            _scroll_conversation_to_tail()
            buffer.text = ""
            if app_ref[0] is not None:
                app_ref[0].invalidate()
            threading.Thread(target=_handle_command_worker, args=(command,), daemon=True).start()
            return False
        _refresh_snapshot()
        _sync_pane_visual_state()
        if app_ref[0] is not None:
            app_ref[0].invalidate()
        return False

    input_height = PTDimension(min=1, max=8, preferred=1) if PTDimension is not None else 3
    input_area = PTTextArea(
        height=input_height,
        multiline=True,
        password=PTCondition(lambda: _secret_input_context() is not None) if PTCondition is not None else False,
        wrap_lines=True,
        prompt=_prompt_fragments,
        style="class:input-area",
        history=_SecretAwareHistory(FileHistory(str(history_path)), lambda: _secret_input_context() is not None),
        completer=build_slash_command_completer(),
        complete_while_typing=True,
        auto_suggest=None,
        accept_handler=_accept,
    )

    def _input_height() -> int:
        try:
            from prompt_toolkit.application import get_app
            from prompt_toolkit.utils import get_cwidth

            prompt_width = max(2, get_cwidth("".join(fragment for _style, fragment in _prompt_fragments())))
            try:
                available_width = get_app().output.get_size().columns - prompt_width - 4
            except Exception:
                available_width = shutil.get_terminal_size((88, 24)).columns - prompt_width - 4
            available_width = max(20, available_width)
            document = input_area.buffer.document
            visual_lines = 0
            for line in document.lines:
                line_width = get_cwidth(line)
                visual_lines += max(1, -(-max(line_width, 1) // available_width))
            return min(max(visual_lines, 1), 8)
        except Exception:
            return 1

    try:
        input_area.window.height = _input_height
    except Exception:
        pass
    try:
        input_area.buffer.on_text_changed += lambda _buffer: last_input_activity_at.__setitem__(0, time.monotonic())
    except Exception:
        pass

    conversation_control = _conversation_control()
    output_control = _output_control()

    conversation_window = PTWindow(
        content=conversation_control,
        wrap_lines=True,
        style="class:panel",
        always_hide_cursor=True,
    )
    conversation_scroll = PTScrollablePane(
        conversation_window,
        show_scrollbar=True,
        display_arrows=False,
    )

    def _scroll_conversation_to_tail() -> None:
        try:
            app = app_ref[0]
            if app is None:
                conversation_scroll.vertical_scroll = 0
                return
            width = max(20, int(app.output.get_size().columns) - 4)
            content = conversation_control.create_content(width, None)
            control_height = 0
            for line_index in range(content.line_count):
                control_height += int(content.get_height_for_line(line_index, width, None))
            preferred_dimension = conversation_window.preferred_height(width, 10_000)
            preferred_height = int(preferred_dimension.preferred or 0)
            view = _current_view()
            fragment_height = _pt_fragment_line_count(
                _pt_transcript_fragments(
                    view,
                    boot_mode=bool(view.get("boot_mode")),
                    show_events=bool(view.get("show_events")),
                )
            )
            content_height = max(preferred_height, fragment_height, control_height)
            conversation_scroll.vertical_scroll = max(0, content_height - _pt_conversation_viewport_rows())
        except Exception:
            return

    def _request_conversation_tail() -> None:
        app = app_ref[0]
        if app is None:
            return

        def _apply() -> None:
            _scroll_conversation_to_tail()
            try:
                redraw = getattr(app, "_redraw", None)
                if callable(redraw) and bool(getattr(app, "_is_running", False)):
                    app._invalidated = False
                    redraw()
                else:
                    app.invalidate()
            except Exception:
                try:
                    app.invalidate()
                except Exception:
                    return

        loop = getattr(app, "loop", None)
        if loop is None or loop.is_closed():
            _apply()
            return
        loop.call_soon_threadsafe(_apply)

    header = PTWindow(
        content=_header_control(),
        height=2,
        style="class:header",
    )
    conversation_panel = PTFrame(
        conversation_scroll,
        title="",
        style="class:panel",
    )
    input_panel = PTFrame(
        input_area,
        title="",
        style="class:input-frame",
    )
    root_content = PTHSplit(
        [
            header,
            conversation_panel,
            input_panel,
        ],
        padding=0,
        padding_char=" ",
        padding_style="class:app",
        style="class:app",
    )
    if PTFloatContainer is not None and PTFloat is not None and PTCompletionsMenu is not None:
        completions_menu = _build_full_width_completions_menu()
        root = PTFloatContainer(
            content=root_content,
            floats=[
                PTFloat(
                    left=0,
                    right=0,
                    bottom=3,
                    content=completions_menu,
                    z_index=1000,
                )
            ],
        )
    else:
        root = root_content
    kb = PTKeyBindings()

    focus_order = [conversation_window, input_area]
    last_pointer_pane: dict[str, str | None] = {"pane": None}

    def _focused_scrollable() -> PTScrollablePane | None:
        if app_ref[0] is None:
            return None
        if app_ref[0].layout.has_focus(conversation_window):
            return conversation_scroll
        return None

    def _focus_pane(pane: str) -> None:
        last_pointer_pane["pane"] = pane

    def _scroll_pane(pane: str, delta: int) -> None:
        targets = {
            "conversation": conversation_scroll,
        }
        target = targets.get(pane, conversation_scroll)
        target.vertical_scroll = max(0, target.vertical_scroll + delta)
        if app_ref[0] is not None:
            app_ref[0].invalidate()

    def _cycle_focus(step: int) -> None:
        if app_ref[0] is None:
            return
        current_index = 0
        for index, element in enumerate(focus_order):
            try:
                if app_ref[0].layout.has_focus(element):
                    current_index = index
                    break
            except Exception:
                continue
        next_index = (current_index + step) % len(focus_order)
        app_ref[0].layout.focus(focus_order[next_index])
        app_ref[0].invalidate()

    def _scroll_current_pane(delta: int) -> None:
        hovered_pane = last_pointer_pane.get("pane")
        if hovered_pane in {"conversation"}:
            _scroll_pane(str(hovered_pane), delta)
            return
        target = _focused_scrollable()
        if target is None:
            target = conversation_scroll
        target.vertical_scroll = max(0, target.vertical_scroll + delta)
        if app_ref[0] is not None:
            app_ref[0].invalidate()

    def _sync_pane_visual_state() -> None:
        conversation_panel.title = ""

    @kb.add("c-c")
    @kb.add("c-d")
    def _exit(event: Any) -> None:
        event.app.exit(result=0)

    @kb.add("tab")
    def _focus_next(_event: Any) -> None:
        _cycle_focus(1)

    @kb.add("s-tab")
    def _focus_prev(_event: Any) -> None:
        _cycle_focus(-1)

    @kb.add("pageup")
    def _page_up(_event: Any) -> None:
        _scroll_current_pane(-PAGE_SCROLL_LINES)

    @kb.add("pagedown")
    def _page_down(_event: Any) -> None:
        _scroll_current_pane(PAGE_SCROLL_LINES)

    @kb.add("<scroll-up>")
    def _scroll_up(_event: Any) -> None:
        _scroll_current_pane(-3)

    @kb.add("<scroll-down>")
    def _scroll_down(_event: Any) -> None:
        _scroll_current_pane(3)

    def _input_area_has_focus() -> bool:
        if app_ref[0] is None:
            return True
        try:
            return bool(app_ref[0].layout.has_focus(input_area))
        except Exception:
            return False

    def _normal_input_has_focus() -> bool:
        return _input_area_has_focus() and _secret_input_context() is None

    @kb.add("escape", "enter", filter=PTCondition(_input_area_has_focus) if PTCondition is not None else True)
    def _insert_newline_alt_enter(event: Any) -> None:
        input_area.buffer.insert_text("\n")
        if app_ref[0] is not None:
            app_ref[0].layout.focus(input_area)

    @kb.add("c-j", filter=PTCondition(_input_area_has_focus) if PTCondition is not None else True)
    def _insert_newline_ctrl_enter(event: Any) -> None:
        input_area.buffer.insert_text("\n")
        if app_ref[0] is not None:
            app_ref[0].layout.focus(input_area)

    @kb.add("up", filter=PTCondition(_normal_input_has_focus) if PTCondition is not None else True)
    def _history_or_cursor_up(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_previous(count=event.arg)
            return
        buffer.auto_up(count=event.arg)

    @kb.add("down", filter=PTCondition(_normal_input_has_focus) if PTCondition is not None else True)
    def _history_or_cursor_down(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_next(count=event.arg)
            return
        buffer.auto_down(count=event.arg)

    @kb.add("enter")
    @kb.add("c-m")
    def _submit_input(event: Any) -> None:
        if app_ref[0] is not None:
            app_ref[0].layout.focus(input_area)
        _accept(input_area.buffer)

    def _input_is_being_edited() -> bool:
        if app_ref[0] is None:
            return False
        try:
            has_focus = app_ref[0].layout.has_focus(input_area)
        except Exception:
            has_focus = False
        if not has_focus:
            return False
        try:
            if bool(input_area.buffer.text):
                return True
        except Exception:
            pass
        return (time.monotonic() - last_input_activity_at[0]) < 0.9

    def _safe_invalidate() -> None:
        if app_ref[0] is None:
            return
        if bool(runtime_profile.get("defer_repaint_while_typing")) and _input_is_being_edited():
            return
        app_ref[0].invalidate()

    style = _build_prompt_toolkit_style()
    cursor = runtime_profile.get("cursor")
    app = PTApplication(
        layout=PTLayout(root, focused_element=input_area),
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=bool(runtime_profile.get("mouse_support")),
        cursor=cursor,
    )
    app_ref[0] = app

    def _finish_boot() -> None:
        sleep_fn(0.0 if is_tui_test_mode_enabled() else 0.18)
        with state_lock:
            state["boot_mode"] = False
            if not isinstance(state.get("inflight_turn"), dict):
                selection_context = shell_session.get("selection_context")
                setup_active = (
                    bool(initial_setup_message)
                    and isinstance(selection_context, dict)
                    and str(selection_context.get("kind") or "").startswith("model_")
                )
                if not setup_active:
                    state["output_history"] = ["已接入当前研究态。输入 help 查看命令。"]
        _refresh_snapshot()
        _sync_pane_visual_state()
        _safe_invalidate()

    def _auto_refresh_loop() -> None:
        while not stop_event.wait(AUTO_REFRESH_INTERVAL_SECONDS):
            with state_lock:
                state["ui_tick"] = int(state.get("ui_tick", 0)) + 1
            _refresh_snapshot()
            _sync_pane_visual_state()
            _safe_invalidate()

    def _ui_animation_loop() -> None:
        while not stop_event.wait(0.4):
            with state_lock:
                state["ui_tick"] = int(state.get("ui_tick", 0)) + 1
            _sync_pane_visual_state()
            _safe_invalidate()

    def _pre_run() -> None:
        app.layout.focus(input_area)
        _sync_pane_visual_state()
        threading.Thread(target=_finish_boot, daemon=True).start()
        if not is_tui_test_mode_enabled():
            threading.Thread(target=_auto_refresh_loop, daemon=True).start()
        if bool(runtime_profile.get("animate_ui")) and not is_tui_test_mode_enabled():
            threading.Thread(target=_ui_animation_loop, daemon=True).start()

    with patch_stdout():
        try:
            return int(app.run(pre_run=_pre_run) or 0)
        finally:
            stop_event.set()


def _maybe_enable_readline() -> bool:
    try:
        importlib.import_module("readline")
    except ImportError:
        return False
    return True


def export_debug_renderables(
    *,
    snapshot: dict[str, object],
    output_dir: Path | None = None,
    last_message: str = "",
    last_command: str = "",
    width: int = 120,
) -> dict[str, str]:
    if not RICH_AVAILABLE:
        raise RuntimeError("rich is required to export debug renderables")
    destination = (output_dir or Path(tempfile.mkdtemp(prefix="autobci-rich-exports-"))).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    theme = _build_terminal_theme()
    outputs: dict[str, str] = {}
    for label, renderable in {
        "startup": build_rich_startup_screen(),
        "main": build_rich_main_screen(snapshot, last_message=last_message, last_command=last_command),
    }.items():
        console = Console(
            record=True,
            force_terminal=True,
            width=width,
            color_system="truecolor",
            file=io.StringIO(),
        )
        console.print(renderable)
        svg_path = destination / f"{label}.svg"
        html_path = destination / f"{label}.html"
        console.save_svg(str(svg_path), theme=theme, clear=False)
        console.save_html(str(html_path), clear=False)
        outputs[f"{label}_svg"] = str(svg_path)
        outputs[f"{label}_html"] = str(html_path)
    return outputs


def is_dashboard_running(host: str, port: int, *, timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def dashboard_server_repo_root(host: str, port: int, *, timeout: float = 0.5) -> Path | None:
    try:
        with urlopen(f"http://{host}:{port}/api/status", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
    root_text = server.get("repo_root") or payload.get("repo_root")
    if not root_text:
        return None
    try:
        return Path(str(root_text)).resolve()
    except Exception:
        return None


def dashboard_server_matches_repo(repo_root: Path, host: str, port: int) -> bool:
    server_root = dashboard_server_repo_root(host, port)
    return server_root == repo_root.resolve()


def resolve_dashboard_launch_port(repo_root: Path, host: str, preferred_port: int, *, search_limit: int = 20) -> tuple[int, int | None]:
    if not is_dashboard_running(host, preferred_port):
        return preferred_port, None
    if dashboard_server_matches_repo(repo_root, host, preferred_port):
        return preferred_port, None
    for candidate in range(preferred_port + 1, preferred_port + search_limit + 1):
        if not is_dashboard_running(host, candidate):
            return candidate, preferred_port
        if dashboard_server_matches_repo(repo_root, host, candidate):
            return candidate, None
    return preferred_port, preferred_port


def _load_provider_module() -> Any | None:
    try:
        return importlib.import_module("bci_autoresearch.providers")
    except ModuleNotFoundError:
        return None


def _provider_call(function_names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    module = _load_provider_module()
    if module is None:
        raise RuntimeError("provider 模块尚未接入。")
    for name in function_names:
        func = getattr(module, name, None)
        if callable(func):
            return func(*args, **kwargs)
    raise RuntimeError(f"provider 模块缺少接口：{' / '.join(function_names)}")


def _provider_list_payload() -> dict[str, Any]:
    payload = _provider_call(("list_provider_statuses", "provider_list", "list_providers", "list_provider_configs"))
    if isinstance(payload, dict):
        result = dict(payload)
        if isinstance(result.get("providers"), list):
            result["providers"] = _sort_provider_rows(result["providers"])
        return result
    return {"ok": True, "providers": _sort_provider_rows(payload if isinstance(payload, list) else [])}


def _sort_provider_rows(providers: list[Any]) -> list[Any]:
    order = {name: index for index, name in enumerate(MODEL_PROVIDER_ORDER)}

    def key(item: Any) -> tuple[int, str]:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or "").strip().lower()
        else:
            name = str(item).strip().lower()
        return (order.get(name, len(order)), name)

    return sorted(providers, key=key)


def _provider_list() -> list[dict[str, Any]]:
    payload = _provider_list_payload()
    providers = payload.get("providers", [])
    default_provider = str(payload.get("default_provider") or "").strip().lower()
    if not isinstance(providers, list):
        raise RuntimeError("provider list 返回格式不是列表。")
    rows: list[dict[str, Any]] = []
    for item in providers:
        if isinstance(item, dict):
            row = dict(item)
            name = str(row.get("name") or row.get("id") or "").strip().lower()
            if "configured" not in row and "ready" in row:
                row["configured"] = bool(row.get("ready"))
            if "current" not in row and default_provider:
                row["current"] = name == default_provider
            rows.append(row)
        else:
            rows.append({"name": str(item), "configured": None, "current": False})
    return rows


def format_provider_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Provider 列表：当前没有发现可用 provider。"
    lines = ["Provider 列表："]
    for item in rows:
        name = _provider_display_label(item)
        configured = item.get("configured")
        current = bool(item.get("current") or item.get("active"))
        status = "已配置" if configured is True else "未配置" if configured is False else "配置未知"
        marker = "当前" if current else "可选"
        message = str(item.get("message") or "").strip()
        suffix = f" · {message}" if message else ""
        lines.append(f"- {name} · {status} · {marker}{suffix}")
    return "\n".join(lines)


def _provider_test(name: str, *, model: str | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    try:
        payload = _provider_call(("test_provider", "provider_test"), name, model=model, repo_root=repo_root)
    except TypeError:
        payload = _provider_call(("test_provider", "provider_test"), name)
    if isinstance(payload, dict):
        return dict(payload)
    return {"ok": True, "provider": name, "message": str(payload)}


def _provider_set(name: str, *, model: str | None = None) -> dict[str, Any]:
    try:
        payload = _provider_call(("set_default_provider", "set_provider", "provider_set"), name, model=model)
    except TypeError:
        payload = _provider_call(("set_default_provider", "set_provider", "provider_set"), name)
    if isinstance(payload, dict):
        return dict(payload)
    return {"ok": True, "default_provider": name, "message": str(payload)}


def handle_provider_command(args: argparse.Namespace) -> tuple[int, str]:
    try:
        if args.provider_action == "list":
            payload = _provider_list_payload()
            if getattr(args, "json", False):
                return 0, json.dumps(payload, ensure_ascii=False, indent=2)
            return 0, format_provider_list(_provider_list())
        if args.provider_action == "test":
            payload = _provider_test(args.name, model=getattr(args, "model", None))
            if getattr(args, "json", False):
                return (0 if payload.get("ok") else 1), json.dumps(payload, ensure_ascii=False, indent=2)
            ok = bool(payload.get("ok", True)) if isinstance(payload, dict) else True
            message = str(payload.get("message") or "") if isinstance(payload, dict) else str(payload)
            provider = str(payload.get("provider") or args.name) if isinstance(payload, dict) else args.name
            model = str(payload.get("model") or getattr(args, "model", None) or "-") if isinstance(payload, dict) else "-"
            return (0 if ok else 1), f"{provider} provider {'可用' if ok else '不可用'}：{message or model}"
        if args.provider_action == "set":
            payload = _provider_set(args.name, model=getattr(args, "model", None))
            if getattr(args, "json", False):
                return (0 if payload.get("ok") else 1), json.dumps(payload, ensure_ascii=False, indent=2)
            ok = bool(payload.get("ok", True)) if isinstance(payload, dict) else True
            message = str(payload.get("message") or "") if isinstance(payload, dict) else str(payload)
            provider = str(payload.get("default_provider") or payload.get("provider") or args.name) if isinstance(payload, dict) else args.name
            model = str(payload.get("default_model") or getattr(args, "model", None) or "-") if isinstance(payload, dict) else "-"
            return (0 if ok else 1), f"Provider 设置：{provider} · {model} · {message or ('已设置' if ok else '失败')}"
    except Exception as exc:
        return 1, f"Provider 命令失败：{exc}"
    return 1, "未知 provider 命令。"


def _model_current_payload(agent: str) -> dict[str, Any]:
    payload = _provider_call(("resolve_agent_provider_model",), agent)
    return dict(payload) if isinstance(payload, dict) else {"agent": agent, "provider": "-", "model": "-"}


def _model_list_payload() -> dict[str, Any]:
    return _provider_list_payload()


def _format_model_current(payload: dict[str, Any]) -> str:
    agent = str(payload.get("agent") or "intake")
    live = "已接入实时调用" if payload.get("live") else "已保存配置，暂未接入实时调用"
    message = str(payload.get("message") or "").strip()
    suffix = f" · 配置错误：{message}" if message else ""
    return f"{_agent_label(agent)}：{payload.get('provider') or '-'} / {payload.get('model') or '-'} · {live}{suffix}"


def _format_model_list(payload: dict[str, Any]) -> str:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    lines = ["模型列表："]
    if agents:
        lines.append("当前模型：")
        visible_agents = {str(item["name"]) for item in MODEL_AGENT_OPTIONS}
        for item in agents:
            if isinstance(item, dict):
                if str(item.get("agent") or "").strip().lower() not in visible_agents:
                    continue
                lines.append("- " + _format_model_current(item))
    if providers:
        lines.append("Provider：")
        for item in providers:
            if isinstance(item, dict):
                lines.append(f"- {_provider_display_label(item)} · {item.get('model') or item.get('default_model') or '-'} · {_provider_ready_label(item)}")
    return "\n".join(lines)


def handle_model_command(args: argparse.Namespace) -> tuple[int, str]:
    try:
        if args.model_action == "current":
            payload = _model_current_payload(args.agent)
            if getattr(args, "json", False):
                return 0, json.dumps(payload, ensure_ascii=False, indent=2)
            return 0, _format_model_current(payload)
        if args.model_action == "list":
            payload = _model_list_payload()
            if getattr(args, "json", False):
                return 0, json.dumps(payload, ensure_ascii=False, indent=2)
            return 0, _format_model_list(payload)
        if args.model_action == "set":
            ok, message = _test_provider_message(args.provider, model=args.model)
            if not ok:
                payload = {"ok": False, "provider": args.provider, "model": args.model, "message": message}
                if getattr(args, "json", False):
                    return 1, json.dumps(payload, ensure_ascii=False, indent=2)
                return 1, message + "。旧模型保持不变。"
            payload = _provider_call(("set_agent_model", "set_agent_provider_model"), args.agent, args.provider, model=args.model)
            result = dict(payload) if isinstance(payload, dict) else {"ok": True, "agent": args.agent, "provider": args.provider, "model": args.model}
            if getattr(args, "json", False):
                return (0 if result.get("ok") else 1), json.dumps(result, ensure_ascii=False, indent=2)
            return (0 if result.get("ok") else 1), _format_model_current(result)
        if args.model_action == "key":
            api_key = str(getattr(args, "api_key", None) or "").strip()
            if not api_key:
                api_key = getpass.getpass(f"{args.provider} API key: ").strip()
            payload = _provider_call(("write_provider_secret",), args.provider, api_key)
            result = dict(payload) if isinstance(payload, dict) else {"ok": True, "provider": args.provider}
            if getattr(args, "json", False):
                return (0 if result.get("ok") else 1), json.dumps(result, ensure_ascii=False, indent=2)
            return (0 if result.get("ok") else 1), f"已保存 {args.provider} API key。"
        if args.model_action == "test":
            payload = _provider_test(args.provider, model=getattr(args, "model", None))
            if getattr(args, "json", False):
                return (0 if payload.get("ok") else 1), json.dumps(payload, ensure_ascii=False, indent=2)
            ok = bool(payload.get("ok"))
            provider = str(payload.get("provider") or args.provider)
            model = str(payload.get("model") or getattr(args, "model", None) or "-")
            if ok:
                return 0, f"{provider} provider 可用：{model}"
            message = str(payload.get("message") or payload.get("error_code") or "测试失败")
            missing = str(payload.get("missing_api_key_env") or "").strip()
            suffix = f"。缺少 {missing}" if missing else ""
            return 1, f"{provider} provider 不可用：{message}{suffix}"
    except Exception as exc:
        return 1, f"模型命令失败：{exc}"
    return 1, "未知 model 命令。"


def _smoke_message_excerpt(message: str, limit: int = 180) -> str:
    clean = " ".join(str(message or "").split())
    return clean[:limit] + ("..." if len(clean) > limit else "")


def _pending_program_id(state: dict[str, Any]) -> str:
    pending = state.get("pending_action") if isinstance(state.get("pending_action"), dict) else {}
    draft = pending.get("program_draft") if isinstance(pending, dict) else {}
    if isinstance(draft, dict):
        return str(draft.get("program_id") or "")
    return ""


def _run_smoke_step(
    steps: list[dict[str, Any]],
    *,
    name: str,
    command: str,
    action: Callable[[], dict[str, Any]],
    expect: Callable[[dict[str, Any]], tuple[bool, str]],
) -> dict[str, Any]:
    try:
        payload = action()
        ok, reason = expect(payload)
        step = {"name": name, "command": command, **payload, "ok": bool(ok), "check": reason}
    except Exception as exc:
        step = {
            "name": name,
            "ok": False,
            "command": command,
            "reason": f"{type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
        }
    steps.append(step)
    return step


def _expect_tool(tool_name: str) -> Callable[[dict[str, Any]], tuple[bool, str]]:
    def _inner(payload: dict[str, Any]) -> tuple[bool, str]:
        actual = str(payload.get("tool_name") or "")
        if actual == tool_name:
            return True, f"tool={actual}"
        return False, f"expected tool={tool_name}, got {actual or '-'}"

    return _inner


def _expect_message_contains(fragment: str) -> Callable[[dict[str, Any]], tuple[bool, str]]:
    def _inner(payload: dict[str, Any]) -> tuple[bool, str]:
        text = str(payload.get("message") or "")
        if fragment in text:
            return True, f"message contains {fragment}"
        return False, f"message missing {fragment}"

    return _inner


def _expect_no_local_substitute(payload: dict[str, Any]) -> tuple[bool, str]:
    text = str(payload.get("message") or "")
    forbidden = ("没有及时返回", "返回不符合约定")
    found = [item for item in forbidden if item in text]
    if found:
        return False, "local substitute appeared: " + ", ".join(found)
    if payload.get("should_quit"):
        return False, "shell requested quit"
    return True, "no local substitute"


def _expect_pending_program(program_id: str) -> Callable[[dict[str, Any]], tuple[bool, str]]:
    def _inner(payload: dict[str, Any]) -> tuple[bool, str]:
        actual = str(payload.get("pending_program_id") or "")
        if actual == program_id:
            return True, f"pending_program_id={actual}"
        return False, f"expected pending_program_id={program_id}, got {actual or '-'}"

    return _inner


def _shell_smoke_action(
    command: str,
    *,
    repo_root: Path,
    state: dict[str, Any],
    host: str,
    port: int,
) -> dict[str, Any]:
    should_quit, message = handle_command(
        command,
        repo_root=repo_root,
        host=host,
        port=port,
        session_state=state,
        use_model_agent=True,
    )
    return {
        "should_quit": should_quit,
        "message": message,
        "message_excerpt": _smoke_message_excerpt(message),
        "pending_program_id": _pending_program_id(state),
        "pending_plan_mode": bool(
            isinstance(state.get("pending_action"), dict) and state.get("pending_action", {}).get("plan_mode")
        ),
        "pending_plan_status": (
            str(state.get("pending_action", {}).get("plan_status") or "")
            if isinstance(state.get("pending_action"), dict)
            else ""
        ),
    }


def run_intake_llm_smoke(
    *,
    repo_root: Path,
    provider: str | None = None,
    model: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    env_overrides: dict[str, str] = {}
    if provider:
        env_overrides["AUTOBCI_INTAKE_PROVIDER"] = provider
    if model:
        env_overrides["AUTOBCI_INTAKE_MODEL"] = model
    previous_env = {key: os.environ.get(key) for key in env_overrides}
    try:
        os.environ.update(env_overrides)
        provider_payload = _model_current_payload("intake")
        smoke_snapshot = {"program_state": {}, "autoresearch_status": {}, "current_track_id": ""}
        direct_cases = [
            ("json_greeting_contract", "你好", _expect_tool("reply")),
            ("json_status_contract", "现在系统是什么状态？", _expect_tool("read_status")),
            (
                "json_bci_task_contract",
                "现在生成 Program：做一个严格因果的 BCI 二分类任务，冻结 train/val/test，主指标 test_balanced_accuracy。",
                _expect_tool("draft_program"),
            ),
        ]
        for name, command, expectation in direct_cases:
            _run_smoke_step(
                steps,
                name=name,
                command=command,
                action=lambda command=command: run_codex_intake_agent_turn(
                    command,
                    smoke_snapshot,
                    repo_root=root,
                    timeout_seconds=DEFAULT_INTAKE_AGENT_TIMEOUT_SECONDS,
                ),
                expect=expectation,
            )
        shell_cases: list[tuple[str, str, Callable[[dict[str, Any]], tuple[bool, str]]]] = [
            ("shell_greeting", "你好", _expect_no_local_substitute),
            ("shell_status_question", "现在系统是什么状态？", _expect_message_contains("当前")),
            ("plan_enter", "/plan", _expect_message_contains("Program 起草已开启")),
            (
                "plan_bci_draft",
                "现在生成 Program：做一个严格因果的 BCI 二分类任务，冻结 train/val/test，主指标 test_balanced_accuracy。",
                _expect_pending_program("gait_phase_binary_v0"),
            ),
            ("plan_show", "/plan show", _expect_message_contains("gait_phase_binary_v0")),
            ("plan_accept", "/plan accept", _expect_message_contains("Program 已确认")),
        ]
        for name, command, expectation in shell_cases:
            _run_smoke_step(
                steps,
                name=name,
                command=command,
                action=lambda command=command: _shell_smoke_action(
                    command,
                    repo_root=root,
                    state=state,
                    host=host,
                    port=port,
                ),
                expect=expectation,
            )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    ok = all(bool(item.get("ok")) for item in steps)
    payload = {
        "ok": ok,
        "recorded_at": _utc_now_label(),
        "provider": provider_payload.get("provider") if isinstance(provider_payload, dict) else provider,
        "model": provider_payload.get("model") if isinstance(provider_payload, dict) else model,
        "agent": "intake",
        "repo_root": str(root),
        "steps": steps,
        "note": "This smoke uses the configured Intake provider; provider/config/runtime failures are reported as failures.",
    }
    out_dir = root / "artifacts" / "monitor" / "intake_llm_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_json_atomic(out_dir / f"{run_id}.json", payload)
    write_json_atomic(out_dir / "latest.json", payload)
    payload["artifact_path"] = str(out_dir / "latest.json")
    return payload


def handle_smoke_command(args: argparse.Namespace, *, repo_root: Path, host: str, port: int) -> tuple[int, str]:
    try:
        if args.smoke_action == "intake-llm":
            target_root = repo_root if getattr(args, "use_current_repo", False) else Path(tempfile.mkdtemp(prefix="autobci-intake-smoke-"))
            payload = run_intake_llm_smoke(
                repo_root=target_root,
                provider=getattr(args, "provider", None),
                model=getattr(args, "model", None),
                host=host,
                port=port,
            )
            if getattr(args, "json", False):
                return (0 if payload.get("ok") else 1), json.dumps(payload, ensure_ascii=False, indent=2)
            failed = [item for item in payload.get("steps", []) if isinstance(item, dict) and not item.get("ok")]
            header = f"Intake LLM smoke：{'通过' if payload.get('ok') else '失败'} · {payload.get('provider')}/{payload.get('model')}"
            lines = [header, f"- artifact: {payload.get('artifact_path') or '-'}"]
            for item in failed[:3]:
                lines.append(f"- failed {item.get('name')}: {item.get('reason')}")
            return (0 if payload.get("ok") else 1), "\n".join(lines)
    except Exception as exc:
        return 1, f"Smoke 命令失败：{type(exc).__name__}: {exc}"
    return 1, "未知 smoke 命令。"


def _provider_config_status() -> dict[str, Any]:
    module = _load_provider_module()
    if module is None:
        return {"ok": False, "status": "missing", "message": "provider 模块尚未接入"}
    for name in ("list_provider_statuses", "provider_list", "get_provider_config_status", "provider_config_status", "current_provider"):
        func = getattr(module, name, None)
        if callable(func):
            try:
                payload = func()
            except Exception as exc:
                return {"ok": False, "status": "error", "message": f"{type(exc).__name__}: {exc}"}
            if isinstance(payload, dict):
                result = dict(payload)
                if isinstance(result.get("providers"), list):
                    result["providers"] = _sort_provider_rows(result["providers"])
                result.setdefault("ok", True)
                return result
            return {"ok": True, "status": "ok", "current": str(payload)}
    try:
        rows = _provider_list()
    except Exception as exc:
        return {"ok": False, "status": "error", "message": str(exc)}
    configured = [item for item in rows if item.get("configured") is True]
    return {"ok": bool(configured), "status": "ok" if configured else "missing_config", "providers": rows}


def _public_dataset_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    public_keys = ("dataset_name", "dataset_root", "source", "updated_at")
    return {key: record[key] for key in public_keys if record.get(key)}


def build_doctor_report(*, repo_root: Path, host: str, port: int) -> dict[str, Any]:
    python_candidates = [repo_root / ".venv" / "Scripts" / "python.exe", repo_root / ".venv" / "bin" / "python"]
    active_venv_python = venv_python_path(repo_root / ".venv")
    cache_root = default_cache_root()
    worktrees_root = default_execution_worktrees_root(repo_root)
    provider_status = _provider_config_status()
    pi_runner = Path(__file__).resolve().parent.parent / "providers" / "pi_runner.mjs"
    custom_structure_runner = os.environ.get(STRUCTURE_SANDBOX_RUNNER_ENV, "").strip()
    opencode_path = shutil.which("opencode")
    codex_path = shutil.which("codex")
    builtin_patch_worker = builtin_patch_worker_status()
    if custom_structure_runner:
        active_structure_executor = custom_structure_runner
        active_structure_executor_kind = "custom_runner"
    elif opencode_path:
        active_structure_executor = opencode_path
        active_structure_executor_kind = "opencode"
    elif codex_path:
        active_structure_executor = codex_path
        active_structure_executor_kind = "codex"
    elif builtin_patch_worker.get("ok"):
        active_structure_executor = "builtin_patch_worker"
        active_structure_executor_kind = "builtin_patch_worker"
    else:
        active_structure_executor = None
        active_structure_executor_kind = None
    dataset_record = configured_task_dataset(repo_root, task_id=DEFAULT_DATA_TASK_ID)
    dataset_root = Path(str(dataset_record.get("dataset_root"))) if isinstance(dataset_record, dict) and dataset_record.get("dataset_root") else None
    public_harness_files = [
        "README.md",
        "AGENTS.md",
        "pyproject.toml",
        "src/bci_autoresearch/product_shell/cli.py",
        "src/bci_autoresearch/providers/client.py",
        "src/bci_autoresearch/storage_optimizer.py",
    ]
    harness_files = [
        {
            "path": item,
            "exists": (repo_root / item).exists(),
        }
        for item in public_harness_files
    ]
    report = {
        "python": {
            "ok": bool(sys.executable),
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "venv_candidates": [str(path) for path in python_candidates],
            "selected_venv_python": str(active_venv_python),
        },
        "node": {"ok": shutil.which("node") is not None, "path": shutil.which("node")},
        "npm": {"ok": shutil.which("npm") is not None, "path": shutil.which("npm")},
        "ui": {
            "ok": True,
            "mode": "headless",
            "message": "AutoBCI no longer requires a TUI. Use CLI/JSON commands from Codex, Claude Code, Cursor, Hermes, or other agents.",
        },
        "pi_runtime": {
            "ok": pi_runner.exists() and shutil.which("node") is not None,
            "runner_file": str(pi_runner),
            "runner_file_exists": pi_runner.exists(),
            "node_ok": shutil.which("node") is not None,
        },
        "structure_sandbox_runner": {
            "ok": bool(active_structure_executor),
            "executor_order": ["custom_runner", "opencode", "codex", "builtin_patch_worker"],
            "custom_runner": custom_structure_runner or None,
            "opencode_path": opencode_path,
            "codex_path": codex_path,
            "active": active_structure_executor,
            "active_kind": active_structure_executor_kind,
            "builtin_patch_worker": builtin_patch_worker,
            "timeout_seconds": os.environ.get(STRUCTURE_SANDBOX_TIMEOUT_ENV, "600"),
        },
        "data_paths": {
            "ok": bool(dataset_root and dataset_root.exists()),
            "purpose": "generic_bci_dataset",
            "config_path": str(data_paths_config_path(repo_root)),
            "configured": _public_dataset_record(dataset_record),
            "dataset_root_exists": bool(dataset_root and dataset_root.exists()),
            "env_var": "AUTOBCI_DATASET_ROOT",
        },
        "harness_files": {
            "ok": all(bool(item["exists"]) for item in harness_files),
            "scope": "generic_public_harness",
            "files": harness_files,
        },
        "provider": provider_status,
        "provider_config": provider_status,
        "dashboard_port": {
            "ok": is_dashboard_running(host, port),
            "host": host,
            "port": port,
            "url": f"http://{host}:{port}/",
        },
        "repo_root": {
            "ok": repo_root.exists(),
            "path": str(repo_root.resolve()),
            "has_agents": (repo_root / "AGENTS.md").exists(),
        },
        "windows_readiness": {
            "ok": True,
            "platform": sys.platform,
            "is_windows": is_windows(),
            "cache_root": str(cache_root),
            "execution_worktrees_root": str(worktrees_root),
            "process_group": "CREATE_NEW_PROCESS_GROUP on Windows; start_new_session on POSIX",
            "pause_resume": "Windows records desired_state and pause/resume requests; POSIX also sends best-effort signals.",
        },
        "linux_readiness": {
            "ok": os.name == "posix",
            "platform": sys.platform,
            "is_linux": sys.platform.startswith("linux"),
            "cache_root": str(cache_root),
            "execution_worktrees_root": str(worktrees_root),
            "venv_python": str(active_venv_python),
            "requires_node": True,
            "install_command": "bash scripts/install_linux.sh",
            "start_command": "source .venv/bin/activate && autobci status --json",
            "process_group": "start_new_session on POSIX",
        },
    }
    report["ok"] = bool(
        report["python"]["ok"]
        and report["repo_root"]["ok"]
        and report["node"]["ok"]
        and report["npm"]["ok"]
        and report["pi_runtime"]["runner_file_exists"]
    )
    return report


def format_doctor_report(report: dict[str, Any], *, windows_only: bool = False, linux_only: bool = False) -> str:
    if windows_only:
        readiness = report["windows_readiness"]
        return "\n".join(
            [
                "Windows readiness：",
                f"- cache root：{readiness['cache_root']}",
                f"- worktrees root：{readiness['execution_worktrees_root']}",
                f"- venv python：{report['python']['selected_venv_python']}",
                f"- pause/resume：{readiness['pause_resume']}",
            ]
        )
    if linux_only:
        readiness = report["linux_readiness"]
        return "\n".join(
            [
                "Linux readiness：",
                f"- cache root：{readiness['cache_root']}",
                f"- worktrees root：{readiness['execution_worktrees_root']}",
                f"- venv python：{readiness['venv_python']}",
                f"- install：{readiness['install_command']}",
                f"- start：{readiness['start_command']}",
            ]
        )
    rows = [
        "AutoBCI doctor：",
        f"- Python：{'通过' if report['python']['ok'] else '失败'} · {report['python']['executable']}",
        f"- Node：{'通过' if report['node']['ok'] else '未找到'} · {report['node'].get('path') or '-'}",
        f"- npm：{'通过' if report['npm']['ok'] else '未找到'} · {report['npm'].get('path') or '-'}",
        f"- UI：{report['ui']['mode']} · TUI not required",
        f"- Pi runtime：{'通过' if report['pi_runtime']['runner_file_exists'] else '缺 runner'} · {report['pi_runtime']['runner_file']}",
        f"- structure sandbox executor：{'通过' if report['structure_sandbox_runner']['ok'] else '未找到可用结构执行器'} · active={report['structure_sandbox_runner'].get('active_kind') or '-'}",
        f"- data paths：{'已配置' if report['data_paths']['ok'] else '未配置或路径不可达'} · {report['data_paths']['config_path']}",
        f"- provider config：{'通过' if report['provider_config'].get('ok') else '未就绪'}",
        f"- dashboard：{'端口可达' if report['dashboard_port']['ok'] else '端口未监听'} · {report['dashboard_port']['url']}",
        f"- repo root：{'通过' if report['repo_root']['ok'] else '不存在'} · {report['repo_root']['path']}",
        f"- Windows readiness：cache={report['windows_readiness']['cache_root']} · worktrees={report['windows_readiness']['execution_worktrees_root']}",
        f"- Linux readiness：install={report['linux_readiness']['install_command']} · start={report['linux_readiness']['start_command']}",
    ]
    return "\n".join(rows)


def _demo_task_stream_path(repo_root: Path) -> Path:
    return repo_root / "artifacts" / "monitor" / "demo_task_stream.json"


def _write_demo_task_stream(repo_root: Path, payload: dict[str, object]) -> None:
    write_json_atomic(_demo_task_stream_path(repo_root), payload)


def _new_demo_step(step_id: str, title: str, summary: str) -> dict[str, object]:
    return {
        "step_id": step_id,
        "title": title,
        "summary": summary,
        "status": "pending",
        "tone": "off",
        "started_at": None,
        "finished_at": None,
    }


def _update_demo_step(
    steps: list[dict[str, object]],
    step_id: str,
    *,
    status: str,
    summary: str | None = None,
    tone: str | None = None,
) -> dict[str, object]:
    step = next((item for item in steps if item.get("step_id") == step_id), None)
    if step is None:
        step = _new_demo_step(step_id, step_id, summary or "")
        steps.append(step)
    now = _utc_now_label()
    if status == "running" and not step.get("started_at"):
        step["started_at"] = now
    if status in {"done", "failed", "skipped"}:
        step.setdefault("started_at", now)
        step["finished_at"] = now
    step["status"] = status
    if summary is not None:
        step["summary"] = summary
    if tone is not None:
        step["tone"] = tone
    elif status == "done":
        step["tone"] = "ok"
    elif status == "failed":
        step["tone"] = "warn"
    elif status == "running":
        step["tone"] = "warn"
    return step


def _publish_demo_stream(
    repo_root: Path,
    *,
    run_id: str,
    status: str,
    steps: list[dict[str, object]],
    message: str,
) -> None:
    current_step = next((item for item in steps if item.get("status") == "running"), None)
    if current_step is None:
        current_step = next((item for item in steps if item.get("status") == "failed"), None)
    _write_demo_task_stream(
        repo_root,
        {
            "run_id": run_id,
            "title": "Onsite live demo",
            "status": status,
            "message": message,
            "updated_at": _utc_now_label(),
            "current_step": current_step or {},
            "steps": steps,
        },
    )


def run_onsite_demo_delivery(
    *,
    repo_root: Path,
    host: str,
    port: int,
    provider: str | None = None,
    model: str | None = None,
    task_id: str | None = None,
    run_smoke: bool = True,
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("onsite-demo-%Y%m%dT%H%M%SZ")
    steps = [
        _new_demo_step("doctor", "Runtime doctor", "检查 Python、Node、provider、Pi runtime、dashboard 和 runner。"),
        _new_demo_step("status", "Control-plane status", "读取当前研究控制面状态。"),
        _new_demo_step("dashboard", "Open dashboard", "启动并打开动态 Dashboard。"),
        _new_demo_step("intake_smoke", "Intake provider smoke", "用真实 provider 验证自然语言到 Program 的入口。"),
    ]
    payload: dict[str, Any] = {
        "ok": False,
        "run_id": run_id,
        "recorded_at": _utc_now_label(),
        "repo_root": str(repo_root.resolve()),
        "dashboard": {"url": build_dashboard_url(host, port, task_id=task_id)},
        "steps": steps,
        "next_commands": [
            "autobci status --json",
            "autobci dashboard",
            "autobci smoke intake-llm --provider openai --model gpt-5.5 --json",
        ],
    }

    def publish(status: str, message: str) -> None:
        _publish_demo_stream(repo_root, run_id=run_id, status=status, steps=steps, message=message)

    publish("running", "Onsite demo 正在启动。")
    try:
        _update_demo_step(steps, "doctor", status="running", summary="正在检查本机运行环境。")
        publish("running", "正在检查 runtime。")
        doctor = build_doctor_report(repo_root=repo_root, host=host, port=port)
        payload["doctor"] = doctor
        _update_demo_step(
            steps,
            "doctor",
            status="done" if doctor.get("ok") else "failed",
            summary=f"ok={bool(doctor.get('ok'))} · provider={bool((doctor.get('provider_config') or {}).get('ok'))}",
        )
        publish("running", "runtime 检查完成。")

        _update_demo_step(steps, "status", status="running", summary="正在读取控制面状态。")
        publish("running", "正在读取控制面状态。")
        status_snapshot = build_status_snapshot(get_control_plane_paths(repo_root))
        resolved_task_id = normalize_dashboard_task_id(task_id)
        research_loop = status_research_loop(repo_root, task_id=resolved_task_id) if resolved_task_id else {}
        payload["status"] = status_snapshot
        payload["research_loop"] = research_loop
        _update_demo_step(
            steps,
            "status",
            status="done",
            summary=(
                f"{status_snapshot.get('agent_status') or 'idle'} · "
                f"research-loop {research_loop.get('phase') or '-'} · queued {research_loop.get('queued_count') or 0}"
            ),
        )
        publish("running", "控制面状态已读取。")

        _update_demo_step(steps, "dashboard", status="running", summary="正在启动并打开 Dashboard。")
        publish("running", "正在打开 Dashboard。")
        dashboard_message = run_dashboard_command(repo_root=repo_root, host=host, port=port, task_id=task_id)
        payload["dashboard"]["message"] = dashboard_message
        url_match = re.search(r"https?://[^\s()（）]+", dashboard_message)
        if url_match:
            payload["dashboard"]["url"] = url_match.group(0)
        dashboard_ok = "启动失败" not in dashboard_message
        _update_demo_step(
            steps,
            "dashboard",
            status="done" if dashboard_ok else "failed",
            summary=dashboard_message,
        )
        publish("running", "Dashboard 已处理。")

        if run_smoke:
            _update_demo_step(
                steps,
                "intake_smoke",
                status="running",
                summary=f"provider={provider or 'current'} · model={model or 'current'}",
            )
            publish("running", "正在跑真实 provider intake smoke。")
            smoke_root = Path(tempfile.mkdtemp(prefix="autobci-onsite-demo-smoke-"))
            smoke = run_intake_llm_smoke(
                repo_root=smoke_root,
                provider=provider,
                model=model,
                host=host,
                port=port,
            )
            payload["smoke"] = smoke
            smoke_ok = bool(smoke.get("ok"))
            _update_demo_step(
                steps,
                "intake_smoke",
                status="done" if smoke_ok else "failed",
                summary=f"{smoke.get('provider') or provider or '-'} / {smoke.get('model') or model or '-'} · ok={smoke_ok}",
            )
        else:
            payload["smoke"] = {"ok": None, "skipped": True}
            _update_demo_step(steps, "intake_smoke", status="skipped", summary="用户选择跳过 live provider smoke。", tone="off")

        payload["ok"] = bool(
            payload.get("doctor", {}).get("ok")
            and "启动失败" not in str(payload.get("dashboard", {}).get("message") or "")
            and (not run_smoke or bool(payload.get("smoke", {}).get("ok")))
        )
        publish("done" if payload["ok"] else "failed", "Onsite demo 交付流程已完成。")
        return payload
    except Exception as exc:
        payload["ok"] = False
        payload["error"] = f"{type(exc).__name__}: {exc}"
        running = next((item for item in steps if item.get("status") == "running"), None)
        if running:
            _update_demo_step(steps, str(running.get("step_id") or "unknown"), status="failed", summary=payload["error"])
        publish("failed", payload["error"])
        return payload


def format_onsite_demo_delivery(payload: dict[str, Any]) -> str:
    dashboard = payload.get("dashboard") if isinstance(payload.get("dashboard"), dict) else {}
    smoke = payload.get("smoke") if isinstance(payload.get("smoke"), dict) else {}
    research_loop = payload.get("research_loop") if isinstance(payload.get("research_loop"), dict) else {}
    lines = [
        f"Onsite demo：{'通过' if payload.get('ok') else '失败'}",
        f"- dashboard：{dashboard.get('message') or dashboard.get('url') or '-'}",
        f"- research-loop：{research_loop.get('phase') or '-'} · queued {research_loop.get('queued_count') or 0} · ledger {research_loop.get('ledger_count') or 0}",
    ]
    if smoke.get("skipped"):
        lines.append("- intake smoke：已跳过")
    elif smoke:
        lines.append(f"- intake smoke：{'通过' if smoke.get('ok') else '失败'} · {smoke.get('provider') or '-'}/{smoke.get('model') or '-'}")
    if payload.get("error"):
        lines.append(f"- error：{payload['error']}")
    lines.extend(
        [
            "",
            "现场可用命令：",
            "- autobci status --json",
            "- autobci dashboard",
            "- autobci demo onsite --provider openai --model gpt-5.5",
        ]
    )
    return "\n".join(lines)


DASHBOARD_TASK_ALIASES = {
    "legacy": "legacy_bci_mainline",
    "bci": "legacy_bci_mainline",
    "gait": "legacy_bci_mainline",
    "walk": "legacy_bci_mainline",
    "步态": "legacy_bci_mainline",
}


def normalize_dashboard_task_id(task_id: str | None) -> str | None:
    raw = str(task_id or "").strip()
    if not raw:
        return None
    return DASHBOARD_TASK_ALIASES.get(raw.lower(), raw)


def build_dashboard_url(host: str, port: int, *, task_id: str | None = None) -> str:
    url = f"http://{host}:{port}/"
    resolved_task_id = normalize_dashboard_task_id(task_id)
    if not resolved_task_id:
        return url
    return f"{url}?{urlencode({'task': resolved_task_id})}"


def dashboard_runtime_status(repo_root: Path, host: str, port: int, *, task_id: str | None = None) -> dict[str, Any]:
    running = is_dashboard_running(host, port)
    server_root = dashboard_server_repo_root(host, port) if running else None
    matches_repo = bool(server_root and server_root == repo_root.resolve())
    launch_port, conflicted_port = resolve_dashboard_launch_port(repo_root, host, port)
    status = "ready" if matches_repo else "foreign" if running else "not_running"
    resolved_task_id = normalize_dashboard_task_id(task_id)
    recommended_command = f"autobci dashboard --port {launch_port}"
    if resolved_task_id:
        recommended_command += f" --task {resolved_task_id}"
    return {
        "status": status,
        "running": running,
        "matches_repo": matches_repo,
        "host": host,
        "port": port,
        "url": build_dashboard_url(host, port, task_id=resolved_task_id),
        "server_repo_root": str(server_root) if server_root else "",
        "recommended_port": launch_port,
        "recommended_url": build_dashboard_url(host, launch_port, task_id=resolved_task_id),
        "conflicted_port": conflicted_port,
        "recommended_command": recommended_command,
    }


def _format_dashboard_runtime_note(status: dict[str, Any]) -> str:
    state = str(status.get("status") or "")
    if state == "ready":
        return f"Dashboard：当前端口属于本仓库 · {status.get('url')}"
    if state == "foreign":
        return (
            "Dashboard：默认端口被其它仓库占用，不把它当作当前 AutoBCI 真源。\n"
            f"- 占用仓库：{status.get('server_repo_root') or '-'}\n"
            f"- 当前仓库建议：{status.get('recommended_command')}\n"
            f"- 建议地址：{status.get('recommended_url')}"
        )
    return (
        "Dashboard：当前未运行。\n"
        f"- 启动命令：{status.get('recommended_command')}\n"
        f"- 预期地址：{status.get('recommended_url')}"
    )


def _build_cli_status_snapshot(repo_root: Path, host: str, port: int) -> dict[str, Any]:
    paths = get_control_plane_paths(repo_root)
    snapshot = build_status_snapshot(paths)
    snapshot["dashboard_runtime"] = dashboard_runtime_status(repo_root, host, port)
    return snapshot


def _format_cli_status_summary(paths: Any, *, repo_root: Path, host: str, port: int) -> str:
    status = dashboard_runtime_status(repo_root, host, port)
    return format_status_summary(paths) + "\n" + _format_dashboard_runtime_note(status)


def run_dashboard_command(
    *,
    repo_root: Path,
    host: str,
    port: int,
    task_id: str | None = None,
    python_executable: str | None = None,
    popen_factory: Callable[..., subprocess.Popen[bytes] | object] = subprocess.Popen,
    browser_opener: Callable[[str], bool] = webbrowser.open,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> str:
    launch_port, conflicted_port = resolve_dashboard_launch_port(repo_root, host, port)
    url = build_dashboard_url(host, launch_port, task_id=task_id)
    if is_tui_test_mode_enabled():
        return f"dashboard test-mode dry-run：{url}"
    if conflicted_port == launch_port and is_dashboard_running(host, launch_port):
        return f"dashboard 端口被其它仓库占用：{host}:{launch_port}，请换 --port 后重试"
    if not is_dashboard_running(host, launch_port):
        command = [
            python_executable or sys.executable,
            str(repo_root / "scripts" / "serve_dashboard.py"),
            "--host",
            host,
            "--port",
            str(launch_port),
        ]
        popen_factory(
            command,
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached_process_kwargs(),
        )
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if is_dashboard_running(host, launch_port):
                break
            sleep_fn(0.1)
        else:
            return f"dashboard 启动失败：{url}"
    browser_opener(url)
    suffix = f"（{port} 被其它仓库占用，已改用 {launch_port}）" if conflicted_port else ""
    return f"dashboard 已打开：{url}{suffix}"


def _intake_agent_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "enum": [
                    "reply",
                    "draft_program",
                    "draft_proposal",
                    "draft_amendment",
                    "read_status",
                    "open_dashboard",
                    "report_latest",
                    "plan_autoresearch",
                    "run_bare_probe",
                ],
            },
            "message": {"type": "string"},
            "normalized_request": {"type": "string"},
            "reason": {"type": "string"},
            "raw_reasoning": {"type": "string"},
            "reasoning_summary": {"type": "string"},
        },
        "required": ["tool_name", "message", "normalized_request", "reason"],
        "additionalProperties": False,
    }


def _intake_agent_history_context(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        text = " ".join(str(item.get("text") or "").split())
        if role not in {"user", "intake"} or not text:
            continue
        rows.append(
            {
                "role": role,
                "text": text[:1200] + ("..." if len(text) > 1200 else ""),
                "intent_kind": str(item.get("intent_kind") or "").strip(),
                "created_at": str(item.get("created_at") or "").strip(),
            }
        )
    return rows[-INTAKE_AGENT_CONTEXT_HISTORY_LIMIT:]


def _intake_agent_pending_context(pending_action: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pending_action, dict):
        return {}
    context: dict[str, Any] = {
        "user_intent_kind": str(pending_action.get("user_intent_kind") or ""),
        "proposed_action": str(pending_action.get("proposed_action") or ""),
        "plan_mode": bool(pending_action.get("plan_mode")),
        "plan_status": str(pending_action.get("plan_status") or ""),
        "plan_summary": str(pending_action.get("plan_summary") or ""),
        "normalized_request": str(pending_action.get("normalized_request") or ""),
    }
    for key in ("open_questions", "discussion_notes"):
        value = pending_action.get(key)
        if isinstance(value, list):
            context[key] = [str(item) for item in value[-8:]]
    draft = _pending_program_draft(pending_action)
    if isinstance(draft, dict):
        goal = draft.get("research_goal") if isinstance(draft.get("research_goal"), dict) else {}
        metrics = draft.get("metrics") if isinstance(draft.get("metrics"), dict) else {}
        context["program_draft"] = {
            "program_id": str(draft.get("program_id") or ""),
            "status": str(draft.get("status") or ""),
            "task_type": str(goal.get("task_type") or draft.get("task_type") or ""),
            "statement": str(goal.get("statement") or ""),
            "primary_metric": str(metrics.get("primary") or draft.get("primary_metric") or ""),
        }
    return context


def _coerce_agent_tool_to_intent(agent_output: dict[str, Any], command_text: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(agent_output.get("tool_name") or "reply").strip()
    normalized = str(agent_output.get("normalized_request") or command_text).strip() or command_text
    message = str(agent_output.get("message") or "").strip()
    explicit_program_request = _looks_like_program_draft_request(command_text)
    if tool_name == "draft_program" and not explicit_program_request:
        return {
            "recognized": True,
            "user_intent_kind": "intake_chat",
            "normalized_request": normalized,
            "target_scope": "intake",
            "proposed_action": "reply",
            "command_preview": "",
            "requires_confirmation": False,
            "result_status": "continued",
            "summary": "用户仍在讨论，尚未明确要求生成 Program。",
            "agent_message": (
                "我先把这当作研究讨论，不生成 Program 草案。"
                "你可以继续否决、换方向或补充约束；等你说“现在生成 Program”时我再写草案。"
            ),
            "raw_reasoning": str(agent_output.get("raw_reasoning") or ""),
            "reasoning_summary": str(agent_output.get("reasoning_summary") or agent_output.get("reason") or ""),
        }
    if tool_name in {"reply", "plan_autoresearch", "run_bare_probe"}:
        intent_kind = "intake_chat" if tool_name == "reply" else tool_name
        return {
            "recognized": True,
            "user_intent_kind": intent_kind,
            "normalized_request": normalized,
            "target_scope": "intake",
            "proposed_action": tool_name,
            "command_preview": "",
            "requires_confirmation": False,
            "result_status": "continued",
            "summary": str(agent_output.get("reason") or "研究计划助手继续对话。"),
            "agent_message": message,
            "raw_reasoning": str(agent_output.get("raw_reasoning") or ""),
            "reasoning_summary": str(agent_output.get("reasoning_summary") or agent_output.get("reason") or ""),
        }
    intent_kind_by_tool = {
        "draft_program": "draft_program",
        "draft_proposal": "draft_proposal",
        "draft_amendment": "draft_amendment",
        "read_status": "read_status",
        "open_dashboard": "open_dashboard",
        "report_latest": "report_latest",
    }
    intent = classify_user_turn(
        {
            "draft_program": "program ",
            "draft_proposal": "propose ",
            "draft_amendment": "amend ",
        }.get(tool_name, "")
        + normalized,
        snapshot,
    )
    intent["user_intent_kind"] = intent_kind_by_tool.get(tool_name, str(intent.get("user_intent_kind") or "intake_chat"))
    intent["proposed_action"] = intent["user_intent_kind"]
    intent["normalized_request"] = normalized
    intent["summary"] = str(agent_output.get("reason") or action_summary_for_intent(intent["user_intent_kind"]))
    intent["raw_reasoning"] = str(agent_output.get("raw_reasoning") or "")
    intent["reasoning_summary"] = str(agent_output.get("reasoning_summary") or agent_output.get("reason") or "")
    if message:
        intent["agent_message"] = message
    if intent["user_intent_kind"] == "draft_program":
        intent["program_draft"] = classify_user_turn("program " + normalized, snapshot).get("program_draft")
    return intent


def _validate_intake_agent_output(payload: dict[str, Any]) -> None:
    schema = _intake_agent_output_schema()
    required = [str(item) for item in schema.get("required", [])]
    missing = [field for field in required if not str(payload.get(field) or "").strip()]
    if missing:
        raise RuntimeError(f"计划/对话模型 JSON 缺少字段：{', '.join(missing)}")
    allowed = set(schema["properties"]["tool_name"]["enum"])
    tool_name = str(payload.get("tool_name") or "").strip()
    if tool_name not in allowed:
        raise RuntimeError(f"计划/对话模型 JSON tool_name 不支持：{tool_name}")


def action_summary_for_intent(intent_kind: object) -> str:
    labels = {
        "read_status": "查看当前研究态",
        "open_dashboard": "打开运行态投影",
        "report_latest": "读取最新摘要",
        "draft_program": "生成 Program 草案",
        "draft_proposal": "生成候选研究草案",
        "draft_amendment": "生成 Program Amendment 草案",
        "plan_autoresearch": "用 AutoResearch 方法论制定计划",
        "run_bare_probe": "准备 bare run 探针",
        "intake_chat": "继续计划对话",
    }
    return labels.get(str(intent_kind), str(intent_kind or "继续计划对话"))


def _resolve_intake_agent_provider_model() -> tuple[str, str]:
    try:
        provider_config = importlib.import_module("bci_autoresearch.providers.config")
        load_provider_config = getattr(provider_config, "load_provider_config", None)
        resolve_agent_provider_model = getattr(provider_config, "resolve_agent_provider_model", None)
        loaded_config = load_provider_config() if callable(load_provider_config) else {}
        has_explicit_config = bool(loaded_config) or any(
            os.environ.get(name)
            for name in (
                "AUTOBCI_INTAKE_PROVIDER",
                "AUTOBCI_INTAKE_MODEL",
                "AUTOBCI_DEFAULT_PROVIDER",
                "AUTOBCI_DEFAULT_MODEL",
            )
        )
        if not has_explicit_config:
            raise RuntimeError("计划/对话模型未配置：请先用 /model 设置 provider、model 和 API key。")
        if callable(resolve_agent_provider_model):
            payload = resolve_agent_provider_model("intake")
            if isinstance(payload, dict):
                provider = str(payload.get("provider") or "").strip()
                model = str(payload.get("model") or "").strip()
                if provider and model:
                    return provider, model
        raise RuntimeError("计划/对话模型配置不完整：缺少 provider 或 model。")
    except Exception as exc:
        raise RuntimeError(f"计划/对话模型配置无效：{exc}") from exc
    provider = str(os.environ.get("AUTOBCI_INTAKE_PROVIDER") or os.environ.get("AUTOBCI_DEFAULT_PROVIDER") or "").strip()
    model = str(os.environ.get("AUTOBCI_INTAKE_MODEL") or os.environ.get("AUTOBCI_DEFAULT_MODEL") or DEFAULT_INTAKE_AGENT_MODEL).strip()
    if not provider or not model:
        raise RuntimeError("计划/对话模型未配置：缺少 provider 或 model。")
    return (provider, model)


def run_codex_intake_agent_turn(
    command_text: str,
    snapshot: dict[str, Any],
    *,
    repo_root: Path,
    conversation_history: list[dict[str, Any]] | None = None,
    pending_action: dict[str, Any] | None = None,
    timeout_seconds: float = DEFAULT_INTAKE_AGENT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    schema = _intake_agent_output_schema()
    provider, model = _resolve_intake_agent_provider_model()
    history_context = _intake_agent_history_context(conversation_history)
    pending_context = _intake_agent_pending_context(pending_action)
    prompt = "\n".join(
        [
            "你是 AutoBCI 的研究计划助手，专门帮助用户把研究任务推进成可验证、可审计的 Program。",
            "AutoResearch 是你可以调用的一套研究工具箱和方法论，不是用户正在填写的线性管线。你要根据上下文自主选择工具，而不是按关键词路由。",
            "AutoBCI 的公开主线是通用 BCI 研究闭环，尤其关注严格因果的脑电、运动学、时序和跨试次验证；不要默认把所有任务解释成某个历史示例任务。",
            "即使用户提到一个看起来完整的研究方向，也要先当作讨论。只有用户明确说“生成 Program / 写 Program / 重写 Program / 按当前版本生成”时，才选择 draft_program。",
            "只有用户明确要求比较不同数据模态或使用多模态证据时，才把任务解释成 cross-modal。",
            "如果用户输入是“官方，90，没有”这类短答，必须结合最近计划对话和当前计划状态解释；不要把短答当成新任务，也不要用无关内置示例填空。",
            "对公开命名数据集，用户说“官方”通常表示官方数据集或官方数据划分；如果前文指标是准确率，用户只说“90”通常表示目标准确率 90%，不要默认解释成训练集占比，除非用户明确说 90/10、90% 训练或 train=90%。",
            "当已经获得数据集、任务类型、主要指标、官方划分或目标阈值、模型偏好等核心信息时，不要重复追问同一组问题；应总结已知内容，并提示用户可以说“现在生成 Program”。",
            "",
            "可用工具名：",
            "- reply: 寒暄、追问、澄清；当信息不足以形成 Program 时使用。",
            "- draft_program: 生成 Program 草案；只在用户明确拍板要求生成或重写 Program 时使用。",
            "- draft_proposal: 在已有研究契约内提出候选研究方向。",
            "- draft_amendment: 用户想改任务类型、数据划分、主指标、禁区等契约边界。",
            "- read_status: 用户问当前在跑什么或当前状态。",
            "- open_dashboard: 用户要打开 dashboard / 看板。",
            "- report_latest: 用户要最新报告或摘要。",
            "- plan_autoresearch: 使用 AutoResearch 方法论规划 assisted/bare 对照、候选搜索和复评，但不直接执行。",
            "- run_bare_probe: 用户明确要求准备从零 bare run 探针；若 Program 未冻结，应先解释需要冻结契约。",
            "",
            "边界：你不能直接启动实验；需要执行或冻结的动作只能让外层工具去做。旧 AutoResearch 状态只是背景，不自动成为当前实验。",
            "普通寒暄不要返回 help，要自然回应并邀请用户描述研究问题。",
            "用户询问当前状态、现在在跑什么、系统状态、模型配置、报告或 dashboard 时，必须选择对应工具，不要用 reply 自己概括状态。",
            "请只返回符合 JSON schema 的对象。",
            "",
            "JSON schema：",
            json.dumps(schema, ensure_ascii=False),
            "",
            "当前系统状态 JSON：",
            json.dumps(
                {
                    "program_state": snapshot.get("program_state"),
                    "autoresearch_status": snapshot.get("autoresearch_status"),
                    "current_track_id": snapshot.get("current_track_id"),
                },
                ensure_ascii=False,
                default=str,
            ),
            "",
            "当前计划状态 JSON：",
            json.dumps(pending_context, ensure_ascii=False, default=str),
            "",
            "最近计划对话 JSON：",
            json.dumps(history_context, ensure_ascii=False, default=str),
            "",
            "用户输入：",
            command_text,
        ]
    )
    runtime = importlib.import_module("bci_autoresearch.agent_runtime")
    runner = getattr(runtime, "run_json_task", None)
    task = {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "output_schema": schema,
        "repo_root": str(repo_root),
        "timeout_seconds": timeout_seconds,
        "task_name": "autobci_intake",
    }
    if callable(runner):
        try:
            parsed = runner(task, repo_root=repo_root)
        except TypeError:
            parsed = runner(
                prompt=prompt,
                output_schema=schema,
                repo_root=repo_root,
                timeout_seconds=timeout_seconds,
                model=model,
                provider=provider,
                task_name="autobci_intake",
            )
    else:
        runtime_cls = getattr(runtime, "AgentRuntime", None)
        if runtime_cls is None:
            raise RuntimeError("agent_runtime 缺少 run_json_task 或 AgentRuntime。")
        instance = runtime_cls(repo_root=repo_root)
        method = getattr(instance, "run_json_task", None) or getattr(instance, "json_task", None)
        if not callable(method):
            raise RuntimeError("AgentRuntime 缺少 JSON task 方法。")
        parsed = method(
            prompt=prompt,
            output_schema=schema,
            timeout_seconds=timeout_seconds,
            model=model,
            task_name="autobci_intake",
        )
    if isinstance(parsed, dict) and "json" in parsed and isinstance(parsed["json"], dict):
        parsed = parsed["json"]
    if not isinstance(parsed, dict):
        raise RuntimeError("计划/对话模型 returned non-object JSON")
    if parsed.get("ok") is False:
        raise RuntimeError(str(parsed.get("message") or parsed.get("error_code") or "计划/对话模型 failed"))
    _validate_intake_agent_output(parsed)
    return parsed


def run_intake_agent_turn(
    command_text: str,
    snapshot: dict[str, Any],
    *,
    repo_root: Path,
    use_model_agent: bool,
    conversation_history: list[dict[str, Any]] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if use_model_agent:
        try:
            agent_output = run_codex_intake_agent_turn(
                command_text,
                snapshot,
                repo_root=repo_root,
                conversation_history=conversation_history,
                pending_action=pending_action,
            )
            _validate_intake_agent_output(agent_output)
            return _coerce_agent_tool_to_intent(
                agent_output,
                command_text,
                snapshot,
            )
        except Exception as exc:
            return build_intake_agent_failure_intent(exc)
    return classify_user_turn(command_text, snapshot)


def build_intake_agent_failure_intent(exc: Exception) -> dict[str, Any]:
    message = str(exc).strip() or type(exc).__name__
    return {
        "recognized": True,
        "user_intent_kind": "intake_chat",
        "normalized_request": "",
        "target_scope": "intake",
        "proposed_action": "intake_model_error",
        "command_preview": "",
        "requires_confirmation": False,
        "result_status": "failed",
        "summary": "计划/对话模型调用失败。",
        "agent_backend": "model_error",
        "agent_error": type(exc).__name__,
        "agent_message": (
            f"计划/对话模型调用失败：{message}\n"
            "没有生成 Program，也不会调用本地替代逻辑。请先用 /model current 查看当前配置，"
            "再用 /model 设置可用 provider、model 和 API key。"
        ),
    }


def _plan_text_from_command(command_text: str) -> str:
    raw = str(command_text or "").strip()
    if raw.startswith("/"):
        raw = raw[1:]
    if raw.lower().startswith("plan"):
        return raw[len("plan") :].strip()
    return raw.strip()


def _handle_plan_command(
    parts: list[str],
    command_text: str,
    *,
    paths: Any,
    state: dict[str, Any],
    snapshot: dict[str, Any],
    repo_root: Path,
    use_model_agent: bool,
) -> tuple[dict[str, Any], bool, str, str, str]:
    pending = state.get("pending_action") if isinstance(state.get("pending_action"), dict) else None
    subcommand = str(parts[1]).lower() if len(parts) > 1 else ""
    if not subcommand:
        if _is_program_plan_action(pending):
            restored = dict(pending)
            restored["plan_mode"] = True
            restored["plan_status"] = "drafting"
            restored["requires_confirmation"] = False
            restored["result_status"] = "planning"
            state["pending_action"] = restored
            return restored, True, "已恢复 Program 起草。\n" + _format_program_plan_message(restored), "not_required", "planning"
        plan = _new_program_plan_action()
        state["pending_action"] = plan
        return plan, True, _format_program_plan_message(plan, opening=True), "not_required", "planning"
    if subcommand == "show":
        if not _is_program_plan_action(pending):
            intent = _plan_command_payload()
            return intent, False, "当前没有 Program。输入 /plan 进入起草。", "not_required", "rejected"
        return dict(pending), True, _format_program_plan_show(pending, paths), "not_required", "planning"
    if subcommand in {"revise", "edit", "change", "modify"}:
        if not _is_program_plan_action(pending):
            intent = _plan_command_payload()
            return intent, False, "当前没有 Program 可以修改。先生成 Program。", "not_required", "rejected"
        draft = _pending_program_draft(pending)
        if not isinstance(draft, dict):
            return dict(pending), False, "还没有生成 Program。先继续讨论，等你拍板后再生成。", "not_required", "rejected"
        state["selection_context"] = {
            "kind": "program_revision",
            "program_id": str(draft.get("program_id") or ""),
        }
        return (
            dict(pending),
            True,
            "直接输入你想改的地方，我会按你的意见更新 Program。输入“取消”可退出修改输入。",
            "not_required",
            "planning",
        )
    if subcommand in {"run", "start", "execute"}:
        if not _is_program_plan_action(pending):
            intent = _plan_command_payload()
            return intent, False, "当前没有 Program 可以运行。先生成 Program。", "not_required", "rejected"
        draft = _pending_program_draft(pending)
        if not isinstance(draft, dict):
            return dict(pending), False, "还没有生成 Program。先继续讨论，等你拍板后再生成。", "not_required", "rejected"
        try:
            validate_program_contract(draft)
        except ProgramContractError as exc:
            return dict(pending), False, f"Program 还不完整：{exc}", "not_required", "rejected"
        accepted = dict(pending)
        accepted.update(
            {
                "plan_mode": False,
                "plan_status": "accepted",
                "requires_confirmation": True,
                "result_status": "awaiting_confirmation",
                "summary": "Program 已确认，正在冻结并进入研究闭环。",
                "proposed_action": "draft_program",
            }
        )
        try:
            program_id, artifact_refs = freeze_program_from_intent(paths, accepted)
        except Exception as exc:
            return accepted, False, f"Program 冻结失败：{exc}", "approved", "failed"
        accepted["artifact_refs"] = artifact_refs
        state.pop("pending_action", None)
        trace = _format_action_trace(
            [
                _manual_action_event(
                    actor="Planner",
                    action="确认 Program",
                    summary=f"{program_id} 已通过 owner 确认。",
                    details=[
                        f"任务类型：{(draft.get('research_goal') or {}).get('task_type') or '-'}",
                        f"主指标：{draft.get('primary_metric') or '-'}",
                        f"计划版本：{accepted.get('revision') or 0}",
                    ],
                ),
                _manual_action_event(
                    actor="Program",
                    action="冻结 Program",
                    summary=f"{program_id} 已写入正式 Program。",
                    details=[
                        "执行沙盒未启动；研究闭环会先进入下一步确认。",
                        "产物：" + ", ".join(str(item) for item in artifact_refs[:3]) if artifact_refs else "产物：-",
                    ],
                ),
            ]
        )
        research_message = _open_research_step_gate(paths, state)
        return (
            accepted,
            True,
            f"已确认并冻结 Program：{program_id}\n\n{trace}\n\n{research_message}",
            "approved",
            "executed",
        )
    if subcommand == "accept":
        if not _is_program_plan_action(pending):
            intent = _plan_command_payload()
            return intent, False, "当前没有 Program 可以确认。输入 /plan 先开始起草。", "not_required", "rejected"
        draft = _pending_program_draft(pending)
        if not isinstance(draft, dict):
            return dict(pending), False, "当前还没有生成 Program，先描述任务、数据、标签和指标。", "not_required", "rejected"
        try:
            validate_program_contract(draft)
        except ProgramContractError as exc:
            return dict(pending), False, f"Program 还不完整：{exc}", "not_required", "rejected"
        accepted = dict(pending)
        accepted.update(
            {
                "plan_mode": False,
                "plan_status": "accepted",
                "requires_confirmation": True,
                "result_status": "awaiting_confirmation",
                "summary": "Program 已确认，等待 approve 冻结。",
                "proposed_action": "draft_program",
            }
        )
        state["pending_action"] = accepted
        trace = _format_action_trace(
            [
                _manual_action_event(
                    actor="Planner",
                    action="确认 Program",
                    summary=f"{draft.get('program_id') or '-'} 已转为待冻结草案。",
                    details=[
                        f"任务类型：{(draft.get('research_goal') or {}).get('task_type') or '-'}",
                        f"主指标：{draft.get('primary_metric') or '-'}",
                        f"计划版本：{accepted.get('revision') or 0}",
                    ],
                )
            ]
        )
        return (
            accepted,
            True,
            "Program 已确认，已转为待冻结草案。输入 /approve 冻结；输入 /cancel 取消。\n\n" + trace,
            "pending",
            "awaiting_confirmation",
        )
    if subcommand == "exit":
        if not _is_program_plan_action(pending):
            intent = _plan_command_payload()
            return intent, False, "当前没有 Program 可以暂停。", "not_required", "rejected"
        paused = dict(pending)
        paused.update({"plan_mode": False, "plan_status": "paused", "requires_confirmation": False, "result_status": "planning"})
        state["pending_action"] = paused
        return paused, True, "已暂停 Program 起草。草案已保留，输入 /plan 可继续。", "not_required", "planning"
    if subcommand == "cancel":
        if not _is_program_plan_action(pending):
            intent = _plan_command_payload()
            return intent, False, "当前没有 Program 可以取消。", "not_required", "rejected"
        cancelled = dict(pending)
        state.pop("pending_action", None)
        return cancelled, True, "已取消 Program 计划。", "cancelled", "cancelled"
    if subcommand == "reset":
        plan = _new_program_plan_action()
        state["pending_action"] = plan
        return plan, True, "已重置 Program 计划。继续描述任务即可。", "not_required", "planning"
    plan_text = _plan_text_from_command(command_text)
    if not plan_text:
        plan = dict(pending) if _is_program_plan_action(pending) else _new_program_plan_action()
        state["pending_action"] = plan
        return plan, True, _format_program_plan_message(plan, opening=not _pending_program_draft(plan)), "not_required", "planning"
    plan = _advance_program_plan(
        plan_text,
        snapshot,
        repo_root=repo_root,
        session_state=state,
        use_model_agent=use_model_agent,
    )
    state["pending_action"] = plan
    return plan, True, _format_program_plan_message(plan), "not_required", "planning"


def handle_command(
    command_text: str,
    *,
    repo_root: Path,
    host: str,
    port: int,
    python_executable: str | None = None,
    session_state: dict[str, Any] | None = None,
    use_model_agent: bool = False,
) -> tuple[bool, str]:
    command = _rewrite_lifecycle_natural_language(command_text.strip())
    if not command:
        return False, "请输入命令。"
    state = ensure_shell_session(session_state)
    paths = get_control_plane_paths(repo_root)
    active_snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, state)
    if (
        ((not use_model_agent and _looks_like_program_discussion_input(command)) or _looks_like_program_draft_request(command))
        and _should_create_default_program_plan(active_snapshot, state)
    ):
        state["pending_action"] = _new_program_plan_action()
        _sync_experiment_manifest(paths, state, snapshot=active_snapshot)
        active_snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, state)
    _refresh_next_actions(state, active_snapshot)
    gate_response = _handle_research_gate_response(paths, state, command)
    if gate_response is not None:
        return False, gate_response
    parts = shlex.split(command)
    if not parts:
        return False, "请输入命令。"

    turn_id = next_turn_id(state)
    state["_last_command_turn_id"] = turn_id
    action = parts[0].lower()
    program_revision = _program_revision_text(command, state)
    manual_model = None if program_revision is not None else _model_manual_text(command, state)
    data_path_input = None if program_revision is not None or manual_model is not None else _data_path_input_text(command, state)
    if program_revision is not None:
        if program_revision == "__cancel__":
            action = "plan_revision_cancel"
            parts = ["plan_revision_cancel"]
        else:
            action = "plan_revision"
            parts = ["plan_revision", program_revision]
    elif manual_model is not None:
        action = "model_manual_model"
        parts = ["model_manual_model", manual_model]
    elif data_path_input is not None:
        if data_path_input == "__cancel__":
            action = "data_cancel"
            parts = ["data_cancel"]
        else:
            action = "data_set"
            parts = ["data_set", data_path_input]
    else:
        director_selection_number = _director_selection_number(command, state)
        if director_selection_number is not None:
            action = "director_select"
            parts = ["director_select", str(director_selection_number)]
        else:
            model_selection_number = _model_selection_number(command, state)
            if model_selection_number is not None:
                action = "model_select"
                parts = ["model_select", str(model_selection_number)]
            else:
                selection_token = _project_switch_selection_token(command, state)
                if selection_token is not None:
                    action = "switch_select"
                    parts = ["switch_select", str(selection_token)]
                else:
                    next_action_number = _next_action_selection_number(command, state)
                    if next_action_number is not None:
                        resolved_command = _resolve_next_action_command(state, next_action_number)
                        if resolved_command is None:
                            return False, f"没有第 {next_action_number} 个下一步动作。"
                        command = resolved_command
                        parts = shlex.split(command)
                        action = parts[0].lower() if parts else ""
    if action.startswith("/") and len(action) > 1:
        action = action[1:]
        parts[0] = action
        command = " ".join(parts)
    if action == "run" and not (len(parts) >= 2 and parts[1].lower() == "smoke"):
        action = "research"
        parts[0] = "research"
        command = " ".join(parts)
    if action == "tasks":
        action = "switch"
        parts[0] = "switch"
        command = " ".join(parts)
    if action == "freeze":
        action = "approve"
        parts[0] = "approve"
        command = " ".join(parts)
    if action == "program" and len(parts) == 1:
        parts.append("show")
    if action in {"dataset", "data"}:
        message = _handle_data_direct_command(parts, repo_root, state)
        return False, message
    if action == "data_set":
        return False, _save_data_path_from_input(repo_root, state, str(parts[1] if len(parts) > 1 else ""))
    if action == "data_cancel":
        state.pop("selection_context", None)
        return False, "已取消数据目录选择。"
    if action == "theme":
        return (
            False,
            "配色命令已废弃：AutoBCI 不再维护 TUI。请使用 headless CLI、Dashboard 和 mobile gateway。",
        )
    if action == "model_select":
        return False, _handle_model_selection(state, int(parts[1]))
    if action == "model_manual_model":
        return False, _handle_model_manual_model(state, str(parts[1]))
    if action == "model":
        return False, _handle_model_direct_command(parts, state)
    if action == "director_select":
        return False, _handle_director_selection(paths, state, int(parts[1]))
    if action == "director":
        return False, _handle_director_direct_command(parts, paths, state)
    if action == "research":
        return False, _handle_research_direct_command(parts, paths, state)
    if action == "remote":
        return False, _handle_remote_direct_command(
            parts,
            paths=paths,
            session_state=state,
            repo_root=repo_root,
            host=host,
            port=port,
            python_executable=python_executable,
            use_model_agent=use_model_agent,
        )
    if action == "switch_select":
        return False, _resume_project_switch_option(paths, state, str(parts[1]))
    if action == "switch":
        mode = "default"
        args = [str(item).strip() for item in parts[1:] if str(item).strip()]
        if args and args[0].lower() in {"--debug", "debug"}:
            mode = "debug"
            args = args[1:]
        elif args and args[0].lower() in {"all", "--all"}:
            mode = "all"
            args = args[1:]
        if args:
            index_text = args[0]
            if not re.fullmatch(r"\d+(?:\.\d+)?", index_text):
                return False, "请输入要切换的任务编号，例如 /tasks 1 或 /tasks 1.2。"
            state["selection_context"] = {"kind": "topic_switch", "topics": _project_switch_options(paths, mode=mode), "mode": mode}
            return False, _resume_project_switch_option(paths, state, index_text)
        return False, _open_project_switcher(paths, state, mode=mode)
    active_snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, state)
    active_program = active_snapshot.get("program_state") if isinstance(active_snapshot.get("program_state"), dict) else {}
    record_transcript = action not in NON_TRANSCRIPT_ACTIONS
    if record_transcript:
        append_intake_history_turn(
            paths,
            state,
            turn_id=turn_id,
            role="user",
            text=command_text,
            intent_kind="pending",
            program_id=str(active_program.get("program_id") or ""),
            visibility="intake_only",
        )

    def _trace(
        intent: dict[str, Any],
        *,
        ok: bool,
        message: str,
        confirmation_result: str,
        artifact_refs: list[str] | None = None,
        result_status: str,
    ) -> None:
        trace_intent = dict(intent)
        trace_intent["reasoning_mode"] = _current_reasoning_mode(state)
        append_shell_trace(
            paths,
            session_id=str(state.get("session_id") or ""),
            turn_id=turn_id,
            intent=trace_intent,
            command_text=command_text,
            ok=ok,
            message=message,
            confirmation_result=confirmation_result,
            artifact_refs=artifact_refs,
            result_status=result_status,
        )
        _sync_experiment_manifest(paths, state, snapshot=active_snapshot, artifact_refs=artifact_refs)
        if record_transcript:
            append_intake_history_turn(
                paths,
                state,
                turn_id=turn_id,
                role="intake",
                text=message,
                intent_kind=str(trace_intent.get("user_intent_kind") or trace_intent.get("proposed_action") or ""),
                program_id=str(active_program.get("program_id") or trace_intent.get("program_id") or ""),
                refs=artifact_refs,
                visibility="intake_only",
            )

    if action == "reasoning":
        mode_arg = str(parts[1]).strip().lower() if len(parts) > 1 else ""
        ok = True
        if mode_arg:
            message = _set_reasoning_mode(state, mode_arg)
            ok = "不支持" not in message
        else:
            message = _format_reasoning_mode_message(state)
        _trace(
            {
                "user_intent_kind": "reasoning_control",
                "normalized_request": "reasoning " + (mode_arg or "show"),
                "target_scope": "tui_debug",
                "proposed_action": "reasoning_mode",
                "command_preview": "/reasoning",
                "requires_confirmation": False,
            },
            ok=ok,
            message=message,
            confirmation_result="not_required",
            result_status="executed" if ok else "rejected",
        )
        return False, message

    if action == "plan_revision_cancel":
        message = "已退出 Program 修改输入。"
        _trace(
            {
                "user_intent_kind": "draft_program",
                "normalized_request": "cancel program revision",
                "target_scope": "Program",
                "proposed_action": "program_revision_cancel",
                "command_preview": "/plan revise",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="planning",
        )
        return False, message
    if action == "plan_revision":
        revision_text = str(parts[1] if len(parts) > 1 else "").strip()
        state.pop("selection_context", None)
        intent = _advance_program_plan(
            revision_text,
            active_snapshot,
            repo_root=repo_root,
            session_state=state,
            use_model_agent=use_model_agent,
        )
        state["pending_action"] = intent
        message = "已按你的修改意见更新 Program。\n" + _format_program_plan_message(intent)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="planning",
        )
        return False, message
    if action == "plan":
        intent, ok, message, confirmation_result, result_status = _handle_plan_command(
            parts,
            command_text,
            paths=paths,
            state=state,
            snapshot=active_snapshot,
            repo_root=repo_root,
            use_model_agent=use_model_agent,
        )
        _trace(
            intent,
            ok=ok,
            message=message,
            confirmation_result=confirmation_result,
            artifact_refs=cast(list[str], intent.get("artifact_refs")) if isinstance(intent.get("artifact_refs"), list) else None,
            result_status=result_status,
        )
        return False, message
    if action == "quit":
        message = "AutoBCI 已退出。"
        _trace(
            {
                "user_intent_kind": "cancel_or_help",
                "normalized_request": "quit",
                "target_scope": "shell",
                "proposed_action": "quit",
                "command_preview": "quit",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="exited",
        )
        return True, message
    if action == "new":
        manifest = start_new_experiment_workspace(paths, state, archive_current=True, archive_reason="rotated_by_new")
        plan = _new_program_plan_action()
        state["pending_action"] = plan
        manifest["pending_action"] = plan
        _write_experiment_manifest(paths, manifest)
        _write_current_experiment(paths, manifest)
        message = f"已开始新的实验工作区和新的计划对话：{manifest['experiment_id']}。可以直接描述研究任务。"
        if len(parts) > 1 and parts[1].lower() == "clean":
            message = f"已全新开始 New Clean：{manifest['experiment_id']}。不会继承旧聊天、scratchpad 或旧 pending action；可以直接描述研究任务。"
        _trace(
            {
                "user_intent_kind": "session_control",
                "normalized_request": "new clean" if len(parts) > 1 and parts[1].lower() == "clean" else "new",
                "target_scope": "experiment_workspace",
                "proposed_action": "new_experiment",
                "command_preview": "/new",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "continue":
        manifest = _sync_experiment_manifest(paths, state, snapshot=active_snapshot)
        message = (
            f"继续当前项目：{manifest.get('title') or manifest.get('experiment_id')}。"
            "没有创建新的 Project，也没有继承额外旧上下文。"
        )
        _trace(
            {
                "user_intent_kind": "lifecycle_control",
                "normalized_request": "continue",
                "target_scope": "project",
                "proposed_action": "continue_project",
                "command_preview": "/continue",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "snapshot":
        manifest = _sync_experiment_manifest(paths, state, snapshot=active_snapshot)
        snapshot = lifecycle_create_snapshot(
            paths,
            project_id=str(manifest.get("project_id") or manifest.get("experiment_id")),
            title=str(manifest.get("title") or "当前快照"),
            session_id=str(manifest.get("intake_session_id") or ""),
            program_id=str(manifest.get("program_id") or ""),
            pending_action=manifest.get("pending_action") if isinstance(manifest.get("pending_action"), dict) else None,
            artifact_refs=list(manifest.get("artifact_refs") or []),
        )
        message = f"已保存快照：{snapshot['snapshot_id']}。之后可以 /fork {snapshot['snapshot_id']} 或 /resume {manifest.get('experiment_id')}。"
        _trace(
            {
                "user_intent_kind": "lifecycle_control",
                "normalized_request": "snapshot",
                "target_scope": "snapshot",
                "proposed_action": "save_snapshot",
                "command_preview": "/snapshot",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "fork":
        snapshot_id = str(parts[1] if len(parts) > 1 else "").strip()
        if not snapshot_id:
            message = "从这里分叉需要先有快照。请先输入 /snapshot 保存当前状态，再用返回的 snap id 执行 /fork snap-..."
            _trace(
                {
                    "user_intent_kind": "lifecycle_control",
                    "normalized_request": "fork",
                    "target_scope": "snapshot",
                    "proposed_action": "fork_project",
                    "command_preview": "/fork <snapshot_id>",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        start_new_intake_session(paths, state)
        try:
            forked = fork_project_from_snapshot(paths, snapshot_id, new_intake_session_id=str(state.get("intake_session_id") or ""))
        except Exception as exc:
            message = f"从快照分支失败：{exc}"
            _trace(
                {
                    "user_intent_kind": "lifecycle_control",
                    "normalized_request": f"fork {snapshot_id}",
                    "target_scope": "snapshot",
                    "proposed_action": "fork_project",
                    "command_preview": f"/fork {snapshot_id}",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="failed",
            )
            return False, message
        state["experiment_id"] = str(forked.get("project_id") or forked.get("experiment_id"))
        state.pop("pending_action", None)
        fork_manifest = _manifest_from_project(paths, forked)
        _write_experiment_manifest(paths, fork_manifest)
        _write_current_experiment(paths, fork_manifest)
        message = f"已从快照分支：{snapshot_id} -> {forked['project_id']}。新分支不会继承原始聊天全文或 scratchpad。"
        _trace(
            {
                "user_intent_kind": "lifecycle_control",
                "normalized_request": f"fork {snapshot_id}",
                "target_scope": "snapshot",
                "proposed_action": "fork_project",
                "command_preview": f"/fork {snapshot_id}",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "rename":
        raw = command_text.strip()
        raw = raw[1:] if raw.startswith("/") else raw
        lowered_raw = raw.lower()
        rename_topic = lowered_raw.startswith("rename topic")
        new_title = raw[len("rename topic") :].strip() if rename_topic else raw[len("rename") :].strip()
        if not new_title:
            message = "请输入新标题，例如 /rename 步态解码调试 #3，或 /rename topic 跨试次稳定性。"
            _trace(
                {
                    "user_intent_kind": "lifecycle_control",
                    "normalized_request": "rename",
                    "target_scope": "topic" if rename_topic else "attempt",
                    "proposed_action": "rename",
                    "command_preview": "/rename",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        manifest = _sync_experiment_manifest(paths, state, snapshot=active_snapshot)
        if rename_topic:
            topic_id = str(manifest.get("topic_id") or "").strip()
            if not topic_id:
                message = "当前尝试还没有归入 Topic；先让 Intake 形成 Program 草案后再重命名 Topic。"
                _trace(
                    {
                        "user_intent_kind": "lifecycle_control",
                        "normalized_request": "rename topic",
                        "target_scope": "topic",
                        "proposed_action": "rename_topic",
                        "command_preview": "/rename topic",
                        "requires_confirmation": False,
                    },
                    ok=False,
                    message=message,
                    confirmation_result="not_required",
                    result_status="rejected",
                )
                return False, message
            lifecycle_update_topic(paths, topic_id, topic_title=new_title, event_type="rename_topic")
            manifest["topic_title"] = new_title
            _write_experiment_manifest(paths, manifest)
            _write_current_experiment(paths, manifest)
            message = f"已重命名当前 Topic：{new_title}。"
        else:
            manifest["title"] = new_title
            manifest["attempt_title"] = new_title
            manifest["title_source"] = "manual_attempt"
            _write_experiment_manifest(paths, manifest)
            _write_current_experiment(paths, manifest)
            lifecycle_update_project(
                paths,
                str(manifest.get("experiment_id") or manifest.get("project_id") or ""),
                title=new_title,
                attempt_title=new_title,
                title_source="manual_attempt",
                event_type="rename_attempt",
            )
            message = f"已重命名当前尝试：{new_title}。"
        _trace(
            {
                "user_intent_kind": "lifecycle_control",
                "normalized_request": "rename topic" if rename_topic else "rename",
                "target_scope": "topic" if rename_topic else "attempt",
                "proposed_action": "rename",
                "command_preview": "/rename",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "title" and len(parts) > 1 and parts[1].lower() == "regenerate":
        manifest = _sync_experiment_manifest(paths, state, snapshot=active_snapshot, force_title=True)
        message = (
            f"已重新生成标题：Topic={manifest.get('topic_title') or '-'}；"
            f"Attempt={manifest.get('attempt_title') or manifest.get('title') or '-'}。Program 未修改。"
        )
        _trace(
            {
                "user_intent_kind": "lifecycle_control",
                "normalized_request": "title regenerate",
                "target_scope": "attempt",
                "proposed_action": "regenerate_title",
                "command_preview": "/title regenerate",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action in {"archive", "clear"}:
        if action == "archive" and len(parts) > 1 and str(parts[1]).lower() == "topic":
            manifest = _sync_experiment_manifest(paths, state, snapshot=active_snapshot)
            topic_id = str(manifest.get("topic_id") or "").strip()
            if not topic_id:
                message = "当前尝试还没有归入 Topic，无法归档整个 Topic。"
                _trace(
                    {
                        "user_intent_kind": "experiment_control",
                        "normalized_request": "archive topic",
                        "target_scope": "topic",
                        "proposed_action": "archive_topic",
                        "command_preview": "/archive topic",
                        "requires_confirmation": False,
                    },
                    ok=False,
                    message=message,
                    confirmation_result="not_required",
                    result_status="rejected",
                )
                return False, message
            topic = lifecycle_archive_topic(paths, topic_id)
            current_project = lifecycle_get_project(paths, str(manifest.get("experiment_id") or manifest.get("project_id") or ""))
            if current_project:
                topic_manifest = _manifest_from_project(paths, current_project)
                _write_experiment_manifest(paths, topic_manifest)
                _write_current_experiment(paths, topic_manifest)
            message = f"已归档 Topic：{topic.get('topic_title') or topic_id}。该 Topic 下的尝试都已标记为 archived。"
            _trace(
                {
                    "user_intent_kind": "experiment_control",
                    "normalized_request": "archive topic",
                    "target_scope": "topic",
                    "proposed_action": "archive_topic",
                    "command_preview": "/archive topic",
                    "requires_confirmation": False,
                },
                ok=True,
                message=message,
                confirmation_result="not_required",
                result_status="executed",
            )
            return False, message
        archived = _archive_current_experiment(paths, state, reason=action)
        manifest = start_new_experiment_workspace(paths, state, archive_current=False)
        plan = _new_program_plan_action()
        state["pending_action"] = plan
        manifest["pending_action"] = plan
        _write_experiment_manifest(paths, manifest)
        _write_current_experiment(paths, manifest)
        message = (
            f"已归档当前实验，并开始新的实验工作区：{manifest['experiment_id']}。可以直接描述研究任务。"
            if action == "clear"
            else f"已归档实验工作区：{archived['experiment_id']}，并开始新的实验工作区：{manifest['experiment_id']}。可以直接描述研究任务。"
        )
        topic_id = str(archived.get("topic_id") or "")
        if action == "archive" and topic_id and lifecycle_active_attempt_count(paths, topic_id) == 0:
            message += " 当前 Topic 已没有 active attempt；Topic 本身未自动归档。"
        _trace(
            {
                "user_intent_kind": "experiment_control",
                "normalized_request": action,
                "target_scope": "experiment_workspace",
                "proposed_action": action,
                "command_preview": f"/{action}",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action in {"experiments", "projects"}:
        lifecycle_message = format_lifecycle_projects_list(paths)
        message = lifecycle_message if action == "projects" else "实验工作区列表（Project 兼容视图）：\n" + "\n".join(lifecycle_message.splitlines()[1:])
        _trace(
            {
                "user_intent_kind": "experiment_control",
                "normalized_request": action,
                "target_scope": "experiment_workspace",
                "proposed_action": "list_projects",
                "command_preview": f"/{action}",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "reset":
        reset_scope = " ".join(parts[1:]).strip().lower()
        if reset_scope not in {"current run", "run"}:
            message = "当前只支持 /reset current run；它只清掉当前 run/pending 执行态，不删除历史 artifact。"
            _trace(
                {
                    "user_intent_kind": "lifecycle_control",
                    "normalized_request": f"reset {reset_scope}",
                    "target_scope": "run",
                    "proposed_action": "reset_current_run",
                    "command_preview": "/reset current run",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        manifest = _sync_experiment_manifest(paths, state, snapshot=active_snapshot)
        reset_project = lifecycle_reset_current_run(paths, str(manifest.get("project_id") or manifest.get("experiment_id")))
        state.pop("pending_action", None)
        reset_manifest = _manifest_from_project(paths, reset_project)
        _write_experiment_manifest(paths, reset_manifest)
        _write_current_experiment(paths, reset_manifest)
        message = "已重置当前 run：pending action 和当前 run 引用已清除，Project、Program 和历史 artifacts 保留。"
        _trace(
            {
                "user_intent_kind": "lifecycle_control",
                "normalized_request": "reset current run",
                "target_scope": "run",
                "proposed_action": "reset_current_run",
                "command_preview": "/reset current run",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "resume":
        experiment_id = str(parts[1] if len(parts) > 1 else "").strip()
        if not experiment_id:
            message = "请提供要恢复的实验工作区 id，例如 /resume exp-..."
            _trace(
                {
                    "user_intent_kind": "experiment_control",
                    "normalized_request": "resume",
                    "target_scope": "experiment_workspace",
                    "proposed_action": "resume_experiment",
                    "command_preview": "/resume <experiment_id>",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        try:
            manifest, missing_refs = resume_experiment_workspace(paths, state, experiment_id)
            message = f"已恢复实验工作区：{manifest['experiment_id']}。"
            if isinstance(manifest.get("pending_action"), dict):
                message += " 已恢复一个等待确认的动作，可以继续 approve 或 cancel。"
            if missing_refs:
                message += " 但有部分历史 artifact 缺失，需要重新生成或重新确认。"
        except Exception as exc:
            message = f"恢复实验工作区失败：{exc}"
            _trace(
                {
                    "user_intent_kind": "experiment_control",
                    "normalized_request": f"resume {experiment_id}",
                    "target_scope": "experiment_workspace",
                    "proposed_action": "resume_experiment",
                    "command_preview": f"/resume {experiment_id}",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="failed",
            )
            return False, message
        _trace(
            {
                "user_intent_kind": "experiment_control",
                "normalized_request": f"resume {experiment_id}",
                "target_scope": "experiment_workspace",
                "proposed_action": "resume_experiment",
                "command_preview": f"/resume {experiment_id}",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action in {"events", "details"}:
        state["show_events"] = True
        message = "已展开系统事件轨迹。"
        _trace(
            {
                "user_intent_kind": "read_events",
                "normalized_request": action,
                "target_scope": "messages",
                "proposed_action": "show_events",
                "command_preview": f"/{action}",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action in {"judge", "guard"}:
        message = _format_cli_status_summary(paths, repo_root=repo_root, host=host, port=port)
        _trace(
            {
                "user_intent_kind": "read_status",
                "normalized_request": action,
                "target_scope": action,
                "proposed_action": "read_status",
                "command_preview": f"/{action}",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "approve":
        pending = state.get("pending_action")
        if not isinstance(pending, dict):
            message = "当前没有等待确认的动作。"
            _trace(
                {
                    "user_intent_kind": "cancel_or_help",
                    "normalized_request": "approve",
                    "target_scope": "shell",
                    "proposed_action": "approve",
                    "command_preview": "",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        if _is_program_plan_action(pending) and str(pending.get("plan_status") or "") != "accepted":
            message = "当前仍在 Program 起草阶段。选择“确认并开始运行”，或输入 /plan accept 转成待冻结草案。"
            _trace(
                dict(pending),
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        intent = dict(pending)
        try:
            artifact_refs: list[str] = []
            if intent.get("user_intent_kind") == "draft_program":
                program_id, artifact_refs = freeze_program_from_intent(paths, intent)
                message = f"已冻结 Program：{program_id}"
            elif intent.get("user_intent_kind") == "draft_proposal":
                topic_id, artifact_refs = draft_proposal(paths, intent)
                message = f"已写入候选研究对象：{topic_id}"
            elif intent.get("user_intent_kind") == "draft_amendment":
                amendment_id, artifact_refs = draft_amendment(paths, intent)
                message = f"已写入 amendment 草案：{amendment_id}"
            elif intent.get("user_intent_kind") == "run_smoke":
                run_id, artifact_refs = launch_smoke(paths, intent, popen_factory=subprocess.Popen)
                message = f"已启动受控 smoke：{run_id}"
            else:
                raise ValueError("当前待确认动作无法执行。")
        except Exception as exc:
            message = f"approve 失败：{exc}"
            _trace(
                intent,
                ok=False,
                message=message,
                confirmation_result="approved",
                result_status="failed",
            )
            return False, message
        if intent.get("user_intent_kind") == "draft_program":
            draft = _pending_program_draft(intent)
            draft = draft if isinstance(draft, dict) else {}
            message += "\n\n" + _format_action_trace(
                [
                    _manual_action_event(
                        actor="Program",
                        action="冻结计划",
                        summary=f"{draft.get('program_id') or intent.get('program_id') or '-'} 已写入正式 Program。",
                        details=[
                            f"任务类型：{(draft.get('research_goal') or {}).get('task_type') or '-'}",
                            f"主指标：{draft.get('primary_metric') or '-'}",
                            "执行沙盒未启动；后续研究闭环仍需单独确认。",
                            "产物：" + ", ".join(str(item) for item in artifact_refs[:3]) if artifact_refs else "",
                        ],
                    )
                ]
            )
        elif artifact_refs:
            message += "\n\n" + _format_action_trace(
                [
                    _manual_action_event(
                        actor="Control Plane",
                        action="确认动作",
                        summary=str(intent.get("proposed_action") or intent.get("user_intent_kind") or "approve"),
                        details=["产物：" + ", ".join(str(item) for item in artifact_refs[:3])],
                    )
                ]
            )
        state.pop("pending_action", None)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="approved",
            artifact_refs=artifact_refs,
            result_status="executed",
        )
        return False, message
    if action == "cancel":
        pending = state.pop("pending_action", None)
        if not isinstance(pending, dict):
            message = "当前没有等待确认的动作。"
            _trace(
                {
                    "user_intent_kind": "cancel_or_help",
                    "normalized_request": "cancel",
                    "target_scope": "shell",
                    "proposed_action": "cancel",
                    "command_preview": "",
                    "requires_confirmation": False,
                },
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="rejected",
            )
            return False, message
        message = f"已取消等待确认的动作：{pending.get('proposed_action') or '-'}"
        _trace(
            dict(pending),
            ok=True,
            message=message,
            confirmation_result="cancelled",
            result_status="cancelled",
        )
        return False, message
    if action == "help":
        message = build_help_message()
        _trace(
            {
                "user_intent_kind": "cancel_or_help",
                "normalized_request": "help",
                "target_scope": "shell",
                "proposed_action": "help",
                "command_preview": "help",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="help",
        )
        return False, message
    if action == "status":
        message = _format_cli_status_summary(paths, repo_root=repo_root, host=host, port=port)
        _trace(
            {
                "user_intent_kind": "read_status",
                "normalized_request": "status",
                "target_scope": str(active_snapshot.get("current_track_id") or "runtime_status"),
                "proposed_action": "read_status",
                "command_preview": "autobci status",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "dashboard":
        message = run_dashboard_command(
            repo_root=repo_root,
            host=host,
            port=port,
            python_executable=python_executable,
        )
        _trace(
            {
                "user_intent_kind": "open_dashboard",
                "normalized_request": "dashboard",
                "target_scope": "dashboard",
                "proposed_action": "open_dashboard",
                "command_preview": "autobci dashboard",
                "requires_confirmation": False,
            },
            ok="启动失败" not in message,
            message=message,
            confirmation_result="not_required",
            artifact_refs=[f"http://{host}:{port}/"],
            result_status="executed" if "启动失败" not in message else "failed",
        )
        return False, message
    if action == "program" and len(parts) >= 2 and parts[1].lower() == "show":
        pending = state.get("pending_action") if isinstance(state.get("pending_action"), dict) else None
        if _is_program_plan_action(pending) and str(pending.get("plan_status") or "") != "accepted":
            message = _format_program_plan_show(pending, paths)
            _trace(
                {
                    "user_intent_kind": "read_program",
                    "normalized_request": "program show",
                    "target_scope": "Program",
                    "proposed_action": "plan_show",
                    "command_preview": "autobci program show",
                    "requires_confirmation": False,
                },
                ok=True,
                message=message,
                confirmation_result="not_required",
                result_status="executed",
            )
            return False, message
        pending_draft = _pending_program_draft(pending)
        if pending_draft:
            program_id = str(pending_draft.get("program_id") or "").strip()
            approve_path = str(paths.programs_dir / program_id / "Program.md") if program_id else ""
            message = _format_program_for_show(
                pending_draft,
                heading="当前 Program 草案（待确认）：",
                path_hint=f"{approve_path}（approve 后写入）" if approve_path else "approve 后写入",
            )
        else:
            program_state = active_snapshot.get("program_state") if isinstance(active_snapshot.get("program_state"), dict) else {}
            if program_state:
                message = _format_program_for_show(
                    program_state,
                    heading="当前 Program：",
                    path_hint=str(program_state.get("path") or "-"),
                )
            else:
                message = "当前 Program：尚未冻结，也没有待确认草案。"
        _trace(
            {
                "user_intent_kind": "read_program",
                "normalized_request": "program show",
                "target_scope": "Program",
                "proposed_action": "program_show",
                "command_preview": "autobci program show",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action == "propose":
        command = normalize_request(command[len("propose") :])
    if action == "amend":
        command = normalize_request(command[len("amend") :])
    if action == "run" and len(parts) >= 2 and parts[1].lower() == "smoke":
        command = "run smoke"
    if action == "report" and len(parts) >= 2 and parts[1].lower() == "latest":
        message = build_digest_summary(paths)
        _trace(
            {
                "user_intent_kind": "report_latest",
                "normalized_request": "report latest",
                "target_scope": "latest_report",
                "proposed_action": "report_latest",
                "command_preview": "autobci report latest",
                "requires_confirmation": False,
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if action in {"propose", "amend"} and not command:
        message = "请输入草案内容。"
        _trace(
            {
                "user_intent_kind": "cancel_or_help",
                "normalized_request": action,
                "target_scope": "shell",
                "proposed_action": action,
                "command_preview": action,
                "requires_confirmation": False,
            },
            ok=False,
            message=message,
            confirmation_result="not_required",
            result_status="rejected",
        )
        return False, message

    active_plan = state.get("pending_action") if isinstance(state.get("pending_action"), dict) else None
    if _is_active_program_plan(active_plan) and _looks_like_program_draft_request(command_text):
        intent = _advance_program_plan(
            command_text,
            active_snapshot,
            repo_root=repo_root,
            session_state=state,
            use_model_agent=use_model_agent,
        )
        if intent.get("result_status") == "failed":
            state.pop("pending_action", None)
            message = build_intake_chat_message(intent)
            _trace(
                intent,
                ok=False,
                message=message,
                confirmation_result="not_required",
                result_status="failed",
            )
            return False, message
        if not _is_program_plan_action(intent):
            state.pop("pending_action", None)
            if intent.get("user_intent_kind") == "intake_chat":
                message = build_intake_chat_message(intent)
                result_status = "continued"
            elif intent.get("user_intent_kind") in {"plan_autoresearch", "run_bare_probe"}:
                message = str(intent.get("agent_message") or "").strip()
                if not message:
                    message = (
                        "我会把 AutoResearch 当作研究工具箱来制定计划：先冻结 Program，"
                        "再设计 assisted / bare 对照、候选搜索和结果复核；这一步不会直接启动实验。"
                    )
                result_status = "continued"
            elif intent.get("requires_confirmation"):
                state["pending_action"] = dict(intent)
                message = build_confirmation_message(intent)
                result_status = str(intent.get("result_status") or "awaiting_confirmation")
            else:
                message = build_help_message()
                result_status = "help"
            _trace(
                intent,
                ok=True,
                message=message,
                confirmation_result="pending" if intent.get("requires_confirmation") else "not_required",
                result_status=result_status,
            )
            return False, message
        state["pending_action"] = intent
        message = _format_program_plan_message(intent)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="planning",
        )
        return False, message
    if (
        _is_active_program_plan(active_plan)
        and _looks_like_program_discussion_input(command_text)
        and not use_model_agent
    ):
        intent = _record_program_discussion(command_text, state)
        message = _format_program_discussion_message(intent)
        _trace(
            {
                "user_intent_kind": "program_discussion",
                "normalized_request": str(intent.get("normalized_request") or command_text),
                "target_scope": "Program",
                "proposed_action": "record_discussion",
                "command_preview": "生成 Program",
                "requires_confirmation": False,
                "summary": str(intent.get("summary") or "记录 Program 讨论。"),
            },
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="continued",
        )
        return False, message

    if _is_active_program_plan(active_plan) and use_model_agent and _looks_like_active_program_context_input(command_text):
        active_plan = _record_program_discussion(command_text, state)

    intent = run_intake_agent_turn(
        command,
        active_snapshot,
        repo_root=repo_root,
        use_model_agent=use_model_agent,
        conversation_history=read_current_intake_history(paths, limit=INTAKE_AGENT_CONTEXT_HISTORY_LIMIT),
        pending_action=active_plan,
    )
    intent = _apply_local_data_config_to_intent(intent, repo_root)
    if intent.get("user_intent_kind") == "cancel_or_help":
        message = build_help_message()
        _trace(
            intent,
            ok=False,
            message=message,
            confirmation_result="not_required",
            result_status="help",
        )
        return False, message
    if intent.get("user_intent_kind") == "intake_chat":
        message = build_intake_chat_message(intent)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="continued",
        )
        return False, message
    if intent.get("user_intent_kind") in {"plan_autoresearch", "run_bare_probe"}:
        message = str(intent.get("agent_message") or "").strip()
        if not message:
            if intent.get("user_intent_kind") == "plan_autoresearch":
                message = (
                    "我会把 AutoResearch 当作研究工具箱来制定计划：先冻结 Program，"
                    "再设计 assisted / bare 对照、候选搜索和结果复核；这一步不会直接启动实验。"
                )
            else:
                message = (
                    "bare run 需要先冻结 Program，然后再创建隔离 worktree / venv / 只读数据目录。"
                    "当前只准备探针方案，不会直接接管旧 AutoResearch。"
                )
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="continued",
        )
        return False, message
    if intent.get("requires_confirmation"):
        state["pending_action"] = dict(intent)
        message = build_confirmation_message(intent)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="pending",
            result_status=str(intent.get("result_status") or "awaiting_confirmation"),
        )
        return False, message
    if intent.get("user_intent_kind") == "read_status":
        result_body = _format_cli_status_summary(paths, repo_root=repo_root, host=host, port=port)
        message = build_direct_result_message(intent, result_body)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if intent.get("user_intent_kind") == "open_dashboard":
        result_body = run_dashboard_command(
            repo_root=repo_root,
            host=host,
            port=port,
            python_executable=python_executable,
        )
        message = build_direct_result_message(intent, result_body)
        _trace(
            intent,
            ok="启动失败" not in result_body,
            message=message,
            confirmation_result="not_required",
            artifact_refs=[f"http://{host}:{port}/"],
            result_status="executed" if "启动失败" not in result_body else "failed",
        )
        return False, message
    if intent.get("user_intent_kind") == "report_latest":
        result_body = build_digest_summary(paths)
        message = build_direct_result_message(intent, result_body)
        _trace(
            intent,
            ok=True,
            message=message,
            confirmation_result="not_required",
            result_status="executed",
        )
        return False, message
    if intent.get("result_status") == "rejected":
        message = (
            f"我理解你要做什么：{intent.get('summary') or '-'}\n"
            f"这会变成什么研究动作：{intent.get('proposed_action') or '-'}\n"
            "现在状态：已拒绝。"
        )
        _trace(
            intent,
            ok=False,
            message=message,
            confirmation_result="not_required",
            result_status="rejected",
        )
        return False, message
    message = build_help_message()
    _trace(
        intent,
        ok=False,
        message=message,
        confirmation_result="not_required",
        result_status="help",
    )
    return False, message


def _should_use_rich(*, input_fn: Callable[[str], str] | None, output: TextIO | None) -> bool:
    return bool(RICH_AVAILABLE and input_fn is None and output is None and sys.stdout.isatty())


def _run_plain_tui(
    *,
    repo_root: Path,
    host: str,
    port: int,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    python_executable: str | None = None,
) -> int:
    input_reader = input_fn or input
    output_stream = output or sys.stdout
    last_message = ""
    shell_session: dict[str, Any] = {}
    paths = get_control_plane_paths(repo_root)
    _ensure_experiment_workspace(paths, shell_session)
    _ensure_default_program_plan(paths, shell_session)
    while True:
        snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, shell_session)
        if getattr(output_stream, "isatty", lambda: False)():
            output_stream.write(CLEAR_SCREEN)
        output_stream.write(
            build_tui_screen(
                snapshot,
                last_message=last_message,
                session_history=read_current_intake_history(paths),
                pending_action=shell_session.get("pending_action")
                if isinstance(shell_session.get("pending_action"), dict)
                else None,
            )
        )
        output_stream.write(f"\n\n› {INTAKE_COMPOSER_PLACEHOLDER}\n› ")
        output_stream.flush()
        try:
            command = input_reader("")
        except EOFError:
            output_stream.write("\n")
            return 0
        inflight_turn = build_inflight_turn(command) if str(command or "").strip() else None
        if inflight_turn is not None:
            output_stream.write(f"\n{format_intake_activity_label(inflight_turn, 0)}\n")
            output_stream.flush()
        should_quit, last_message = handle_command(
            command,
            repo_root=repo_root,
            host=host,
            port=port,
            python_executable=python_executable,
            session_state=shell_session,
            use_model_agent=should_use_tui_model_agent(),
        )
        if should_quit:
            output_stream.write(f"{last_message}\n")
            output_stream.flush()
            return 0


def _run_rich_tui(
    *,
    repo_root: Path,
    host: str,
    port: int,
    python_executable: str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    _maybe_enable_readline()
    console = Console()
    last_message = ""
    last_command = ""
    shell_session: dict[str, Any] = {}
    paths = get_control_plane_paths(repo_root)
    _ensure_experiment_workspace(paths, shell_session)
    _ensure_default_program_plan(paths, shell_session)
    with Live(
        build_rich_startup_screen(),
        console=console,
        screen=True,
        auto_refresh=True,
        refresh_per_second=LIVE_REFRESH_PER_SECOND,
        transient=False,
        vertical_overflow="crop",
    ) as live:
        console.show_cursor(True)
        live.refresh()
        sleep_fn(0.0 if is_tui_test_mode_enabled() else 0.18)
        snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, shell_session)
        live.update(
            _build_rich_shell_layout(
                snapshot,
                last_message="已接入当前研究态。输入 help 查看命令。",
                last_command="",
                session_history=read_current_intake_history(paths),
            ),
            refresh=True,
        )
        sleep_fn(0.0 if is_tui_test_mode_enabled() else 0.08)
        while True:
            snapshot = _attach_experiment_state(build_status_snapshot(paths), paths, shell_session)
            live.update(
                _build_rich_shell_layout(
                    snapshot,
                    last_message=last_message,
                    last_command=last_command,
                    session_history=read_current_intake_history(paths),
                    pending_action=shell_session.get("pending_action")
                    if isinstance(shell_session.get("pending_action"), dict)
                    else None,
                ),
                refresh=True,
            )
            try:
                console.show_cursor(True)
                command = console.input(Text.assemble(("› ", f"bold {PALETTE['accent']}")))
            except (EOFError, KeyboardInterrupt):
                return 0
            last_command = command.strip()
            inflight_turn = build_inflight_turn(command) if last_command else None
            if inflight_turn is not None:
                live.update(
                    _build_rich_shell_layout(
                        snapshot,
                        last_message="",
                        last_command=last_command,
                        session_history=read_current_intake_history(paths),
                        pending_action=shell_session.get("pending_action")
                        if isinstance(shell_session.get("pending_action"), dict)
                        else None,
                        inflight_turn=inflight_turn,
                    ),
                    refresh=True,
                )
            should_quit, last_message = handle_command(
                command,
                repo_root=repo_root,
                host=host,
                port=port,
                python_executable=python_executable,
                session_state=shell_session,
                use_model_agent=should_use_tui_model_agent(),
            )
            if should_quit:
                live.update(
                    _build_rich_shell_layout(
                        snapshot,
                        last_message=last_message,
                        last_command=last_command,
                        session_history=read_current_intake_history(paths),
                        pending_action=shell_session.get("pending_action")
                        if isinstance(shell_session.get("pending_action"), dict)
                        else None,
                    ),
                    refresh=True,
                )
                sleep_fn(0.12)
                return 0


def run_tui(
    *,
    repo_root: Path,
    host: str,
    port: int,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    python_executable: str | None = None,
) -> int:
    raise RuntimeError(
        "AutoBCI TUI has been retired. Use headless CLI commands such as "
        "`autobci status --json` or `autobci ask \"现在进展如何？\" --json`."
    )


def _format_headless_entry_help() -> str:
    return "\n".join(
        [
            "AutoBCI 是 headless 研究闭环 CLI，不再要求打开 TUI。",
            "",
            "常用机器入口：",
            "- autobci doctor --json",
            "- autobci status --json",
            "- autobci goal status --json",
            "- autobci perp status --json",
            "- autobci research-tree show --json",
            "- autobci model list --json",
            "- autobci model current --agent intake --json",
            "- autobci ask \"现在进展如何？\" --json",
            "",
            "模型配置：",
            "- autobci model key minimax-cn",
            "- autobci model set --agent intake --provider minimax-cn --model MiniMax-M3",
            "- autobci model test minimax-cn --model MiniMax-M3 --json",
            "",
            "数据配置：",
            "- autobci data set /absolute/path/to/dataset",
            "",
            "手机/微信网关：让 Hermes、ClawBot 或其它 agent 调用上面的 CLI；不要再开启 TUI remote bridge。",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[3]
    if args.command is None:
        print(_format_headless_entry_help())
        return 0
    if args.command == "provider":
        code, message = handle_provider_command(args)
        print(message)
        return code
    if args.command == "model":
        code, message = handle_model_command(args)
        print(message)
        return code
    if args.command == "smoke":
        code, message = handle_smoke_command(args, repo_root=repo_root, host=args.host, port=args.port)
        print(message)
        return code
    if args.command == "doctor":
        report = build_doctor_report(repo_root=repo_root, host=args.host, port=args.port)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(format_doctor_report(report))
        return 0
    if args.command == "status":
        paths = get_control_plane_paths(repo_root)
        if args.json:
            print(json.dumps(_build_cli_status_snapshot(repo_root, args.host, args.port), ensure_ascii=False, indent=2))
        else:
            print(_format_cli_status_summary(paths, repo_root=repo_root, host=args.host, port=args.port))
        return 0
    if args.command == "goal":
        payload = _handle_goal_cli(args, repo_root=repo_root)
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_goal_status(payload))
        return 0
    if args.command == "perp":
        payload = _handle_perp_cli(args, repo_root=repo_root)
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_perp_status(payload))
        return 0
    if args.command == "research-tree":
        payload = _handle_research_tree_cli(args, repo_root=repo_root)
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_research_tree_status(payload))
        return 0
    if args.command == "data":
        action = str(args.data_action or "").strip().lower()
        if action == "set":
            parts = ["data", str(args.path)]
        else:
            parts = ["data", action]
        print(_handle_data_direct_command(parts, repo_root, {}))
        return 0
    if args.command == "storage" and args.storage_action == "audit":
        report = build_storage_optimization_report(
            repo_root,
            min_duplicate_bytes=int(args.min_duplicate_bytes),
            min_compressible_bytes=int(args.min_compressible_bytes),
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(_format_storage_audit_report(report))
        return 0
    if args.command == "ask":
        message = " ".join(str(part) for part in args.message).strip()
        should_quit, response = handle_command(
            message,
            repo_root=repo_root,
            host=args.host,
            port=args.port,
            session_state={},
            use_model_agent=bool(args.use_model_agent),
        )
        if args.json:
            print(
                json.dumps(
                    {"ok": True, "quit": bool(should_quit), "message": response},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(response)
        return 0
    if args.command == "dashboard":
        message = run_dashboard_command(repo_root=repo_root, host=args.host, port=args.port, task_id=args.task)
        print(message)
        return 0 if "启动失败" not in message else 1
    if args.command == "demo" and args.demo_action == "onsite":
        payload = run_onsite_demo_delivery(
            repo_root=repo_root,
            host=args.host,
            port=args.port,
            provider=args.provider,
            model=args.model,
            task_id=args.task,
            run_smoke=not args.skip_smoke,
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_onsite_demo_delivery(payload))
        return 0 if payload.get("ok") else 1
    if args.command == "windows" and args.windows_action == "doctor":
        report = build_doctor_report(repo_root=repo_root, host=args.host, port=args.port)
        if getattr(args, "json", False):
            payload = dict(report)
            payload["windows_target"] = True
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_doctor_report(report, windows_only=True))
        return 0
    if args.command == "linux" and args.linux_action == "doctor":
        report = build_doctor_report(repo_root=repo_root, host=args.host, port=args.port)
        if getattr(args, "json", False):
            payload = dict(report)
            payload["linux_target"] = True
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_doctor_report(report, linux_only=True))
        return 0
    print(_format_headless_entry_help())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
