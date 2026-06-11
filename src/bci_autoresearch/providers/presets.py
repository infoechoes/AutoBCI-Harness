from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ProviderProtocol = Literal["pi", "openai_compatible", "anthropic_compatible"]


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    protocol: ProviderProtocol
    base_url: str
    api_key_env: str | None
    default_model: str
    capabilities: tuple[str, ...]
    capability_profile: dict[str, Any] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    pi_provider: str | None = None
    display_name: str | None = None
    aliases: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        payload["aliases"] = list(self.aliases)
        return payload


_OPENAI_JSON_BODY = {"response_format": {"type": "json_object"}}


def _profile(
    *,
    chat: bool,
    json_schema: bool,
    tool_calls: str,
    streaming: bool,
    reasoning: str,
    context: str,
    coding_suitability: str,
) -> dict[str, Any]:
    return {
        "chat": chat,
        "json_schema": json_schema,
        "tool_calls": tool_calls,
        "streaming": streaming,
        "reasoning": reasoning,
        "context": context,
        "coding_suitability": coding_suitability,
    }


OPENAI_COMPATIBLE_PROFILE = _profile(
    chat=True,
    json_schema=True,
    tool_calls="adapter_json_actions",
    streaming=False,
    reasoning="provider_dependent",
    context="provider_default",
    coding_suitability="smoke_supported",
)


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        name="deepseek",
        protocol="pi",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-v4-flash",
        capabilities=("pi_ai", "json_task"),
        capability_profile=OPENAI_COMPATIBLE_PROFILE,
        extra_body={},
        pi_provider="deepseek",
        display_name="DeepSeek",
    ),
    "kimi": ProviderPreset(
        name="kimi",
        protocol="pi",
        base_url="https://api.kimi.com/coding",
        api_key_env="KIMI_API_KEY",
        default_model="kimi-k2-thinking",
        capabilities=("pi_ai", "json_task"),
        capability_profile=OPENAI_COMPATIBLE_PROFILE,
        extra_body={},
        pi_provider="kimi-coding",
        display_name="Kimi",
    ),
    "glm": ProviderPreset(
        name="glm",
        protocol="pi",
        base_url="https://api.z.ai/api/coding/paas/v4",
        api_key_env="ZAI_API_KEY",
        default_model="glm-4.7",
        capabilities=("pi_ai", "json_task"),
        capability_profile=OPENAI_COMPATIBLE_PROFILE,
        extra_body={},
        pi_provider="zai",
        display_name="GLM / zAI",
    ),
    "minimax": ProviderPreset(
        name="minimax",
        protocol="pi",
        base_url="https://api.minimax.io/anthropic",
        api_key_env="MINIMAX_API_KEY",
        default_model="MiniMax-M2.7",
        capabilities=("pi_ai", "json_task"),
        capability_profile=OPENAI_COMPATIBLE_PROFILE,
        extra_body={},
        pi_provider="minimax",
        display_name="MiniMax",
    ),
    "openai": ProviderPreset(
        name="openai",
        protocol="pi",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-5.5",
        capabilities=("pi_ai", "json_task"),
        capability_profile=OPENAI_COMPATIBLE_PROFILE,
        extra_body={},
        pi_provider="openai",
        display_name="OpenAI",
    ),
    "anthropic": ProviderPreset(
        name="anthropic",
        protocol="pi",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-4-20250514",
        capabilities=("pi_ai", "json_task"),
        capability_profile=_profile(
            chat=True,
            json_schema=True,
            tool_calls="pi_ai_tools_available_not_used",
            streaming=False,
            reasoning="provider_dependent",
            context="provider_default",
            coding_suitability="smoke_supported",
        ),
        extra_body={},
        pi_provider="anthropic",
        display_name="Anthropic",
    ),
    "xiaomi": ProviderPreset(
        name="xiaomi",
        protocol="pi",
        base_url="https://api.xiaomimimo.com/anthropic",
        api_key_env="XIAOMI_API_KEY",
        default_model="mimo-v2-pro",
        capabilities=("pi_ai", "json_task"),
        capability_profile=OPENAI_COMPATIBLE_PROFILE,
        extra_body={},
        pi_provider="xiaomi",
        display_name="Xiaomi MiMo",
        aliases=("mimo", "xiaomi-mimo"),
    ),
}

PROVIDER_ALIASES: dict[str, str] = {
    alias: name
    for name, preset in PROVIDER_PRESETS.items()
    for alias in preset.aliases
}


def normalize_provider_name(name: str) -> str:
    key = str(name or "").strip().lower()
    return PROVIDER_ALIASES.get(key, key)


def list_provider_presets() -> list[str]:
    return sorted(PROVIDER_PRESETS)


def get_provider_preset(name: str) -> ProviderPreset:
    key = normalize_provider_name(name)
    try:
        return PROVIDER_PRESETS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {name}") from exc
