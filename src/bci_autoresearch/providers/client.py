from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import resolve_provider_api_key
from .pi_runtime import PiRuntimeAdapter
from .presets import ProviderPreset


class ProviderError(RuntimeError):
    error_code = "provider_error"


class MissingProviderKey(ProviderError):
    error_code = "missing_api_key"

    def __init__(self, provider: str, env_name: str) -> None:
        super().__init__(f"{provider} requires {env_name}")
        self.provider = provider
        self.env_name = env_name


class UnsupportedProviderProtocol(ProviderError):
    error_code = "unsupported_protocol"


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ProviderError("provider_returned_non_object_json")
    return payload


def _task_timeout(task: dict[str, Any], default: float) -> float:
    raw = task.get("timeout_seconds")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class ProviderClient:
    preset: ProviderPreset
    model: str
    timeout_seconds: float = 30.0

    def generate_json(self, task: dict[str, Any]) -> dict[str, Any]:
        if self.preset.protocol == "pi":
            return PiRuntimeAdapter(
                preset=self.preset,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
            ).generate_json(task)
        if self.preset.protocol == "anthropic_compatible":
            return self._anthropic_compatible_json(task)
        if self.preset.protocol == "openai_compatible":
            return self._openai_compatible_json(task)
        raise UnsupportedProviderProtocol(f"Unsupported provider protocol: {self.preset.protocol}")

    def _openai_compatible_json(self, task: dict[str, Any]) -> dict[str, Any]:
        if not self.preset.api_key_env:
            raise MissingProviderKey(self.preset.name, "")
        api_key = resolve_provider_api_key(self.preset.name, self.preset.api_key_env)
        if not api_key:
            raise MissingProviderKey(self.preset.name, self.preset.api_key_env)
        timeout = _task_timeout(task, self.timeout_seconds)
        url = self.preset.base_url.rstrip("/") + "/chat/completions"
        prompt = str(task.get("prompt") or task.get("message") or "Return a JSON object.")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. Do not include markdown fences.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": float(task.get("temperature", 0.2)),
        }
        body.update(self.preset.extra_body)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"provider_http_error:{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError("provider_network_error") from exc
        content = payload["choices"][0]["message"]["content"]
        return _parse_json_text(str(content or ""))

    def _anthropic_compatible_json(self, task: dict[str, Any]) -> dict[str, Any]:
        if not self.preset.api_key_env:
            raise MissingProviderKey(self.preset.name, "")
        api_key = resolve_provider_api_key(self.preset.name, self.preset.api_key_env)
        if not api_key:
            raise MissingProviderKey(self.preset.name, self.preset.api_key_env)
        timeout = _task_timeout(task, self.timeout_seconds)
        url = self.preset.base_url.rstrip("/") + "/v1/messages"
        prompt = str(task.get("prompt") or task.get("message") or "Return a JSON object.")
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(task.get("max_tokens") or 1024),
            "system": str(task.get("system_prompt") or "Return only valid JSON. Do not include markdown fences."),
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
            "temperature": float(task.get("temperature", 0.2)),
        }
        body.update(self.preset.extra_body)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"provider_http_error:{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError("provider_network_error") from exc
        content = payload.get("content")
        if not isinstance(content, list):
            raise ProviderError("provider_missing_anthropic_content")
        text = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        return _parse_json_text(text)
