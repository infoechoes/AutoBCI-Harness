from __future__ import annotations

from pathlib import Path
from typing import Any


RAW_PREFIX = "data/raw/"
SENSITIVE_TERMS = {
    "split",
    "alignment",
    "primary metric",
    "primary_metric",
    "canonical gate",
    "promote gate",
}
ALIGNMENT_OR_METRIC_TERMS = {"split", "alignment", "primary metric", "primary_metric"}


def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _in_allowed_dirs(path: str, allowed_dirs: list[str]) -> bool:
    scopes = [scope.replace("\\", "/").strip().strip("/") for scope in allowed_dirs if scope.strip()]
    return any(path == scope or path.startswith(f"{scope}/") for scope in scopes)


def check_runtime_safety(payload: dict[str, Any], *, allowed_dirs: list[str] | None = None) -> dict[str, Any]:
    allowed = allowed_dirs or [
        "src/bci_autoresearch/providers",
        "src/bci_autoresearch/agent_runtime",
        "tests",
    ]
    violations: list[dict[str, str]] = []
    for raw_path in payload.get("files_touched", []) or []:
        path = _normalize_path(raw_path)
        if path.startswith(RAW_PREFIX):
            violations.append({"code": "raw_data_forbidden", "path": path, "message": "raw data is read-only"})
        if not _in_allowed_dirs(path, allowed):
            violations.append({"code": "outside_allowed_dirs", "path": path, "message": "path is outside allowed dirs"})

    searchable = " ".join(
        str(payload.get(key, "")) for key in ("changes_summary", "why_this_change", "next_step", "message", "prompt")
    ).lower()
    matched = sorted(term for term in SENSITIVE_TERMS if term in searchable)
    for term in matched:
        violations.append({"code": "sensitive_term", "term": term, "message": "sensitive runtime term requires review"})
    if any(term in searchable for term in ALIGNMENT_OR_METRIC_TERMS):
        violations.append(
            {
                "code": "alignment_or_metric_sensitive",
                "message": "数据划分、alignment 或 primary metric 相关改动需要人工审查",
            }
        )
    return {"ok": not violations, "violations": violations}
