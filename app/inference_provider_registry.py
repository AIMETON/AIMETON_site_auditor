from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "inference_provider_registry.json"


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chat: bool = True
    streaming: bool | None = None
    reasoning: bool | None = None
    tools: bool | None = None
    structured_output: bool | None = None
    json_mode: bool | None = None


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    price_input: float | None = Field(default=None, ge=0)
    price_output: float | None = Field(default=None, ge=0)
    price_cached_input: float | None = Field(default=None, ge=0)


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    provider: str
    enabled: bool = True
    base_url_env: str
    api_key_env: str
    model_env: str
    timeout_seconds: float = Field(default=180, gt=0)
    connect_timeout_seconds: float = Field(default=20, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    models: dict[str, ModelSpec] = Field(default_factory=dict)

    def resolve(self, model_override: str | None = None) -> "ResolvedProviderProfile":
        base_url = os.getenv(self.base_url_env, "").rstrip("/")
        api_key = os.getenv(self.api_key_env, "")
        model = (model_override or os.getenv(self.model_env, "")).strip()
        enabled_models = {model_id for model_id, spec in self.models.items() if spec.enabled}
        model_allowed = bool(not model or not enabled_models or model in enabled_models)
        return ResolvedProviderProfile(
            profile_name=self.name,
            provider=self.provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            configured=bool(self.enabled and base_url and api_key and model_allowed),
            model_allowed=model_allowed,
            timeout_seconds=self.timeout_seconds,
            connect_timeout_seconds=self.connect_timeout_seconds,
            max_retries=self.max_retries,
            model_spec=self.models.get(model),
        )


class ResolvedProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_name: str
    provider: str
    base_url: str
    api_key: str
    model: str
    configured: bool
    model_allowed: bool
    timeout_seconds: float
    connect_timeout_seconds: float
    max_retries: int
    model_spec: ModelSpec | None = None

    def safe_descriptor(self) -> dict[str, Any]:
        spec = self.model_spec
        return {
            "profile_name": self.profile_name,
            "provider": self.provider,
            "model": self.model,
            "configured": self.configured,
            "model_allowed": self.model_allowed,
            "capabilities": spec.capabilities.model_dump() if spec else None,
            "context_window": spec.context_window if spec else None,
            "max_output_tokens": spec.max_output_tokens if spec else None,
            "price_input": spec.price_input if spec else None,
            "price_output": spec.price_output if spec else None,
            "price_cached_input": spec.price_cached_input if spec else None,
        }


def load_provider_profiles(path: Path = REGISTRY_PATH) -> dict[str, ProviderProfile]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, dict):
        raise RuntimeError("invalid_inference_provider_registry")
    return {
        name: ProviderProfile(name=name, **value)
        for name, value in profiles.items()
        if isinstance(value, dict)
    }


def provider_profile(name: str) -> ProviderProfile:
    try:
        return load_provider_profiles()[name]
    except KeyError as exc:
        raise KeyError(name) from exc


def runtime_provider_profiles() -> list[ProviderProfile]:
    return [profile for profile in load_provider_profiles().values() if profile.enabled]
