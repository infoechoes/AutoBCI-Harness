from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import resolve_provider_api_key
from .presets import ProviderPreset


PI_RUNNER_ENV = "AUTOBCI_PI_RUNNER"


class PiRuntimeError(RuntimeError):
    error_code = "pi_runtime_error"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code:
            self.error_code = error_code


def _redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _runner_command() -> list[str]:
    override = str(os.environ.get(PI_RUNNER_ENV) or "").strip()
    if override:
        return [override]
    node = shutil.which("node")
    if not node:
        raise PiRuntimeError("Node.js is required for pi-ai runtime; install Node and run npm install.")
    runner = Path(__file__).with_name("pi_runner.mjs")
    return [node, str(runner)]


def _payload_json_from_runner(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PiRuntimeError("pi runner returned non-JSON stdout") from exc
    if not isinstance(payload, dict):
        raise PiRuntimeError("pi runner returned a non-object payload")
    return payload


@dataclass(frozen=True)
class PiRuntimeAdapter:
    preset: ProviderPreset
    model: str
    timeout_seconds: float = 30.0

    def generate_json(self, task: dict[str, Any]) -> dict[str, Any]:
        if not self.preset.pi_provider:
            raise PiRuntimeError(f"{self.preset.name} is missing pi_provider metadata.")
        if not self.preset.api_key_env:
            raise PiRuntimeError(f"{self.preset.name} is missing api_key_env metadata.")
        api_key = resolve_provider_api_key(self.preset.name, self.preset.api_key_env)
        if not api_key:
            from .client import MissingProviderKey

            raise MissingProviderKey(self.preset.name, self.preset.api_key_env)
        timeout = float(task.get("timeout_seconds") or self.timeout_seconds)
        prompt = str(task.get("prompt") or task.get("message") or "Return a JSON object.")
        request_payload = {
            "provider": self.preset.pi_provider,
            "providerName": self.preset.name,
            "model": self.model,
            "prompt": prompt,
            "systemPrompt": str(task.get("system_prompt") or "Return only valid JSON. Do not include markdown fences."),
            "temperature": float(task.get("temperature", 0.2)),
            "timeoutMs": int(timeout * 1000),
            "taskName": str(task.get("task_name") or task.get("taskName") or "json_task"),
        }
        if isinstance(task.get("output_schema"), dict):
            request_payload["outputSchema"] = task["output_schema"]
        env = os.environ.copy()
        env[self.preset.api_key_env] = api_key
        command = _runner_command()
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(request_payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout + 5.0,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PiRuntimeError("pi runner timed out") from exc
        except OSError as exc:
            raise PiRuntimeError(f"pi runner could not start: {exc}") from exc
        stderr = _redact(str(completed.stderr or "").strip(), [api_key])
        stdout = _redact(str(completed.stdout or "").strip(), [api_key])
        if completed.returncode != 0:
            message = stderr or stdout or f"pi runner exited with {completed.returncode}"
            raise PiRuntimeError(message)
        payload = _payload_json_from_runner(stdout)
        if not payload.get("ok", True):
            raise PiRuntimeError(
                str(payload.get("message") or payload.get("error_code") or "pi runner failed"),
                error_code=str(payload.get("error_code") or "pi_runtime_error"),
            )
        response = payload.get("json") or payload.get("response")
        if isinstance(response, dict):
            return response
        text = str(payload.get("text") or "").strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PiRuntimeError("pi runner returned text that is not valid JSON") from exc
            if isinstance(parsed, dict):
                return parsed
        raise PiRuntimeError("pi runner did not return a JSON object response")


def cli_main() -> int:
    from .presets import get_provider_preset

    payload = json.loads(sys.stdin.read() or "{}")
    preset = get_provider_preset(str(payload.get("providerName") or payload.get("provider") or ""))
    adapter = PiRuntimeAdapter(preset=preset, model=str(payload.get("model") or preset.default_model))
    result = adapter.generate_json(payload)
    print(json.dumps({"ok": True, "provider": preset.name, "model": adapter.model, "json": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - direct CLI helper for local debugging.
    raise SystemExit(cli_main())
