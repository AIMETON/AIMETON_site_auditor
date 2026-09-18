import pytest

from app.search_observer_models import (
    OBSERVER_MODEL_PROFILES,
    ObserverProvider,
    configured_observer_models,
    observer_profile,
)


def test_registry_contains_chinese_and_global_candidates():
    providers = {profile.provider for profile in OBSERVER_MODEL_PROFILES}
    assert ObserverProvider.QWEN in providers
    assert ObserverProvider.GLM in providers
    assert ObserverProvider.DEEPSEEK in providers
    assert ObserverProvider.GEMINI in providers
    assert ObserverProvider.OPENAI in providers


def test_qwen_profile_resolves_from_environment(monkeypatch):
    monkeypatch.setenv("QWEN_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setenv("QWEN_API_KEY", "secret")
    monkeypatch.setenv("QWEN_OBSERVER_MODEL", "qwen-test")
    resolved = observer_profile("qwen-flash").resolve()
    assert resolved.configured is True
    assert resolved.base_url == "https://example.invalid/v1"
    assert resolved.model == "qwen-test"
    assert resolved.safe_descriptor() == {
        "profile_name": "qwen-flash",
        "provider": "qwen",
        "model": "qwen-test",
        "tier": "O1",
        "configured": True,
    }
    assert "secret" not in str(resolved.safe_descriptor())


def test_unconfigured_profile_is_not_selected(monkeypatch):
    for env_name in (
        "GLM_BASE_URL",
        "GLM_API_KEY",
        "GLM_OBSERVER_MODEL",
        "QWEN_BASE_URL",
        "QWEN_API_KEY",
        "QWEN_OBSERVER_MODEL",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_OBSERVER_MODEL",
        "GEMINI_OPENAI_BASE_URL",
        "GEMINI_API_KEY",
        "GEMINI_OBSERVER_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_OBSERVER_MODEL",
        "ROUTERAI_API_KEY",
        "ROUTERAI_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    names = {item.profile_name for item in configured_observer_models()}
    assert "qwen-flash" not in names
    assert "glm-flash" not in names
    assert "deepseek-chat" not in names


def test_unknown_profile_rejected():
    with pytest.raises(KeyError):
        observer_profile("not-a-model")


def test_routerai_deepseek_v4_flash_latest_profile_uses_stable_alias(monkeypatch):
    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only-key")
    monkeypatch.setenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")
    monkeypatch.delenv("ROUTERAI_DEEPSEEK_V4_FLASH_MODEL", raising=False)

    resolved = observer_profile("routerai-deepseek-v4-flash-latest").resolve()

    assert resolved.configured is True
    assert resolved.provider is ObserverProvider.ROUTERAI
    assert resolved.model == "~deepseek/deepseek-v4-flash-latest"
    assert resolved.safe_descriptor()["model"] == "~deepseek/deepseek-v4-flash-latest"
    assert "test-only-key" not in str(resolved.safe_descriptor())
