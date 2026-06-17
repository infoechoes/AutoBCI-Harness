from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bci_autoresearch.control_plane import build_status_snapshot, get_control_plane_paths
from bci_autoresearch.control_plane.research_loop import TASK_ID, status_research_loop


DASHBOARD_DIR = ROOT / "dashboard"


def build_dashboard_status() -> dict[str, Any]:
    paths = get_control_plane_paths(ROOT)
    snapshot = build_status_snapshot(paths)
    return {
        "ok": True,
        "repo_root": str(ROOT.resolve()),
        "server": {"repo_root": str(ROOT.resolve())},
        "status": snapshot,
        "research_control": snapshot.get("research_control") or {},
        "research_loop": status_research_loop(ROOT, task_id=TASK_ID),
    }


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _safe_dashboard_path(url_path: str) -> Path | None:
    if url_path in {"", "/"}:
        return DASHBOARD_DIR / "index.html"
    candidate = (DASHBOARD_DIR / url_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(DASHBOARD_DIR.resolve())
    except ValueError:
        return None
    return candidate


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AutoBCIHeadlessDashboard/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _send(self, status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url_path = urlsplit(self.path).path
        if url_path == "/api/status":
            self._send(HTTPStatus.OK, _json_bytes(build_dashboard_status()), content_type="application/json; charset=utf-8")
            return
        target = _safe_dashboard_path(url_path)
        if target is None or not target.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"Not found", content_type="text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), content_type=content_type)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serve_dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8878)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"AutoBCI dashboard: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
