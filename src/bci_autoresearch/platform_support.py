from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Mapping, Any


def is_windows(platform_name: str | None = None) -> bool:
    name = (platform_name or platform.system()).lower()
    return name.startswith("win")


def _env_value(env: Mapping[str, str] | None, key: str) -> str:
    source = env if env is not None else os.environ
    return str(source.get(key) or "").strip()


def default_cache_root(
    *,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    current_platform = platform_name or platform.system()
    if is_windows(platform_name):
        base = _env_value(env, "LOCALAPPDATA") or _env_value(env, "APPDATA") or str(Path.home())
        return Path(base) / "AutoBci" / "session_cache"
    if current_platform == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AutoBci" / "session_cache"
    xdg_cache = _env_value(env, "XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "AutoBci" / "session_cache"
    return Path.home() / ".cache" / "AutoBci" / "session_cache"


def default_execution_worktrees_root(
    repo_root: Path,
    *,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    configured = _env_value(env, "AUTOBCI_EXECUTION_WORKTREES_ROOT")
    if configured:
        return Path(configured).expanduser()
    if is_windows(platform_name):
        base = _env_value(env, "LOCALAPPDATA") or _env_value(env, "APPDATA") or str(Path.home())
        return Path(base) / "AutoBci" / "worktrees"
    return Path(repo_root).resolve().parent / ".hermes-worktrees" / "autobci"


def detached_process_kwargs(*, platform_name: str | None = None) -> dict[str, Any]:
    if is_windows(platform_name):
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
    return {"start_new_session": True}


def venv_python_path(venv_dir: Path) -> Path:
    windows_python = venv_dir / "Scripts" / "python.exe"
    posix_python = venv_dir / "bin" / "python"
    if windows_python.exists():
        return windows_python
    if posix_python.exists():
        return posix_python
    return windows_python if is_windows() else posix_python
