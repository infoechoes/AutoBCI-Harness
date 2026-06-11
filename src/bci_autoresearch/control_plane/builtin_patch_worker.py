from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable


GenerateJsonTask = Callable[[dict[str, Any]], dict[str, Any]]


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _normalize_repo_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _run_subprocess(
    args: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
        check=False,
    )


def _run_shell(command: str, *, cwd: Path, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def _cleanup_python_bytecode(worktree: Path, changed_paths: list[str]) -> list[str]:
    removed: list[str] = []
    roots = sorted({(worktree / path).parent for path in changed_paths})
    for root in roots:
        if not root.exists():
            continue
        for pycache in sorted(root.rglob("__pycache__")):
            relative = _normalize_repo_path(pycache.relative_to(worktree))
            if relative.startswith("data/raw/"):
                continue
            shutil.rmtree(pycache, ignore_errors=True)
            removed.append(relative)
        for pyc in sorted(root.rglob("*.pyc")):
            if not pyc.exists():
                continue
            relative = _normalize_repo_path(pyc.relative_to(worktree))
            if relative.startswith("data/raw/"):
                continue
            pyc.unlink()
            removed.append(relative)
    return removed


def _worker_error(
    message: str,
    *,
    exit_code: int = 1,
    provider: str | None = None,
    model: str | None = None,
    smoke: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "command": ["builtin_patch_worker"],
        "exit_code": exit_code,
        "stdout_tail": "",
        "stderr_tail": _tail(message),
        "provider": provider,
        "model": model,
        "smoke": smoke,
    }


def _default_generate_json_task(task: dict[str, Any]) -> dict[str, Any]:
    from bci_autoresearch.providers.service import generate_json_task

    resolved = _resolve_worker_provider_model()
    return generate_json_task(task, provider_name=resolved["provider"], model=resolved["model"])


def _missing_api_key_env(provider: str) -> str | None:
    from bci_autoresearch.providers.config import resolve_provider_api_key
    from bci_autoresearch.providers.presets import get_provider_preset

    preset = get_provider_preset(provider)
    if preset.api_key_env and not resolve_provider_api_key(preset.name, preset.api_key_env):
        return preset.api_key_env
    return None


def _resolve_worker_provider_model() -> dict[str, Any]:
    from bci_autoresearch.providers.config import load_provider_config, resolve_agent_provider_model

    config = load_provider_config()
    agents = config.get("agents")
    worker_cfg = agents.get("worker") if isinstance(agents, dict) else None
    worker_is_explicit = isinstance(worker_cfg, dict) and bool(worker_cfg.get("provider") or worker_cfg.get("model"))
    worker = dict(resolve_agent_provider_model("worker", config))
    worker["source_agent"] = "worker" if worker_is_explicit else "default"
    if worker_is_explicit:
        return worker
    try:
        intake = dict(resolve_agent_provider_model("intake", config))
    except Exception:
        return worker
    if _missing_api_key_env(str(intake.get("provider") or "")) is None:
        intake["agent"] = "worker"
        intake["source_agent"] = "intake"
        intake["live"] = False
        return intake
    return worker


def builtin_patch_worker_status() -> dict[str, Any]:
    try:
        resolved = _resolve_worker_provider_model()
        missing_env = _missing_api_key_env(str(resolved.get("provider") or ""))
        ok = missing_env is None
        return {
            "ok": ok,
            "status": "available" if ok else "missing_api_key",
            "provider": resolved.get("provider"),
            "model": resolved.get("model"),
            "agent": "worker",
            "source_agent": resolved.get("source_agent"),
            "missing_api_key_env": missing_env,
            "role": "built-in provider-backed patch worker for structure sandbox edit_code tracks",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "agent": "worker",
            "message": f"{type(exc).__name__}: {exc}",
            "role": "built-in provider-backed patch worker for structure sandbox edit_code tracks",
        }


def _safe_edit_path(worktree: Path, raw_path: Any, editable_files: list[str]) -> tuple[str, Path]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("edit path must be a non-empty relative path")
    raw = raw_path.strip().replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"edit path must be relative: {raw_path}")
    parts = PurePosixPath(raw).parts
    if ".." in parts:
        raise ValueError(f"edit path must not contain '..': {raw_path}")
    normalized = _normalize_repo_path(raw)
    if normalized.startswith("data/raw/"):
        raise ValueError(f"edit path is forbidden under data/raw/: {normalized}")
    allowed = {_normalize_repo_path(item) for item in editable_files}
    if normalized not in allowed:
        raise ValueError(f"edit path outside editable_files: {normalized}")
    return normalized, worktree / normalized


def _apply_one_edit(worktree: Path, edit: Any, editable_files: list[str]) -> dict[str, str]:
    if not isinstance(edit, dict):
        raise ValueError("each edit must be an object")
    normalized, path = _safe_edit_path(worktree, edit.get("path"), editable_files)
    if not path.exists():
        raise ValueError(f"editable file does not exist: {normalized}")
    original = path.read_text(encoding="utf-8")
    if isinstance(edit.get("content"), str):
        content = str(edit["content"])
        if content == original:
            return {"path": normalized, "operation": "write_file", "changed": "false"}
        path.write_text(content, encoding="utf-8")
        return {"path": normalized, "operation": "write_file", "changed": "true"}
    find = edit.get("find", edit.get("old"))
    replace = edit.get("replace", edit.get("new"))
    if not isinstance(find, str) or not isinstance(replace, str):
        raise ValueError(f"edit for {normalized} must contain string find/replace or content")
    count = original.count(find)
    if count != 1:
        raise ValueError(f"find text for {normalized} matched {count} times; expected exactly 1")
    updated = original.replace(find, replace, 1)
    if updated == original:
        return {"path": normalized, "operation": "replace", "changed": "false"}
    path.write_text(updated, encoding="utf-8")
    return {"path": normalized, "operation": "replace", "changed": "true"}


def _prompt_for_patch(track: dict[str, Any], editable_files: list[str], smoke_command: str, file_context: list[dict[str, str]]) -> str:
    schema = {
        "summary": "short explanation of the intended code change",
        "edits": [
            {
                "path": "one exact path from editable_files",
                "find": "exact existing text that appears once",
                "replace": "replacement text",
            }
        ],
        "commit_message": "short git commit message",
    }
    return "\n".join(
        [
            "You are AutoBCI's built-in patch worker inside an isolated git worktree.",
            "Return only valid JSON. Do not include markdown fences.",
            "Your job is to make the smallest code edit that advances the track.",
            "",
            "Hard boundaries:",
            f"- Only edit these files: {', '.join(editable_files)}",
            "- Do not touch data/raw/ or alignment/leakage rules.",
            "- Prefer exact find/replace edits. Use content only when replacing a whole allowed file is necessary.",
            f"- The harness will run this smoke command before accepting the commit: {smoke_command}",
            "",
            "Return this JSON shape:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "Track:",
            json.dumps(track, ensure_ascii=False, indent=2),
            "",
            "Editable file contents:",
            json.dumps(file_context, ensure_ascii=False, indent=2),
        ]
    )


def _build_task(worktree: Path, track: dict[str, Any], editable_files: list[str], smoke_command: str) -> dict[str, Any]:
    file_context: list[dict[str, str]] = []
    for relative in editable_files:
        normalized, path = _safe_edit_path(worktree, relative, editable_files)
        if not path.exists():
            raise ValueError(f"editable file does not exist: {normalized}")
        file_context.append({"path": normalized, "content": path.read_text(encoding="utf-8")})
    return {
        "task_name": "builtin_patch_worker",
        "temperature": 0.1,
        "prompt": _prompt_for_patch(track, editable_files, smoke_command, file_context),
        "schema": {
            "summary": "string",
            "edits": [{"path": "string", "find": "string", "replace": "string"}],
            "commit_message": "string",
        },
    }


def _commit_message(plan: dict[str, Any], track: dict[str, Any]) -> str:
    raw = str(plan.get("commit_message") or "").strip()
    if not raw:
        raw = f"builtin patch worker: {track.get('track_id') or 'edit-code'}"
    first_line = raw.splitlines()[0].strip()
    return first_line[:120] or "builtin patch worker edit"


def run_builtin_patch_worker(
    worktree: Path,
    track: dict[str, Any],
    editable_files: list[str],
    smoke_command: str,
    *,
    timeout: int,
    generate_json_task: GenerateJsonTask | None = None,
) -> dict[str, Any]:
    generator = generate_json_task or _default_generate_json_task
    provider: str | None = None
    model: str | None = None
    try:
        task = _build_task(worktree, track, editable_files, smoke_command)
        model_result = generator(task)
    except Exception as exc:
        return _worker_error(f"builtin patch worker provider unavailable: {type(exc).__name__}: {exc}", exit_code=127)

    if not isinstance(model_result, dict):
        return _worker_error("builtin patch worker provider returned a non-object result", exit_code=1)
    provider = str(model_result.get("provider") or "") or None
    model = str(model_result.get("model") or "") or None
    if not model_result.get("ok"):
        message = str(model_result.get("message") or model_result.get("error_code") or "provider call failed")
        return _worker_error(
            f"builtin patch worker provider unavailable: {message}",
            exit_code=127,
            provider=provider,
            model=model,
        )

    response = model_result.get("response")
    if not isinstance(response, dict):
        response = model_result.get("json")
    if not isinstance(response, dict):
        return _worker_error("builtin patch worker provider returned no JSON patch plan", provider=provider, model=model)
    plan = response
    edits = plan.get("edits")
    if not isinstance(edits, list) or not edits:
        return _worker_error("builtin patch worker patch plan contains no edits", provider=provider, model=model)

    try:
        applied = [_apply_one_edit(worktree, edit, editable_files) for edit in edits]
    except Exception as exc:
        return _worker_error(str(exc), provider=provider, model=model)

    changed = [item for item in applied if item.get("changed") == "true"]
    if not changed:
        return _worker_error("builtin patch worker made no file changes", provider=provider, model=model)

    changed_paths = sorted({item["path"] for item in changed})
    smoke_timeout = max(1, min(int(timeout or 120), 120))
    try:
        smoke = _run_shell(smoke_command, cwd=worktree, timeout=smoke_timeout)
    except subprocess.TimeoutExpired:
        return _worker_error(
            f"builtin patch worker smoke timed out after {smoke_timeout} seconds",
            exit_code=124,
            provider=provider,
            model=model,
        )
    if int(smoke.get("exit_code") or 0) != 0:
        return _worker_error(
            str(smoke.get("stderr_tail") or smoke.get("stdout_tail") or "builtin patch worker smoke failed"),
            provider=provider,
            model=model,
            smoke=smoke,
        )
    clean_result = _run_subprocess(["git", "clean", "-fdX"], cwd=worktree, timeout=120)
    if clean_result.returncode != 0:
        return _worker_error(_tail(clean_result.stderr or clean_result.stdout or "git clean ignored files failed"), provider=provider, model=model, smoke=smoke)
    removed_bytecode = _cleanup_python_bytecode(worktree, changed_paths)

    add_result = _run_subprocess(["git", "add", *changed_paths], cwd=worktree, timeout=120)
    if add_result.returncode != 0:
        return _worker_error(_tail(add_result.stderr or add_result.stdout or "git add failed"), provider=provider, model=model, smoke=smoke)
    commit_result = _run_subprocess(
        [
            "git",
            "-c",
            "user.email=autobci-worker@example.com",
            "-c",
            "user.name=AutoBCI Builtin Worker",
            "commit",
            "-m",
            _commit_message(plan, track),
        ],
        cwd=worktree,
        timeout=120,
    )
    if commit_result.returncode != 0:
        return _worker_error(_tail(commit_result.stderr or commit_result.stdout or "git commit failed"), provider=provider, model=model, smoke=smoke)
    commit = _run_subprocess(["git", "rev-parse", "HEAD"], cwd=worktree, timeout=120)
    summary = str(plan.get("summary") or "builtin patch worker applied JSON patch").strip()
    stdout_tail = "\n".join(
        [
            f"provider={provider or '-'} model={model or '-'}",
            f"summary={summary}",
            f"applied_edits={len(changed)}",
            f"smoke_exit={smoke['exit_code']}",
            f"clean_ignored_exit={clean_result.returncode}",
            f"removed_bytecode={len(removed_bytecode)}",
            f"commit={commit.stdout.strip() if commit.returncode == 0 else '-'}",
        ]
    )
    return {
        "command": ["builtin_patch_worker"],
        "exit_code": 0,
        "stdout_tail": _tail(stdout_tail),
        "stderr_tail": "",
        "provider": provider,
        "model": model,
        "summary": summary,
        "edits_applied": changed,
        "smoke": smoke,
        "clean_ignored": {
            "command": "git clean -fdX",
            "exit_code": clean_result.returncode,
            "stdout_tail": _tail(clean_result.stdout),
            "stderr_tail": _tail(clean_result.stderr),
        },
        "removed_bytecode": removed_bytecode,
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
    }
