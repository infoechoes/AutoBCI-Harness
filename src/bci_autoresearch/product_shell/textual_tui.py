from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog, Static, TextArea

from bci_autoresearch.control_plane import build_status_snapshot, get_control_plane_paths
from bci_autoresearch.product_shell import cli as shell_cli


TEXTUAL_THEMES = {
    "1": {
        "name": "Graphite",
        "base": "#101214",
        "surface": "#15191d",
        "top": "#1e2429",
        "panel": "#22282e",
        "tool": "#171b20",
        "line": "#6f7b86",
        "text": "#f5f2ea",
        "muted": "#b8b0a0",
        "accent": "#f2c46d",
        "ok": "#9dd9a6",
        "risk": "#ff9b85",
    },
    "2": {
        "name": "Forest",
        "base": "#07140f",
        "surface": "#0e2118",
        "top": "#143323",
        "panel": "#173d2a",
        "tool": "#10291d",
        "line": "#4fa36d",
        "text": "#ecfff4",
        "muted": "#9ed6b3",
        "accent": "#7df7a8",
        "ok": "#b8f39d",
        "risk": "#f7d06b",
    },
    "3": {
        "name": "Blueprint",
        "base": "#06192d",
        "surface": "#08223e",
        "top": "#0b3156",
        "panel": "#0d3d68",
        "tool": "#0b2f51",
        "line": "#3683b7",
        "text": "#eff9ff",
        "muted": "#a5d5ee",
        "accent": "#8ee8ff",
        "ok": "#8cffc4",
        "risk": "#ffd166",
    },
    "4": {
        "name": "Paper",
        "base": "#f4efe5",
        "surface": "#fbf7ee",
        "top": "#e7dcc9",
        "panel": "#efe6d8",
        "tool": "#fffaf0",
        "line": "#b08f68",
        "text": "#2a241d",
        "muted": "#756b5c",
        "accent": "#1e6f8f",
        "ok": "#2e7d5b",
        "risk": "#a35b28",
    },
    "5": {
        "name": "Violet",
        "base": "#171124",
        "surface": "#211733",
        "top": "#2a1d44",
        "panel": "#37265f",
        "tool": "#261b3d",
        "line": "#8c6bd8",
        "text": "#f7f0ff",
        "muted": "#c6b6e8",
        "accent": "#e7a6ff",
        "ok": "#8ef4d0",
        "risk": "#ffcf7a",
    },
}

TEXTUAL_BLUEPRINT_THEME = TEXTUAL_THEMES["3"]


def slash_menu_matches(text: str) -> list[str]:
    """Return first-level slash commands matching the current composer text."""

    lines = str(text or "").splitlines()
    query = lines[0].strip().lower() if lines else ""
    if not query.startswith("/"):
        return []
    if query == "/":
        return list(shell_cli.SLASH_MENU_COMMANDS)
    return [command for command in shell_cli.SLASH_MENU_COMMANDS if command.lower().startswith(query)]


class SlashComposerTextArea(TextArea):
    """Text area that gives slash picker navigation priority over cursor moves."""

    async def _on_key(self, event: Any) -> None:
        app = self.app
        key_text = str(getattr(event, "character", None) or getattr(event, "key", "") or "")
        if (
            len(key_text) == 1
            and key_text.isdigit()
            and hasattr(app, "_accept_selection_number")
            and app._accept_selection_number(key_text)  # type: ignore[attr-defined]
        ):
            event.stop()
            event.prevent_default()
            return
        if (
            event.key == "enter"
            and hasattr(app, "_should_accept_menu_on_enter")
            and app._should_accept_menu_on_enter(str(self.text or ""))  # type: ignore[attr-defined]
        ):
            event.stop()
            event.prevent_default()
            app._accept_slash_selection()  # type: ignore[attr-defined]
            return
        if event.key == "enter" and hasattr(app, "action_submit"):
            event.stop()
            event.prevent_default()
            app.action_submit()  # type: ignore[attr-defined]
            return
        await super()._on_key(event)

    def action_cursor_up(self, select: bool = False) -> None:  # type: ignore[override]
        app = self.app
        if hasattr(app, "_handle_slash_navigation") and app._handle_slash_navigation(-1):  # type: ignore[attr-defined]
            return
        super().action_cursor_up(select)

    def action_cursor_down(self, select: bool = False) -> None:  # type: ignore[override]
        app = self.app
        if hasattr(app, "_handle_slash_navigation") and app._handle_slash_navigation(1):  # type: ignore[attr-defined]
            return
        super().action_cursor_down(select)

    def action_delete_to_start_of_line(self) -> None:  # type: ignore[override]
        app = self.app
        if hasattr(app, "_slash_menu_visible") and app._slash_menu_visible():  # type: ignore[attr-defined]
            self.load_text("")
            app._hide_slash_menu()  # type: ignore[attr-defined]
            return
        super().action_delete_to_start_of_line()


class AutoBciTextualApp(App[None]):
    """Modern Textual shell for the AutoBCI research harness."""

    TITLE = "AutoBCI"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("ctrl+c", "quit", "退出"),
        ("ctrl+d", "quit", "退出"),
        ("enter", "submit", "发送"),
        ("ctrl+j", "newline", "换行"),
        ("ctrl+u", "clear_composer", "清空输入"),
        ("escape", "focus_composer", "输入"),
    ]

    CSS = f"""
    Screen {{
        background: {TEXTUAL_BLUEPRINT_THEME["base"]};
        color: {TEXTUAL_BLUEPRINT_THEME["text"]};
    }}

    #statusbar {{
        height: 3;
        padding: 0 1;
        background: {TEXTUAL_BLUEPRINT_THEME["top"]};
        color: {TEXTUAL_BLUEPRINT_THEME["text"]};
        border-bottom: solid {TEXTUAL_BLUEPRINT_THEME["line"]};
        text-style: bold;
    }}

    #main {{
        height: 1fr;
        padding: 1 2;
        background: {TEXTUAL_BLUEPRINT_THEME["surface"]};
        overflow-x: hidden;
        overflow-y: hidden;
    }}

    #feed {{
        width: 100%;
        height: 100%;
        padding: 0;
        background: {TEXTUAL_BLUEPRINT_THEME["surface"]};
        overflow-x: hidden;
        overflow-y: auto;
    }}

    #composer-shell {{
        height: auto;
        min-height: 5;
        background: {TEXTUAL_BLUEPRINT_THEME["base"]};
        padding: 0 1 1 1;
        border-top: solid {TEXTUAL_BLUEPRINT_THEME["line"]};
    }}

    #slash-menu {{
        display: none;
        height: auto;
        max-height: 10;
        margin: 0 0 1 0;
        padding: 0 1;
        background: {TEXTUAL_BLUEPRINT_THEME["panel"]};
        color: {TEXTUAL_BLUEPRINT_THEME["text"]};
        border: solid {TEXTUAL_BLUEPRINT_THEME["accent"]};
    }}

    #composer {{
        height: 4;
        min-height: 3;
        max-height: 8;
        background: {TEXTUAL_BLUEPRINT_THEME["tool"]};
        color: {TEXTUAL_BLUEPRINT_THEME["text"]};
        border: solid {TEXTUAL_BLUEPRINT_THEME["line"]};
    }}

    #secret-input {{
        display: none;
        height: 3;
        background: {TEXTUAL_BLUEPRINT_THEME["tool"]};
        color: {TEXTUAL_BLUEPRINT_THEME["text"]};
        border: solid {TEXTUAL_BLUEPRINT_THEME["accent"]};
    }}

    #slash-help {{
        height: auto;
        color: {TEXTUAL_BLUEPRINT_THEME["muted"]};
        padding: 0 1;
    }}
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        host: str,
        port: int,
        python_executable: str | None = None,
    ) -> None:
        super().__init__()
        self.repo_root = Path(repo_root)
        self.host = host
        self.port = port
        self.python_executable = python_executable
        self.paths = get_control_plane_paths(self.repo_root)
        self.shell_session: dict[str, Any] = {}
        self.snapshot: dict[str, object] = {}
        self.output_history: list[object] = []
        self.inflight_turn: dict[str, Any] | None = None
        self.ui_tick = 0
        self._command_lock = threading.RLock()
        self.slash_matches: list[str] = []
        self.slash_selected_index = 0
        self.selection_items: list[dict[str, str]] = []
        self.selection_selected_index = 0
        self.selection_context_signature = ""
        self.active_menu_kind: str | None = None
        self.theme_id = "3"
        self.palette = TEXTUAL_THEMES[self.theme_id]
        self.theme_selection_active = False

    def compose(self) -> ComposeResult:
        yield Static("AutoBCI", id="statusbar")
        with Container(id="main"):
            yield RichLog(id="feed", wrap=True, highlight=False, markup=False)
        with Container(id="composer-shell"):
            yield Static("", id="slash-menu")
            yield SlashComposerTextArea(
                "",
                id="composer",
                soft_wrap=True,
                show_line_numbers=False,
                placeholder="",
            )
            yield Input("", id="secret-input", password=True, placeholder="粘贴 API key，回车保存")
            yield Static("", id="slash-help")

    def on_mount(self) -> None:
        self._refresh_snapshot()
        initial = shell_cli._maybe_open_initial_model_setup(self.shell_session)
        if initial:
            self.output_history.append(shell_cli._make_output_history_entry(initial))
        else:
            self.output_history.append(shell_cli._make_output_history_entry("已接入当前研究态。"))
        self._apply_runtime_theme()
        self._render()
        self._update_input_mode()
        self._hide_slash_menu()
        self._sync_selection_picker()
        self.query_one("#slash-help", Static).update("Enter 发送 · Ctrl+J 换行 · / 打开常用命令")
        self.query_one("#composer", TextArea).focus()

    def action_focus_composer(self) -> None:
        if self._slash_menu_visible():
            self._cancel_active_selection()
            self._hide_slash_menu()
            return
        if self._secret_input_context() is not None:
            self.query_one("#secret-input", Input).focus()
        else:
            self.query_one("#composer", TextArea).focus()

    def action_newline(self) -> None:
        composer = self.query_one("#composer", TextArea)
        if composer.has_focus:
            self._hide_slash_menu()
            composer.insert("\n")

    def action_clear_composer(self) -> None:
        composer = self.query_one("#composer", TextArea)
        if composer.has_focus:
            composer.load_text("")
            self._hide_slash_menu()

    def action_submit(self) -> None:
        composer = self.query_one("#composer", TextArea)
        secret = self.query_one("#secret-input", Input)
        if secret.has_focus:
            self._submit_secret(secret.value)
            secret.value = ""
            return
        if not composer.has_focus:
            composer.focus()
            return
        if self._slash_menu_visible():
            self._accept_slash_selection()
            return
        text = str(composer.text or "").strip()
        if not text:
            return
        composer.load_text("")
        self._hide_slash_menu()
        self._submit_command(text)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "secret-input":
            self._submit_secret(event.value)
            event.input.value = ""

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "composer":
            return
        text = str(event.text_area.text or "")
        self._update_slash_menu(text)

    def _slash_menu_visible(self) -> bool:
        return self.active_menu_kind in {"slash", "selection"}

    def _should_accept_menu_on_enter(self, current_text: str = "") -> bool:
        if self.active_menu_kind == "slash":
            return True
        if self.active_menu_kind == "selection":
            return not str(current_text or "").strip()
        return False

    def _update_slash_menu(self, text: str) -> None:
        if self._secret_input_context() is not None:
            self._hide_slash_menu()
            return
        if str(text or "").strip() and self.active_menu_kind == "selection":
            self._cancel_active_selection()
        if not str(text or "").strip():
            self.slash_matches = []
            self._sync_selection_picker()
            return
        matches = slash_menu_matches(text)
        if not matches:
            self._hide_slash_menu()
            return
        self.slash_matches = matches
        self.slash_selected_index = min(self.slash_selected_index, len(matches) - 1)
        self.active_menu_kind = "slash"
        self._render_slash_menu()

    def _hide_slash_menu(self) -> None:
        self.slash_matches = []
        self.slash_selected_index = 0
        self.selection_items = []
        self.active_menu_kind = None
        menu = self.query_one("#slash-menu", Static)
        menu.styles.display = "none"
        menu.update("")
        self.query_one("#slash-help", Static).update("Enter 发送 · Ctrl+J 换行 · / 打开常用命令")

    def _cancel_active_selection(self) -> None:
        if self.active_menu_kind != "selection":
            return
        self.theme_selection_active = False
        if self._selection_context() is not None:
            self.shell_session.pop("selection_context", None)
        self.selection_items = []
        self.selection_selected_index = 0
        self.selection_context_signature = ""

    def _render_slash_menu(self) -> None:
        menu = self.query_one("#slash-menu", Static)
        menu.styles.display = "block"
        display_limit = 8
        visible_matches = self.slash_matches[:display_limit]
        text = Text()
        text.append("常用命令  ↑↓ 选择 · Enter 执行 · Esc 关闭\n", style=self.palette["muted"])
        for index, command in enumerate(visible_matches):
            help_text = shell_cli.SLASH_COMMAND_HELP.get(command, "")
            row = f"{'>' if index == self.slash_selected_index else ' '} {command:<18} {help_text}"
            style = (
                f"bold {self.palette['base']} on {self.palette['accent']}"
                if index == self.slash_selected_index
                else self.palette["text"]
            )
            text.append(row, style=style)
            if index != len(visible_matches) - 1:
                text.append("\n")
        if len(self.slash_matches) > display_limit:
            text.append(f"\n  还有 {len(self.slash_matches) - display_limit} 个命令，继续输入缩小范围", style=self.palette["muted"])
        menu.update(text)
        self.query_one("#slash-help", Static).update("↑↓ 选择命令 · Enter 执行 · Esc 关闭")

    def _handle_slash_navigation(self, delta: int) -> bool:
        if not self._slash_menu_visible():
            return False
        if self.active_menu_kind == "selection":
            if not self.selection_items:
                self._hide_slash_menu()
                return False
            self.selection_selected_index = (self.selection_selected_index + delta) % len(self.selection_items)
            self._render_selection_menu()
            return True
        self.slash_selected_index = (self.slash_selected_index + delta) % len(self.slash_matches)
        self._render_slash_menu()
        return True

    def _accept_slash_selection(self) -> None:
        if not self._slash_menu_visible():
            return
        if self.active_menu_kind == "selection":
            command = self.selection_items[self.selection_selected_index]["command"]
        else:
            command = self.slash_matches[self.slash_selected_index]
        composer = self.query_one("#composer", TextArea)
        composer.load_text("")
        self._hide_slash_menu()
        self._submit_command(command)

    def _sync_selection_picker(self) -> None:
        composer = self.query_one("#composer", TextArea)
        if self._secret_input_context() is not None or str(composer.text or "").strip():
            if self.active_menu_kind == "selection":
                self._hide_slash_menu()
            return
        items = self._build_selection_items()
        if not items:
            if self.active_menu_kind == "selection":
                self._hide_slash_menu()
            return
        signature = self._selection_signature(items)
        if signature != self.selection_context_signature:
            self.selection_selected_index = 0
            self.selection_context_signature = signature
        self.selection_items = items
        self.selection_selected_index = min(self.selection_selected_index, len(items) - 1)
        self.active_menu_kind = "selection"
        self._render_selection_menu()

    def _selection_signature(self, items: list[dict[str, str]]) -> str:
        context = self._selection_context() or {}
        if self.theme_selection_active:
            return f"theme_select:{self.theme_id}"
        kind = str(context.get("kind") or "")
        agent = str(context.get("agent") or "")
        provider = str(context.get("provider") or "")
        if not kind:
            kind = "next_actions"
        labels = "|".join(f"{item.get('command', '')}:{item.get('label', '')}" for item in items)
        return f"{kind}:{agent}:{provider}:{labels}"

    def _selection_context(self) -> dict[str, Any] | None:
        context = self.shell_session.get("selection_context")
        return context if isinstance(context, dict) else None

    def _build_selection_items(self) -> list[dict[str, str]]:
        if self.theme_selection_active:
            return [
                {
                    "command": f"/theme {theme_id}",
                    "label": str(theme.get("name") or theme_id),
                    "meta": "当前" if theme_id == self.theme_id else "",
                }
                for theme_id, theme in TEXTUAL_THEMES.items()
            ]
        context = self._selection_context()
        if context is None:
            return self._build_next_action_items()
        options = list(context.get("options") or [])
        if not options:
            return []
        kind = str(context.get("kind") or "")
        items: list[dict[str, str]] = []
        for index, option in enumerate(options, start=1):
            if not isinstance(option, dict):
                continue
            label, meta = self._selection_option_text(kind, option)
            items.append({"command": str(index), "label": label, "meta": meta})
        return items

    def _build_next_action_items(self) -> list[dict[str, str]]:
        view = self._view()
        actions = list(view.get("next_actions") or [])
        items: list[dict[str, str]] = []
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                continue
            command = str(action.get("command") or index).strip()
            label = str(action.get("label") or command).strip()
            meta = str(action.get("description") or action.get("note") or "").strip()
            if command and label:
                items.append({"command": command, "label": label, "meta": meta})
        return items

    def _selection_option_text(self, kind: str, option: dict[str, Any]) -> tuple[str, str]:
        action_labels = {
            "switch_intake": "切换当前模型",
            "configure_key": "配置 Provider API key",
            "test_provider": "测试 Provider",
            "list_models": "查看所有模型",
            "close": "返回",
        }
        action = str(option.get("action") or "").strip()
        if action:
            return action_labels.get(action, action), ""
        if kind == "model_agent_select":
            return str(option.get("label") or option.get("name") or "-"), str(option.get("note") or "")
        if kind in {"model_provider_select", "model_key_provider_select", "model_test_provider_select"}:
            name = str(option.get("name") or option.get("id") or "-").strip()
            display = str(option.get("display_name") or "").strip()
            label = f"{name} ({display})" if display and display.lower() != name.lower() else name
            model = str(option.get("model") or option.get("default_model") or "-")
            if bool(option.get("ready")):
                ready = "ready"
            else:
                missing = str(option.get("missing_api_key_env") or "").strip()
                ready = f"missing key:{missing}" if missing else "not ready"
            return label, f"{model} · {ready}"
        if kind == "model_model_select":
            if option.get("manual"):
                return "手动输入模型名", ""
            return str(option.get("model") or "-"), "选择后自动检测 key / 测试 provider"
        return str(option.get("label") or option.get("title") or option.get("name") or option.get("model") or "-"), str(
            option.get("description") or option.get("note") or ""
        )

    def _selection_title(self) -> str:
        if self.theme_selection_active:
            return "选择配色"
        context = self._selection_context() or {}
        kind = str(context.get("kind") or "")
        if not kind:
            return "下一步动作"
        titles = {
            "model_initial_setup": "首次模型配置",
            "model_menu": "模型设置",
            "model_agent_select": "选择模型用途",
            "model_provider_select": "选择 Provider",
            "model_model_select": "选择模型",
            "model_key_provider_select": "选择要配置 key 的 Provider",
            "model_test_provider_select": "选择要测试的 Provider",
            "director_menu": "研究方向调度",
        }
        return titles.get(kind, "选择")

    def _render_selection_menu(self) -> None:
        menu = self.query_one("#slash-menu", Static)
        menu.styles.display = "block"
        text = Text()
        text.append(f"{self._selection_title()}  ↑↓ 选择 · Enter 确认\n", style=self.palette["muted"])
        for index, item in enumerate(self.selection_items[:8]):
            label = item["label"]
            meta = item["meta"]
            row = f"{'>' if index == self.selection_selected_index else ' '} {index + 1}. {label:<28} {meta}".rstrip()
            style = (
                f"bold {self.palette['base']} on {self.palette['accent']}"
                if index == self.selection_selected_index
                else self.palette["text"]
            )
            text.append(row, style=style)
            if index != min(len(self.selection_items), 8) - 1:
                text.append("\n")
        if len(self.selection_items) > 8:
            text.append(f"\n  还有 {len(self.selection_items) - 8} 个选项。", style=self.palette["muted"])
        menu.update(text)
        self.query_one("#slash-help", Static).update("↑↓ 选择 · Enter 确认 · Esc 关闭")

    def _secret_input_context(self) -> dict[str, Any] | None:
        current = self.shell_session.get("secret_input")
        return current if isinstance(current, dict) else None

    def _submit_secret(self, secret_value: str) -> None:
        secret_input = self._secret_input_context()
        if secret_input is None:
            return
        provider = str(secret_input.get("provider") or "").strip().lower()
        payload = dict(secret_input)
        self.shell_session.pop("secret_input", None)
        self.output_history.append(shell_cli._make_output_history_entry(f"正在保存 {provider} API key…"))
        self._render()
        self._update_input_mode()

        def worker() -> None:
            try:
                message = shell_cli._save_provider_secret_from_input(
                    provider,
                    secret_value,
                    after_save_agent=str(payload.get("after_save_agent") or "").strip().lower() or None,
                    after_save_model=str(payload.get("after_save_model") or "").strip() or None,
                )
            except Exception as exc:  # pragma: no cover - defensive display path
                message = f"{provider} API key 保存失败：{type(exc).__name__}。"
            self.call_from_thread(self._finish_command, message, False)

        threading.Thread(target=worker, daemon=True).start()

    def _accept_selection_number(self, value: str) -> bool:
        if self.active_menu_kind != "selection" or not self.selection_items:
            return False
        try:
            index = int(value) - 1
        except ValueError:
            return False
        if index < 0 or index >= len(self.selection_items):
            return False
        self.selection_selected_index = index
        self._accept_slash_selection()
        return True

    def _handle_theme_command(self, command: str) -> bool:
        text = str(command or "").strip()
        token = text.lower()
        if self.theme_selection_active and token in {"n", "no", "返回", "退出", "exit", "cancel"}:
            self.theme_selection_active = False
            self.output_history.append(shell_cli._make_output_history_entry("已退出配色选择。"))
            self._render()
            return True
        if self.theme_selection_active and self._resolve_theme_id(token) is not None:
            self._apply_theme_id(self._resolve_theme_id(token) or self.theme_id)
            return True
        if not (token == "/theme" or token.startswith("/theme ")):
            if self.theme_selection_active and token.startswith("/"):
                self.theme_selection_active = False
            return False

        parts = text.split()
        if len(parts) == 1:
            self.theme_selection_active = True
            self._sync_selection_picker()
            self._update_input_mode()
            return True
        theme_id = self._resolve_theme_id(parts[1])
        if theme_id is None:
            self.theme_selection_active = True
            self.output_history.append(
                shell_cli._make_output_history_entry(
                    f"没有这个配色：{parts[1]}。\n\n{self._theme_menu_text()}"
                )
            )
            self._render()
            return True
        self._apply_theme_id(theme_id)
        return True

    def _resolve_theme_id(self, value: str) -> str | None:
        query = str(value or "").strip().lower()
        if query in TEXTUAL_THEMES:
            return query
        for theme_id, theme in TEXTUAL_THEMES.items():
            if query == str(theme.get("name") or "").strip().lower():
                return theme_id
        return None

    def _theme_menu_text(self) -> str:
        lines = ["选择配色，输入编号即可切换：", "", "```text"]
        for theme_id, theme in TEXTUAL_THEMES.items():
            current = "（当前）" if theme_id == self.theme_id else ""
            lines.append(f"{theme_id}. {theme['name']} {current}".rstrip())
        lines.append("```")
        lines.append("")
        lines.append("也可以直接输入 /theme 3。")
        return "\n".join(lines)

    def _apply_theme_id(self, theme_id: str) -> None:
        self.theme_id = theme_id
        self.palette = TEXTUAL_THEMES[theme_id]
        self.theme_selection_active = False
        self._apply_runtime_theme()
        self._hide_slash_menu()
        self.output_history.append(shell_cli._make_output_history_entry(f"配色已切换：{theme_id}. {self.palette['name']}"))
        self._render()
        self._update_input_mode()

    def _apply_runtime_theme(self) -> None:
        theme = self.palette
        self.styles.background = theme["base"]
        self.styles.color = theme["text"]
        status = self.query_one("#statusbar", Static)
        status.styles.background = theme["top"]
        status.styles.color = theme["text"]
        status.styles.border_bottom = ("solid", theme["line"])
        main = self.query_one("#main", Container)
        main.styles.background = theme["surface"]
        feed = self.query_one("#feed", RichLog)
        feed.styles.background = theme["surface"]
        feed.styles.color = theme["text"]
        shell = self.query_one("#composer-shell", Container)
        shell.styles.background = theme["base"]
        shell.styles.border_top = ("solid", theme["line"])
        menu = self.query_one("#slash-menu", Static)
        menu.styles.background = theme["panel"]
        menu.styles.color = theme["text"]
        menu.styles.border = ("solid", theme["accent"])
        composer = self.query_one("#composer", TextArea)
        composer.styles.background = theme["tool"]
        composer.styles.color = theme["text"]
        composer.styles.border = ("solid", theme["line"])
        secret = self.query_one("#secret-input", Input)
        secret.styles.background = theme["tool"]
        secret.styles.color = theme["text"]
        secret.styles.border = ("solid", theme["accent"])
        help_text = self.query_one("#slash-help", Static)
        help_text.styles.color = theme["muted"]

    def _submit_command(self, command: str) -> None:
        if self._handle_theme_command(command):
            return
        if self.inflight_turn is not None:
            self.output_history.append(shell_cli._make_output_history_entry("上一条消息还在处理，请稍等。"))
            self._render()
            return
        self.inflight_turn = shell_cli.build_inflight_turn(command)
        self._render()

        def worker() -> None:
            try:
                with self._command_lock:
                    should_quit, message = shell_cli.handle_command(
                        command,
                        repo_root=self.repo_root,
                        host=self.host,
                        port=self.port,
                        python_executable=self.python_executable,
                        session_state=self.shell_session,
                        use_model_agent=shell_cli.should_use_tui_model_agent(),
                    )
            except Exception as exc:  # pragma: no cover - defensive display path
                should_quit = False
                message = f"这一轮处理失败：{type(exc).__name__}。你的消息已经收到。"
            self.call_from_thread(self._finish_command, message, should_quit)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_command(self, message: str, should_quit: bool) -> None:
        turn_id = ""
        if isinstance(self.inflight_turn, dict):
            turn_id = str(self.inflight_turn.get("turn_id") or "")
        self.output_history.append(shell_cli._make_output_history_entry(message, turn_id=turn_id or None))
        self.output_history = self.output_history[-shell_cli.TUI_OUTPUT_HISTORY_LIMIT :]
        self.inflight_turn = None
        self._refresh_snapshot()
        self._render()
        self._update_input_mode()
        self._sync_selection_picker()
        if should_quit:
            self.exit()

    def _refresh_snapshot(self) -> None:
        self.snapshot = shell_cli._attach_experiment_state(
            build_status_snapshot(self.paths),
            self.paths,
            self.shell_session,
        )
        shell_cli._ensure_default_program_plan(self.paths, self.shell_session, snapshot=self.snapshot)
        self.snapshot = shell_cli._attach_experiment_state(
            build_status_snapshot(self.paths),
            self.paths,
            self.shell_session,
        )

    def _view(self) -> dict[str, Any]:
        pending_action = self.shell_session.get("pending_action")
        return shell_cli.build_intake_chat_view_model(
            self.snapshot,
            boot_mode=False,
            output_history=self.output_history,
            session_history=shell_cli.read_current_intake_history(self.paths),
            pending_action=pending_action if isinstance(pending_action, dict) else None,
            inflight_turn=self.inflight_turn,
            ui_tick=self.ui_tick,
        )

    def _render(self) -> None:
        self.ui_tick += 1
        view = self._view()
        self.query_one("#statusbar", Static).update(self._status_text(view))
        feed = self.query_one("#feed", RichLog)
        feed.clear()
        rows = list(view.get("transcript_rows") or [])
        audit_renderables = self._audit_renderables(view)
        self._write_top_padding(feed, rows, audit_renderables)
        for renderable in audit_renderables:
            feed.write(renderable, scroll_end=True)
        if not rows:
            feed.write(Text("描述你的研究任务，我会整理计划、风险和下一步判断。", style=self.palette["muted"]))
        for row in rows:
            if not isinstance(row, dict):
                continue
            feed.write(self._row_renderable(row), scroll_end=True)
        self._sync_selection_picker()

    def _write_top_padding(self, feed: RichLog, rows: list[object], audit_renderables: list[object]) -> None:
        feed_height = int(getattr(feed.size, "height", 0) or 0)
        if feed_height <= 0:
            app_height = int(getattr(self.size, "height", 0) or 0)
            feed_height = max(0, app_height - 8) if app_height else 20
        if feed_height <= 0:
            return
        estimated_lines = self._estimated_feed_lines(rows, audit_renderables)
        padding = max(0, feed_height - estimated_lines - 1)
        for _ in range(padding):
            feed.write(Text(" "))

    def _estimated_feed_lines(self, rows: list[object], audit_renderables: list[object]) -> int:
        if not rows:
            return 1 + len(audit_renderables)
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            total += self._estimated_row_lines(row)
        total += len(audit_renderables)
        return max(1, total)

    def _estimated_row_lines(self, row: dict[str, Any]) -> int:
        role = str(row.get("role") or "intake")
        text = str(row.get("text") or "")
        if role == "card" and isinstance(row.get("card"), dict):
            card = row["card"]
            row_count = len(list(card.get("rows") or []))
            missing = card.get("missing") if isinstance(card.get("missing"), list) else []
            return 1 + row_count + (1 if missing else 0)
        line_count = len(text.splitlines() or [""])
        if role in {"tool", "intake"}:
            line_count += 1
        return max(1, line_count)

    def _status_text(self, view: dict[str, Any]) -> Text:
        text = Text("AutoBCI · 研究控制台\n", style=f"bold {self.palette['accent']}")
        text.append(str(view.get("header_text") or "AutoBCI"), style=f"bold {self.palette['text']}")
        banner = str(view.get("benchmark_banner") or "").strip()
        if banner:
            text.append("\n" + banner, style=self.palette["risk"])
        return text

    def _row_renderable(self, row: dict[str, Any]) -> object:
        role = str(row.get("role") or "intake")
        text = str(row.get("text") or "")
        if role == "user":
            return Text(self._prefix_lines(text, "› "), style=f"bold {self.palette['accent']}")
        if role == "tool":
            return Group(
                Text("◆ 工具调用", style=f"bold {self.palette['risk']}"),
                Text(self._indent_lines(text), style=self.palette["muted"]),
            )
        if role == "card" and isinstance(row.get("card"), dict):
            return Group(
                Text("研究计划 / Program", style=f"bold {self.palette['ok']}"),
                self._program_table(row["card"]),
            )
        return Group(Text("·", style=f"bold {self.palette['ok']}"), Markdown(text))

    @staticmethod
    def _prefix_lines(text: str, prefix: str) -> str:
        lines = str(text or "").splitlines() or [""]
        return "\n".join(f"{prefix}{line}" for line in lines)

    @staticmethod
    def _indent_lines(text: str) -> str:
        lines = str(text or "").splitlines() or [""]
        return "\n".join(f"  {line}" for line in lines)

    def _program_table(self, card: dict[str, Any]) -> Table:
        table = Table.grid(expand=True)
        table.add_column(justify="right", style=self.palette["risk"], no_wrap=True)
        table.add_column(style=self.palette["text"])
        for label, value in list(card.get("rows") or []):
            table.add_row(f"{label}：", str(value))
        missing = card.get("missing") if isinstance(card.get("missing"), list) else []
        if missing:
            table.add_row("缺失：", "、".join(str(item) for item in missing))
        return table

    def _audit_renderables(self, view: dict[str, Any]) -> list[object]:
        renderables: list[object] = []
        event_items = list(view.get("system_event_items") or [])
        if event_items:
            event_lines = ["◆ 最近工具 / 判断"]
            for item in event_items[-4:]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("message_type") or "event")
                detail = str(item.get("detail") or "-")
                event_lines.append(f"  {title}: {detail}")
            renderables.append(Text("\n".join(event_lines), style=self.palette["muted"]))
        return renderables

    def _update_input_mode(self) -> None:
        composer = self.query_one("#composer", TextArea)
        secret = self.query_one("#secret-input", Input)
        secret_context = self._secret_input_context()
        if secret_context is None:
            composer.styles.display = "block"
            secret.styles.display = "none"
            composer.focus()
            return
        provider = str(secret_context.get("provider") or "provider")
        composer.styles.display = "none"
        secret.styles.display = "block"
        secret.placeholder = f"粘贴 {provider} API key，回车保存"
        secret.focus()


def run_textual_tui(
    *,
    repo_root: Path,
    host: str,
    port: int,
    python_executable: str | None = None,
) -> int:
    app = AutoBciTextualApp(
        repo_root=repo_root,
        host=host,
        port=port,
        python_executable=python_executable,
    )
    app.run()
    return 0
