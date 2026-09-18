from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ObserverProvider(StrEnum):
    ROUTERAI = "routerai"
    QWEN = "qwen"
    GLM = "glm"
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    OPENAI = "openai"


class ObserverModelProfile(BaseModel):
    """Configuration-only model profile for Search Observer evaluation.

    Profiles carry no routing or provider-execution authority. They only resolve
    an OpenAI-compatible endpoint, model id and credential environment variable
    for an advisory Observer call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    provider: ObserverProvider
    base_url_env: str = Field(min_length=1, max_length=120)
    api_key_env: str = Field(min_length=1, max_length=120)
    model_env: str = Field(min_length=1, max_length=120)
    default_base_url: str | None = None
    default_model: str | None = None
    tier: str = Field(default="O1", pattern=r"^O[12]$")
    enabled_by_default: bool = False

    def resolve(self) -> "ResolvedObserverModel":
        base_url = os.getenv(self.base_url_env, self.default_base_url or "").rstrip("/")
        api_key = os.getenv(self.api_key_env, "")
        model = os.getenv(self.model_env, self.default_model or "")
        return ResolvedObserverModel(
            profile_name=self.name,
            provider=self.provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            tier=self.tier,
            configured=bool(base_url and api_key and model),
        )


class ResolvedObserverModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_name: str
    provider: ObserverProvider
    base_url: str
    api_key: str
    model: str
    tier: str
    configured: bool

    def safe_descriptor(self) -> dict[str, str | bool]:
        return {
            "profile_name": self.profile_name,
            "provider": self.provider.value,
            "model": self.model,
            "tier": self.tier,
            "configured": self.configured,
        }


# Model identifiers remain environment-overridable because provider catalogs evolve.
# Direct-provider profiles remain available, while RouterAI-backed peers let AIMETON
# compare Chinese O1 candidates with the already configured RouterAI credential.
OBSERVER_MODEL_PROFILES: tuple[ObserverModelProfile, ...] = (
    ObserverModelProfile(
        name="routerai-current",
        provider=ObserverProvider.ROUTERAI,
        base_url_env="ROUTERAI_BASE_URL",
        api_key_env="ROUTERAI_API_KEY",
        model_env="ROUTERAI_MODEL",
        default_base_url="https://api.routerai.ru/v1",
        tier="O2",
        enabled_by_default=True,
    ),
    ObserverModelProfile(
        name="routerai-qwen35-9b",
        provider=ObserverProvider.ROUTERAI,
        base_url_env="ROUTERAI_BASE_URL",
        api_key_env="ROUTERAI_API_KEY",
        model_env="ROUTERAI_QWEN_OBSERVER_MODEL",
        default_base_url="https://api.routerai.ru/v1",
        default_model="qwen/qwen3.5-9b",
        tier="O1",
    ),
    ObserverModelProfile(
        name="routerai-glm47-flash",
        provider=ObserverProvider.ROUTERAI,
        base_url_env="ROUTERAI_BASE_URL",
        api_key_env="ROUTERAI_API_KEY",
        model_env="ROUTERAI_GLM_OBSERVER_MODEL",
        default_base_url="https://api.routerai.ru/v1",
        default_model="z-ai/glm-4.7-flash",
        tier="O1",
    ),
    ObserverModelProfile(
        name="routerai-deepseek-v32",
        provider=ObserverProvider.ROUTERAI,
        base_url_env="ROUTERAI_BASE_URL",
        api_key_env="ROUTERAI_API_KEY",
        model_env="ROUTERAI_DEEPSEEK_OBSERVER_MODEL",
        default_base_url="https://api.routerai.ru/v1",
        default_model="deepseek/deepseek-v3.2",
        tier="O1",
    ),
    ObserverModelProfile(
        name="routerai-deepseek-v4-flash-latest",
        provider=ObserverProvider.ROUTERAI,
        base_url_env="ROUTERAI_BASE_URL",
        api_key_env="ROUTERAI_API_KEY",
        model_env="ROUTERAI_DEEPSEEK_V4_FLASH_MODEL",
        default_base_url="https://api.routerai.ru/v1",
        default_model="~deepseek/deepseek-v4-flash-latest",
        tier="O1",
    ),
    ObserverModelProfile(
        name="qwen-flash",
        provider=ObserverProvider.QWEN,
        base_url_env="QWEN_BASE_URL",
        api_key_env="QWEN_API_KEY",
        model_env="QWEN_OBSERVER_MODEL",
        tier="O1",
    ),
    ObserverModelProfile(
        name="glm-flash",
        provider=ObserverProvider.GLM,
        base_url_env="GLM_BASE_URL",
        api_key_env="GLM_API_KEY",
        model_env="GLM_OBSERVER_MODEL",
        tier="O1",
    ),
    ObserverModelProfile(
        name="deepseek-chat",
        provider=ObserverProvider.DEEPSEEK,
        base_url_env="DEEPSEEK_BASE_URL",
        api_key_env="DEEPSEEK_API_KEY",
        model_env="DEEPSEEK_OBSERVER_MODEL",
        tier="O1",
    ),
    ObserverModelProfile(
        name="gemini-flash-lite",
        provider=ObserverProvider.GEMINI,
        base_url_env="GEMINI_OPENAI_BASE_URL",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_OBSERVER_MODEL",
        tier="O1",
    ),
    ObserverModelProfile(
        name="openai-nano",
        provider=ObserverProvider.OPENAI,
        base_url_env="OPENAI_BASE_URL",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_OBSERVER_MODEL",
        default_base_url="https://api.openai.com/v1",
        tier="O1",
    ),
)


def configured_observer_models() -> list[ResolvedObserverModel]:
    return [resolved for profile in OBSERVER_MODEL_PROFILES if (resolved := profile.resolve()).configured]


def observer_profile(name: str) -> ObserverModelProfile:
    for profile in OBSERVER_MODEL_PROFILES:
        if profile.name == name:
            return profile
    raise KeyError(name)
