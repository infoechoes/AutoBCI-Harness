from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from bci_autoresearch.control_plane.runtime_store import read_json, write_json_atomic


DEFAULT_TASK_ID = "rsvp_ship_image_only_v0"
DATA_PATHS_RELATIVE_PATH = Path(".autobci") / "data_paths.json"


def data_paths_config_path(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve() / DATA_PATHS_RELATIVE_PATH


def load_data_paths_config(repo_root: str | Path) -> dict[str, Any]:
    payload = read_json(data_paths_config_path(repo_root), {})
    if not isinstance(payload, dict):
        return {}
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        payload["tasks"] = {}
    return payload


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_user_path(raw_path: str, *, repo_root: str | Path) -> Path:
    text = str(raw_path or "").strip()
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        text = text[1:-1].strip()
    if text.startswith("file://"):
        text = text[len("file://") :]
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path(repo_root).expanduser().resolve() / path
    return path.resolve()


def save_task_dataset_root(
    repo_root: str | Path,
    dataset_root: str | Path,
    *,
    task_id: str = DEFAULT_TASK_ID,
    dataset_name: str | None = None,
    source: str = "user",
) -> dict[str, Any]:
    root = Path(dataset_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"数据目录不存在：{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"不是数据目录：{root}")
    config = load_data_paths_config(repo_root)
    tasks = config.setdefault("tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
        config["tasks"] = tasks
    record = {
        "task_id": task_id,
        "dataset_root": str(root),
        "dataset_name": dataset_name or root.name,
        "source": source,
        "updated_at": _utc_now(),
    }
    tasks[task_id] = record
    config.setdefault("version", 1)
    config["updated_at"] = record["updated_at"]
    write_json_atomic(data_paths_config_path(repo_root), config)
    return record


def clear_task_dataset_root(repo_root: str | Path, *, task_id: str = DEFAULT_TASK_ID) -> bool:
    config = load_data_paths_config(repo_root)
    tasks = config.get("tasks")
    if not isinstance(tasks, dict) or task_id not in tasks:
        return False
    tasks.pop(task_id, None)
    config["updated_at"] = _utc_now()
    write_json_atomic(data_paths_config_path(repo_root), config)
    return True


def _env_dataset_root(task_id: str) -> str:
    if task_id == DEFAULT_TASK_ID:
        value = os.environ.get("AUTOBCI_RSVP_SHIP_IMAGE_DATASET_ROOT")
        if value:
            return value
    return os.environ.get("AUTOBCI_DATASET_ROOT", "")


def configured_task_dataset(repo_root: str | Path, *, task_id: str = DEFAULT_TASK_ID) -> dict[str, Any] | None:
    env_value = _env_dataset_root(task_id).strip()
    if env_value:
        root = Path(env_value).expanduser()
        return {
            "task_id": task_id,
            "dataset_root": str(root),
            "dataset_name": root.name or "dataset",
            "source": "env",
        }
    config = load_data_paths_config(repo_root)
    tasks = config.get("tasks")
    if not isinstance(tasks, dict):
        return None
    record = tasks.get(task_id)
    return dict(record) if isinstance(record, dict) else None


def configured_dataset_root(repo_root: str | Path, *, task_id: str = DEFAULT_TASK_ID) -> Path | None:
    record = configured_task_dataset(repo_root, task_id=task_id)
    if not isinstance(record, dict):
        return None
    raw = str(record.get("dataset_root") or "").strip()
    return Path(raw).expanduser() if raw else None


def resolve_dataset_root(
    repo_root: str | Path,
    *,
    task_id: str = DEFAULT_TASK_ID,
    state: dict[str, Any] | None = None,
) -> Path | None:
    if isinstance(state, dict):
        raw = str(state.get("dataset_root") or "").strip()
        if raw:
            return Path(raw).expanduser()
    return configured_dataset_root(repo_root, task_id=task_id)


def require_dataset_root(
    repo_root: str | Path,
    *,
    task_id: str = DEFAULT_TASK_ID,
    state: dict[str, Any] | None = None,
) -> Path:
    root = resolve_dataset_root(repo_root, task_id=task_id, state=state)
    if root is None:
        raise FileNotFoundError(
            "RSVP 纯图像数据目录未配置。请运行 `autobci data set <path>`，"
            "或设置 AUTOBCI_RSVP_SHIP_IMAGE_DATASET_ROOT。"
        )
    return root.expanduser().resolve()


def apply_dataset_to_program_draft(repo_root: str | Path, draft: dict[str, Any]) -> dict[str, Any]:
    program_id = str(draft.get("program_id") or "").strip()
    if program_id != DEFAULT_TASK_ID:
        return draft
    record = configured_task_dataset(repo_root, task_id=program_id)
    if not isinstance(record, dict):
        return draft
    root = str(record.get("dataset_root") or "").strip()
    if not root:
        return draft
    patched = dict(draft)
    data_boundary = dict(patched.get("data_boundary") if isinstance(patched.get("data_boundary"), dict) else {})
    data_boundary["dataset_root"] = root
    data_boundary["dataset_name"] = str(record.get("dataset_name") or Path(root).name or data_boundary.get("dataset_name") or "dataset")
    data_boundary["local_config_source"] = str(DATA_PATHS_RELATIVE_PATH)
    patched["data_boundary"] = data_boundary
    uncertainties = list(patched.get("uncertainties") or [])
    uncertainties = [
        item
        for item in uncertainties
        if "下载目录" not in str(item) and "Downloads" not in str(item)
    ]
    uncertainties.insert(0, "已使用本地数据目录配置；正式运行前仍需审计图片、标签和数据划分。")
    patched["uncertainties"] = uncertainties
    return patched
