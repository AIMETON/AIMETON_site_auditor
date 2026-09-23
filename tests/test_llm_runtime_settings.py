from __future__ import annotations

import os

import pytest

from app.llm_runtime_settings import (
    LlmReasoningEffort,
    LlmReasoningMode,
    LlmRole,
    LlmRuntimeSettings,
    LlmRuntimeSettingsRepository,
    resolve_llm_runtime,
)


def test_llm_runtime_settings_roundtrip(tmp_path) -> None:
    repo = LlmRuntimeSettingsRepository(tmp_path / "runtime.sqlite3")
    initial = repo.ensure_bootstrap_default()
    assert initial.settings.fast_research.profile_name == "routerai-qwen35-9b"
    assert initial.settings.extraction.profile_name == "routerai-current"
    assert initial.settings.reasoning.reasoning_mode is LlmReasoningMode.INHERIT

    updated = initial.settings.model_copy(deep=True)
    updated.reasoning = updated.reasoning.model_copy(
        update={
            "profile_name": "routerai-deepseek-v32",
            "model_id": "deepseek/deepseek-v4-pro",
            "temperature": 0.2,
            "max_tokens": 4096,
            "timeout_seconds": 240,
            "reasoning_mode": LlmReasoningMode.ON,
            "reasoning_effort": LlmReasoningEffort.XHIGH,
        }
    )
    saved = repo.save(updated, actor_id=7, reason="switch reasoning model")
    reread = repo.get()

    assert reread == saved
    assert reread.updated_by == 7
    assert reread.settings.reasoning.model_id == "deepseek/deepseek-v4-pro"
    assert reread.settings.reasoning.reasoning_effort is LlmReasoningEffort.XHIGH


def test_resolve_llm_runtime_uses_registered_credential_and_model_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("ROUTERAI_API_KEY", "secret-test-key")
    monkeypatch.setenv("ROUTERAI_BASE_URL", "https://router.example/v1")
    monkeypatch.setenv("ROUTERAI_QWEN_OBSERVER_MODEL", "qwen/qwen3.5-9b")

    settings = LlmRuntimeSettings()
    settings.fast_research = settings.fast_research.model_copy(
        update={"model_id": "qwen/qwen3.5-14b"}
    )
    runtime = resolve_llm_runtime(LlmRole.FAST_RESEARCH, settings=settings)

    assert runtime.configured is True
    assert runtime.base_url == "https://router.example/v1"
    assert runtime.api_key == "secret-test-key"
    assert runtime.model == "qwen/qwen3.5-14b"
    safe = runtime.safe_descriptor()
    assert "api_key" not in safe
    assert "base_url" not in safe
    assert "secret-test-key" not in str(safe)


def test_routerai_current_preserves_legacy_model_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("ROUTERAI_API_KEY", "test-key")
    monkeypatch.delenv("ROUTERAI_MODEL", raising=False)

    runtime = resolve_llm_runtime(LlmRole.REASONING, settings=LlmRuntimeSettings())

    assert runtime.model == "openai/gpt-4o-mini"
    assert runtime.configured is True


def test_repository_rejects_unregistered_direct_provider_profile(tmp_path) -> None:
    repo = LlmRuntimeSettingsRepository(tmp_path / "runtime.sqlite3")
    settings = LlmRuntimeSettings()
    settings.fast_research = settings.fast_research.model_copy(
        update={"profile_name": "openai-nano"}
    )

    with pytest.raises(ValueError, match="unknown_or_invalid_llm_profile"):
        repo.save(settings, actor_id=1, reason="invalid direct provider")


def test_immers_profile_is_selectable_and_secret_safe(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("IMMERS_API_KEY", "immers-secret")
    monkeypatch.setenv("IMMERS_BASE_URL", "https://immers.example/v1")
    monkeypatch.setenv("IMMERS_DEFAULT_MODEL", "DeepSeek-V4-Flash-0731")

    settings = LlmRuntimeSettings()
    settings.reasoning = settings.reasoning.model_copy(
        update={"profile_name": "immers-primary"}
    )
    repo = LlmRuntimeSettingsRepository(tmp_path / "runtime.sqlite3")
    repo.save(settings, actor_id=1, reason="use immers")

    runtime = resolve_llm_runtime(LlmRole.REASONING, settings=settings)
    assert runtime.provider == "immers"
    assert runtime.model == "DeepSeek-V4-Flash-0731"
    assert runtime.configured is True
    assert runtime.timeout_seconds == 180
    safe = runtime.safe_descriptor()
    assert "immers-secret" not in str(safe)
    assert "https://immers.example/v1" not in str(safe)


def test_immers_rejects_model_outside_registry(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("IMMERS_API_KEY", "immers-secret")
    monkeypatch.setenv("IMMERS_BASE_URL", "https://immers.example/v1")
    monkeypatch.setenv("IMMERS_DEFAULT_MODEL", "DeepSeek-V4-Flash-0731")
    settings = LlmRuntimeSettings()
    settings.reasoning = settings.reasoning.model_copy(
        update={"profile_name": "immers-primary", "model_id": "unlisted-model"}
    )

    with pytest.raises(RuntimeError, match="llm_model_not_allowed"):
        resolve_llm_runtime(LlmRole.REASONING, settings=settings)
