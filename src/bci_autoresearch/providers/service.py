from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import MissingProviderKey, ProviderClient, ProviderError, UnsupportedProviderProtocol
from .config import KNOWN_AGENT_NAMES
from .config import load_provider_config
from .config import resolve_agent_provider_model
from .config import resolve_default_provider
from .config import resolve_model
from .config import resolve_provider_api_key
from .config import set_agent_provider_model
from .pi_runtime import PiRuntimeError
from .presets import get_provider_preset, list_provider_presets


def _provider_status(name: str, config: dict[str, Any], *, default_provider: str) -> dict[str, Any]:
    preset = get_provider_preset(name)
    model = resolve_model(name, config, allow_global_default=(preset.name == default_provider))
    api_key = resolve_provider_api_key(preset.name, preset.api_key_env)
    missing_env = preset.api_key_env if preset.api_key_env and not api_key else None
    return {
        "name": preset.name,
        "display_name": preset.display_name or preset.name,
        "provider_runtime": "pi-ai" if preset.protocol == "pi" else preset.protocol,
        "protocol": preset.protocol,
        "pi_provider": preset.pi_provider,
        "base_url": preset.base_url,
        "api_key_env": preset.api_key_env,
        "default_model": preset.default_model,
        "aliases": list(preset.aliases),
        "model": model,
        "capabilities": list(preset.capabilities),
        "capability_profile": dict(preset.capability_profile),
        "extra_body": preset.extra_body,
        "ready": missing_env is None,
        "missing_api_key_env": missing_env,
    }


def list_provider_statuses(*, config_path: str | Path | None = None) -> dict[str, Any]:
    config = load_provider_config(config_path)
    provider = resolve_default_provider(config)
    config_errors: list[str] = []
    try:
        default_model = resolve_model(provider, config)
    except Exception as exc:
        default_model = ""
        config_errors.append(f"default provider {provider!r} is invalid: {exc}")
    agents: list[dict[str, Any]] = []
    for name in KNOWN_AGENT_NAMES:
        try:
            agents.append(resolve_agent_provider_model(name, config))
        except Exception as exc:
            agents.append(
                {
                    "agent": name,
                    "provider": "",
                    "model": "",
                    "live": name == "intake",
                    "error_code": "invalid_provider_config",
                    "message": str(exc),
                }
            )
    return {
        "ok": True,
        "default_provider": provider,
        "default_model": default_model,
        "config_errors": config_errors,
        "providers": [_provider_status(name, config, default_provider=provider) for name in list_provider_presets()],
        "agents": agents,
    }


def _structured_error(provider: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MissingProviderKey):
        return {
            "ok": False,
            "provider": provider,
            "error_code": exc.error_code,
            "missing_api_key_env": exc.env_name,
            "message": f"{provider} is not configured; set {exc.env_name}.",
        }
    if isinstance(exc, UnsupportedProviderProtocol):
        return {
            "ok": False,
            "provider": provider,
            "error_code": exc.error_code,
            "message": str(exc),
            "todo": "Implement live Anthropic-compatible messages when this provider is promoted into the runtime path.",
        }
    if isinstance(exc, ProviderError):
        return {"ok": False, "provider": provider, "error_code": exc.error_code, "message": str(exc)}
    if isinstance(exc, PiRuntimeError):
        return {"ok": False, "provider": provider, "error_code": exc.error_code, "message": str(exc)}
    return {"ok": False, "provider": provider, "error_code": "provider_error", "message": str(exc)}


def generate_json_task(
    task: dict[str, Any],
    *,
    provider_name: str | None = None,
    model: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    config = load_provider_config(config_path)
    explicit_provider = provider_name or task.get("provider")
    provider = (explicit_provider or resolve_default_provider(config)).strip().lower()
    try:
        preset = get_provider_preset(provider)
        provider = preset.name
        resolved_model = resolve_model(
            provider,
            config,
            override=model or task.get("model"),
            allow_global_default=(not explicit_provider or provider == resolve_default_provider(config)),
        )
        client = ProviderClient(preset=preset, model=resolved_model)
        payload = client.generate_json(task)
        return {"ok": True, "provider": provider, "model": resolved_model, "response": payload}
    except Exception as exc:  # Convert provider failures into call-site friendly payloads.
        return _structured_error(provider, exc)


def test_provider(
    provider_name: str,
    *,
    model: str | None = None,
    config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    del repo_root
    task = {"prompt": "Return a JSON object with ok=true for an AutoBci provider health check."}
    result = generate_json_task(task, provider_name=provider_name, model=model, config_path=config_path)
    if result.get("ok"):
        return {
            "ok": True,
            "provider": result["provider"],
            "model": result["model"],
            "response": result["response"],
        }
    return result


def set_agent_model(
    agent_name: str,
    provider_name: str,
    *,
    model: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    return set_agent_provider_model(agent_name, provider_name, model=model, config_path=config_path)
