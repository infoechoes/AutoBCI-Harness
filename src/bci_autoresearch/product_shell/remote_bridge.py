from __future__ import annotations

import html
import json
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from bci_autoresearch.control_plane.client_api import build_status_snapshot
from bci_autoresearch.control_plane.paths import AutoBciControlPlanePaths
from bci_autoresearch.control_plane.runtime_store import append_jsonl, read_json, read_jsonl, write_json_atomic


RemoteCommandCallback = Callable[[str, dict[str, Any]], tuple[bool, str]]


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return b""
    if length > 1_000_000:
        raise ValueError("request body too large")
    return handler.rfile.read(length)


def _extract_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "message", "command", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            if key == "content":
                parsed = _maybe_json(value)
                if isinstance(parsed, dict):
                    nested = _extract_text(parsed)
                    if nested:
                        return nested
            return value.strip()
    event = payload.get("event")
    if isinstance(event, dict):
        nested = _extract_text(event)
        if nested:
            return nested
        message = event.get("message")
        if isinstance(message, dict):
            nested = _extract_text(message)
            if nested:
                return nested
    message = payload.get("message")
    if isinstance(message, dict):
        nested = _extract_text(message)
        if nested:
            return nested
    return ""


def _extract_sender(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("sender", "user", "user_id", "open_id", "chat_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    event = payload.get("event")
    if isinstance(event, dict):
        sender = event.get("sender")
        if isinstance(sender, dict):
            for key in ("sender_id", "tenant_key"):
                value = sender.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    nested = value.get("open_id") or value.get("user_id") or value.get("union_id")
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
    return ""


def _maybe_json(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return None


def _parse_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    body = _read_body(handler)
    content_type = (handler.headers.get("Content-Type") or "").lower()
    if not body:
        return {}
    if "application/json" in content_type:
        parsed = _maybe_json(body.decode("utf-8", errors="replace"))
        return parsed if isinstance(parsed, dict) else {"text": str(parsed or "")}
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
        return {key: values[-1] if values else "" for key, values in parsed.items()}
    return {"text": body.decode("utf-8", errors="replace")}


def _tail_jsonl(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    rows = [row for row in read_jsonl(path) if isinstance(row, dict)]
    if limit <= 0:
        return rows
    return rows[-limit:]


def _html_page(*, title: str, post_url: str, events_url: str) -> bytes:
    escaped_title = html.escape(title)
    escaped_post_url = html.escape(post_url, quote=True)
    escaped_events_url = html.escape(events_url, quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escaped_title}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #111; color: #eee; }}
    main {{ max-width: 780px; margin: 0 auto; padding: 20px; }}
    textarea {{ width: 100%; min-height: 120px; box-sizing: border-box; background: #1c1c1c; color: #f3f3f3; border: 1px solid #555; border-radius: 8px; padding: 12px; font-size: 16px; }}
    button {{ margin-top: 10px; padding: 10px 16px; border-radius: 8px; border: 0; background: #d0aa6f; color: #111; font-weight: 700; }}
    pre {{ white-space: pre-wrap; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 12px; }}
    .muted {{ color: #aaa; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>AutoBCI Remote</h1>
    <p class="muted">当前 TUI 会话的手机远程入口。消息会进入同一个 Program / 研究流。</p>
    <textarea id="text" placeholder="输入消息，例如：现在进展如何？"></textarea>
    <br />
    <button id="send">发送</button>
    <button id="refresh">刷新进展</button>
    <h2>回复 / 进展</h2>
    <pre id="output">等待消息...</pre>
  </main>
  <script>
    async function send() {{
      const text = document.getElementById('text').value;
      const res = await fetch('{escaped_post_url}', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ text, source: 'mobile_web' }})
      }});
      document.getElementById('output').textContent = JSON.stringify(await res.json(), null, 2);
    }}
    async function refresh() {{
      const res = await fetch('{escaped_events_url}');
      document.getElementById('output').textContent = JSON.stringify(await res.json(), null, 2);
    }}
    document.getElementById('send').onclick = send;
    document.getElementById('refresh').onclick = refresh;
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>""".encode("utf-8")


@dataclass
class RemoteBridgeInfo:
    bind_host: str
    bind_port: int
    local_url: str
    lan_url: str
    token: str
    session_id: str
    experiment_id: str


class RemoteBridge:
    def __init__(
        self,
        *,
        paths: AutoBciControlPlanePaths,
        session_id: str,
        experiment_id: str,
        host: str,
        port: int,
        token: str,
        command_callback: RemoteCommandCallback,
    ) -> None:
        self.paths = paths
        self.session_id = session_id
        self.experiment_id = experiment_id
        self.token = token
        self.command_callback = command_callback
        self.remote_dir = paths.monitor_dir / "remote_bridge"
        self.inbox_path = self.remote_dir / "inbox.jsonl"
        self.outbox_path = self.remote_dir / "outbox.jsonl"
        self.session_path = self.remote_dir / "session.json"
        self._started_at = utc_now()
        self._httpd = ThreadingHTTPServer((host, int(port)), self._make_handler())
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def bind_host(self) -> str:
        return str(self._httpd.server_address[0])

    @property
    def bind_port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.bind_port}/?token={self.token}"

    @property
    def lan_url(self) -> str:
        host = _lan_ip() if self.bind_host in {"0.0.0.0", "::"} else self.bind_host
        return f"http://{host}:{self.bind_port}/?token={self.token}"

    def public_info(self, *, include_token: bool = False) -> dict[str, Any]:
        local_url = self.local_url if include_token else self.local_url.split("?token=", 1)[0]
        lan_url = self.lan_url if include_token else self.lan_url.split("?token=", 1)[0]
        info = {
            "enabled": True,
            "mode": "current_tui_session",
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "local_url": local_url,
            "lan_url": lan_url,
            "session_id": self.session_id,
            "experiment_id": self.experiment_id,
            "started_at": self._started_at,
            "token_hint": self.token[:6] + "...",
            "inbox_path": str(self.inbox_path),
            "outbox_path": str(self.outbox_path),
        }
        if include_token:
            info["token"] = self.token
        return info

    def start(self) -> None:
        self.remote_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="autobci-remote-bridge", daemon=True)
        self._thread.start()
        self._write_state(enabled=True)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._write_state(enabled=False)

    def _write_state(self, *, enabled: bool) -> None:
        info = self.public_info(include_token=False)
        info["enabled"] = enabled
        info["updated_at"] = utc_now()
        write_json_atomic(self.session_path, info)
        runtime = read_json(self.paths.runtime_state, {})
        runtime = runtime if isinstance(runtime, dict) else {}
        runtime["remote_bridge"] = info
        write_json_atomic(self.paths.runtime_state, runtime)

    def _authorized(self, parsed: urllib.parse.ParseResult, headers: Any) -> bool:
        query = urllib.parse.parse_qs(parsed.query)
        query_token = (query.get("token") or [""])[-1]
        header_token = headers.get("X-AutoBCI-Remote-Token") or ""
        auth = headers.get("Authorization") or ""
        bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        return self.token in {query_token, header_token, bearer}

    def _write_json_response(self, handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _write_html_response(self, handler: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handle_message(self, handler: BaseHTTPRequestHandler, parsed: urllib.parse.ParseResult) -> None:
        if not self._authorized(parsed, handler.headers):
            self._write_json_response(handler, 401, {"ok": False, "error": "unauthorized"})
            return
        try:
            payload = _parse_payload(handler)
            text = _extract_text(payload)
            sender = _extract_sender(payload) or "remote"
            if not text:
                self._write_json_response(handler, 400, {"ok": False, "error": "missing text"})
                return
            inbound = {
                "created_at": utc_now(),
                "session_id": self.session_id,
                "experiment_id": self.experiment_id,
                "source": "remote_http",
                "sender": sender,
                "text": text,
            }
            append_jsonl(self.inbox_path, inbound)
            should_quit, message = self.command_callback(text, {"sender": sender, "payload": payload})
            outbound = {
                "created_at": utc_now(),
                "session_id": self.session_id,
                "experiment_id": self.experiment_id,
                "source": "autobci",
                "request_text": text,
                "text": message,
                "should_quit": bool(should_quit),
            }
            append_jsonl(self.outbox_path, outbound)
            self._write_json_response(handler, 200, {"ok": True, "reply": message, "should_quit": bool(should_quit)})
        except Exception as exc:
            self._write_json_response(handler, 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _handle_events(self, handler: BaseHTTPRequestHandler, parsed: urllib.parse.ParseResult) -> None:
        if not self._authorized(parsed, handler.headers):
            self._write_json_response(handler, 401, {"ok": False, "error": "unauthorized"})
            return
        try:
            snapshot = build_status_snapshot(self.paths)
        except Exception:
            snapshot = {}
        research_loop = snapshot.get("research_loop") if isinstance(snapshot.get("research_loop"), dict) else {}
        self._write_json_response(
            handler,
            200,
            {
                "ok": True,
                "remote": self.public_info(include_token=False),
                "inbox": _tail_jsonl(self.inbox_path, 12),
                "outbox": _tail_jsonl(self.outbox_path, 12),
                "research_events": list(research_loop.get("recent_events") or [])[-12:],
                "program_state": snapshot.get("program_state") if isinstance(snapshot, dict) else {},
                "experiment_state": snapshot.get("experiment_state") if isinstance(snapshot, dict) else {},
            },
        )

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    bridge._write_json_response(self, 200, {"ok": True, "status": "running", "session_id": bridge.session_id})
                    return
                if parsed.path in {"/events", "/api/events"}:
                    bridge._handle_events(self, parsed)
                    return
                if parsed.path in {"/", "/index.html"}:
                    if not bridge._authorized(parsed, self.headers):
                        bridge._write_json_response(self, 401, {"ok": False, "error": "unauthorized"})
                        return
                    query = urllib.parse.urlparse(self.path).query
                    post_url = f"/message?{query}"
                    events_url = f"/events?{query}"
                    bridge._write_html_response(
                        self,
                        200,
                        _html_page(title="AutoBCI Remote", post_url=post_url, events_url=events_url),
                    )
                    return
                bridge._write_json_response(self, 404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path in {"/message", "/api/message"} or parsed.path.startswith("/webhook/"):
                    bridge._handle_message(self, parsed)
                    return
                bridge._write_json_response(self, 404, {"ok": False, "error": "not found"})

        return Handler


def current_remote_bridge(session_state: dict[str, Any]) -> RemoteBridge | None:
    bridge = session_state.get("_remote_bridge")
    return bridge if isinstance(bridge, RemoteBridge) else None


def start_remote_bridge(
    *,
    paths: AutoBciControlPlanePaths,
    session_state: dict[str, Any],
    host: str,
    port: int,
    command_callback: RemoteCommandCallback,
    token: str | None = None,
) -> RemoteBridge:
    existing = current_remote_bridge(session_state)
    if existing is not None:
        return existing
    bridge = RemoteBridge(
        paths=paths,
        session_id=str(session_state.get("session_id") or ""),
        experiment_id=str(session_state.get("experiment_id") or ""),
        host=host,
        port=port,
        token=token or secrets.token_urlsafe(18),
        command_callback=command_callback,
    )
    bridge.start()
    session_state["_remote_bridge"] = bridge
    return bridge


def stop_remote_bridge(session_state: dict[str, Any]) -> bool:
    bridge = current_remote_bridge(session_state)
    if bridge is None:
        return False
    bridge.stop()
    session_state.pop("_remote_bridge", None)
    return True
