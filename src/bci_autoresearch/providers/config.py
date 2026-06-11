from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback keeps config readable enough.
    tomllib = None  # type: ignore[assignment]

from .presets import get_provider_preset, normalize_provider_name


CONFIG_ENV = "AUTOBCI_PROVIDER_CONFIG"
SECRETS_ENV = "AUTOBCI_PROVIDER_SECRETS"
DEFAULT_PROVIDER_ENV = "AUTOBCI_DEFAULT_PROVIDER"
DEFAULT_MODEL_ENV = "AUTOBCI_DEFAULT_MODEL"
KNOWN_AGENT_NAMES = ("intake", "judge", "guard", "research", "worker")
LIVE_AGENT_NAMES = {"intake"}


def get_provider_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    if platform.system().lower().startswith("win"):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "AutoBci" / "providers.toml"
    return Path.home() / ".config" / "autobci" / "providers.toml"


def get_provider_secrets_path() -> Path:
    override = os.environ.get(SECRETS_ENV)
    if override:
        return Path(override).expanduser()
    if platform.system().lower().startswith("win"):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "AutoBci" / "provider_secrets.toml"
    return Path.home() / ".config" / "autobci" / "provider_secrets.toml"


def load_provider_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else get_provider_config_path()
    if not path.exists():
        return {}
    if tomllib is None:
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload if isinstance(payload, dict) else {}


def load_provider_secrets(secrets_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(secrets_path) if secrets_path is not None else get_provider_secrets_path()
    if not path.exists():
        return {}
    if tomllib is None:
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload if isinstance(payload, dict) else {}


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_table(lines: list[str], root: str, values: dict[str, Any]) -> None:
    for name in sorted(values):
        if not isinstance(values[name], dict):
            continue
        lines.append("")
        lines.append(f"[{root}.{name}]")
        for key, value in sorted(values[name].items()):
            if value is None:
                continue
            lines.append(f"{key} = {_toml_quote(str(value))}")


def write_provider_config(config: dict[str, Any], config_path: str | Path | None = None) -> Path:
    path = Path(config_path) if config_path is not None else get_provider_config_path()
    lines: list[str] = []
    default_provider = str(config.get("default_provider") or "").strip()
    default_model = str(config.get("default_model") or "").strip()
    if default_provider:
        lines.append(f"default_provider = {_toml_quote(default_provider)}")
    if default_model:
        lines.append(f"default_model = {_toml_quote(default_model)}")
    providers = config.get("providers")
    if isinstance(providers, dict):
        _write_table(lines, "providers", providers)
    agents = config.get("agents")
    if isinstance(agents, dict):
        _write_table(lines, "agents", agents)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_provider_secret(provider_name: str, api_key: str, *, secrets_path: str | Path | None = None) -> dict[str, Any]:
    preset = get_provider_preset(provider_name)
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("API key cannot be empty.")
    path = Path(secrets_path) if secrets_path is not None else get_provider_secrets_path()
    secrets = load_provider_secrets(path)
    providers = secrets.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    provider_cfg = providers.get(preset.name)
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    provider_cfg["api_key"] = key
    providers[preset.name] = provider_cfg
    lines: list[str] = []
    _write_table(lines, "providers", providers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).lstrip().rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows permissions are best-effort.
        pass
    return {"ok": True, "provider": preset.name, "secrets_path": str(path), "key_saved": True}


def resolve_provider_api_key(provider_name: str, env_name: str | None) -> str | None:
    if env_name:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
    secrets = load_provider_secrets()
    providers = secrets.get("providers")
    if isinstance(providers, dict):
        canonical = normalize_provider_name(provider_name)
        item = providers.get(canonical)
        if not isinstance(item, dict):
            item = providers.get(str(provider_name or "").strip().lower())
        if isinstance(item, dict):
            value = str(item.get("api_key") or "").strip()
            if value:
                return value
    return None


def resolve_default_provider(config: dict[str, Any] | None = None) -> str:
    env_value = os.environ.get(DEFAULT_PROVIDER_ENV)
    if env_value:
        return normalize_provider_name(env_value)
    cfg_value = (config or {}).get("default_provider")
    if cfg_value:
        return normalize_provider_name(str(cfg_value))
    return "openai"


def resolve_model(
    provider_name: str,
    config: dict[str, Any] | None = None,
    override: str | None = None,
    *,
    allow_global_default: bool = True,
) -> str:
    provider_name = normalize_provider_name(provider_name)
    if override:
        return override
    env_value = os.environ.get(DEFAULT_MODEL_ENV)
    if env_value and allow_global_default:
        return env_value.strip()
    cfg = config or {}
    provider_cfg = cfg.get("providers", {})
    if isinstance(provider_cfg, dict):
        item = provider_cfg.get(provider_name, {})
        if isinstance(item, dict) and item.get("model"):
            return str(item["model"]).strip()
    if cfg.get("default_model") and allow_global_default:
        return str(cfg["default_model"]).strip()
    return get_provider_preset(provider_name).default_model


def _normalize_agent_name(agent_name: str) -> str:
    return str(agent_name or "intake").strip().lower().replace("-", "_")


def _agent_env_prefix(agent_name: str) -> str:
    return "AUTOBCI_" + _normalize_agent_name(agent_name).upper().replace("-", "_")


def resolve_agent_provider_model(
    agent_name: str,
    config: dict[str, Any] | None = None,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    agent = _normalize_agent_name(agent_name)
    cfg = config if config is not None else load_provider_config()
    env_prefix = _agent_env_prefix(agent)
    env_provider = str(os.environ.get(f"{env_prefix}_PROVIDER") or "").strip().lower()
    env_model = str(os.environ.get(f"{env_prefix}_MODEL") or "").strip()
    agents = cfg.get("agents", {})
    agent_cfg = agents.get(agent, {}) if isinstance(agents, dict) else {}
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
    configured_provider = str(agent_cfg.get("provider") or "").strip().lower()
    configured_model = str(agent_cfg.get("model") or "").strip()
    if agent == "worker" and not provider_override and not env_provider and not configured_provider:
        intake_cfg = agents.get("intake", {}) if isinstance(agents, dict) else {}
        if isinstance(intake_cfg, dict):
            configured_provider = str(intake_cfg.get("provider") or "").strip().lower()
            if not env_model and not configured_model:
                configured_model = str(intake_cfg.get("model") or "").strip()
    provider = normalize_provider_name(str(provider_override or env_provider or configured_provider or resolve_default_provider(cfg)))
    preset = get_provider_preset(provider)
    provider = preset.name
    agent_has_provider = bool(provider_override or env_provider or configured_provider)
    model = str(model_override or env_model or configured_model or "").strip()
    if not model:
        model = resolve_model(provider, cfg, allow_global_default=not agent_has_provider)
    return {"agent": agent, "provider": provider, "model": model, "live": agent in LIVE_AGENT_NAMES}


def set_agent_provider_model(
    agent_name: str,
    provider_name: str,
    *,
    model: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    agent = _normalize_agent_name(agent_name)
    preset = get_provider_preset(provider_name)
    path = Path(config_path) if config_path is not None else get_provider_config_path()
    config = load_provider_config(path)
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    provider_cfg = providers.get(preset.name)
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    resolved_model = str(model or resolve_model(preset.name, config, allow_global_default=False)).strip()
    provider_cfg["model"] = resolved_model
    providers[preset.name] = provider_cfg
    agents = config.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    agent_cfg = agents.get(agent)
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
    agent_cfg["provider"] = preset.name
    agent_cfg["model"] = resolved_model
    agents[agent] = agent_cfg
    config["providers"] = providers
    config["agents"] = agents
    write_provider_config(config, path)
    return {
        "ok": True,
        "agent": agent,
        "provider": preset.name,
        "model": resolved_model,
        "config_path": str(path),
        "live": agent in LIVE_AGENT_NAMES,
    }


def set_default_provider(provider_name: str, *, model: str | None = None, config_path: str | Path | None = None) -> dict[str, Any]:
    preset = get_provider_preset(provider_name)
    path = Path(config_path) if config_path is not None else get_provider_config_path()
    config = load_provider_config(path)
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    provider_cfg = providers.get(preset.name)
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    resolved_model = model or resolve_model(preset.name, config, allow_global_default=False)
    provider_cfg["model"] = resolved_model
    providers[preset.name] = provider_cfg
    config["providers"] = providers
    config["default_provider"] = preset.name
    config["default_model"] = resolved_model
    write_provider_config(config, path)
    return {
        "ok": True,
        "config_path": str(path),
        "default_provider": preset.name,
        "default_model": resolved_model,
    }
