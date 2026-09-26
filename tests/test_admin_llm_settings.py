from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.admin_llm_settings_api as api
from app.auth import User, UserRole
from app.auth_api import require_admin


def _admin() -> User:
    return User(id=1, username="admin", role=UserRole.ADMIN, is_active=True)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_admin] = _admin
    return TestClient(app)


def test_admin_llm_settings_projection_never_exposes_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("ROUTERAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("ROUTERAI_BASE_URL", "https://router.example/v1")
    monkeypatch.setenv("ROUTERAI_MODEL", "deepseek/deepseek-v4-pro")

    response = _client().get("/api/admin/llm-settings")

    assert response.status_code == 200
    body = response.json()
    dumped = json.dumps(body)
    assert "must-not-leak" not in dumped
    assert "api_key" not in dumped
    assert "https://router.example/v1" not in dumped
    assert body["resolved"]["reasoning"]["model"] == "deepseek/deepseek-v4-pro"
    assert body["record"]["settings"]["fast_research"]["profile_name"] == "routerai-qwen35-9b"


def test_admin_can_save_three_llm_roles_with_csrf(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("ROUTERAI_API_KEY", "test-key")
    csrf = "csrf-test"
    payload = {
        "settings": {
            "fast_research": {
                "profile_name": "routerai-qwen35-9b",
                "model_id": "qwen/qwen3.5-9b",
                "temperature": 0,
                "max_tokens": 900,
                "timeout_seconds": 12,
                "output_mode": "strict_schema",
                "reasoning_mode": "off",
                "reasoning_effort": None,
            },
            "extraction": {
                "profile_name": "routerai-current",
                "model_id": "deepseek/deepseek-v4-pro",
                "temperature": 0.1,
                "max_tokens": 8192,
                "timeout_seconds": 120,
                "output_mode": "strict_schema",
                "reasoning_mode": "off",
                "reasoning_effort": None,
            },
            "reasoning": {
                "profile_name": "routerai-current",
                "model_id": "deepseek/deepseek-v4-pro",
                "temperature": 0.15,
                "max_tokens": 6000,
                "timeout_seconds": 240,
                "output_mode": "strict_schema",
                "reasoning_mode": "on",
                "reasoning_effort": "xhigh",
            },
        },
        "reason": "admin model policy",
    }

    response = _client().put(
        "/api/admin/llm-settings",
        json=payload,
        cookies={"aimeton_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["record"]["updated_by"] == 1
    assert body["record"]["reason"] == "admin model policy"
    assert body["resolved"]["fast_research"]["model"] == "qwen/qwen3.5-9b"
    assert body["resolved"]["reasoning"]["model"] == "deepseek/deepseek-v4-pro"
    assert body["resolved"]["reasoning"]["reasoning_effort"] == "xhigh"


def test_admin_llm_save_requires_csrf(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    body = _client().get("/api/admin/llm-settings").json()
    response = _client().put(
        "/api/admin/llm-settings",
        json={"settings": body["record"]["settings"], "reason": "missing csrf"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "csrf_failed"


def test_probe_uses_unsaved_role_settings_and_returns_sanitized_telemetry(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("ROUTERAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("ROUTERAI_BASE_URL", "https://router.example/v1")
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "model": "qwen/qwen3.5-14b",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '{"ok":true,"message":"ready"}'},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout: FakeClient())
    csrf = "csrf-test"
    response = _client().post(
        "/api/admin/llm-settings/test",
        json={
            "role": "fast_research",
            "settings": {
                "profile_name": "routerai-qwen35-9b",
                "model_id": "qwen/qwen3.5-14b",
                "temperature": 0,
                "max_tokens": 512,
                "timeout_seconds": 10,
                "output_mode": "strict_schema",
                "reasoning_mode": "off",
                "reasoning_effort": None,
            },
        },
        cookies={"aimeton_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["resolved_model"] == "qwen/qwen3.5-14b"
    assert body["structured_output_valid"] is True
    assert body["effective_output_mode"] == "strict_schema"
    assert body["total_tokens"] == 15
    assert captured["payload"]["model"] == "qwen/qwen3.5-14b"
    assert captured["payload"]["reasoning"] == {"enabled": False}
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert "must-not-leak" not in json.dumps(body)


def test_admin_catalog_includes_deepseek_v4_flash_latest_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only-key")
    monkeypatch.setenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")

    response = _client().get("/api/admin/llm-settings")

    assert response.status_code == 200
    profiles = {item["profile_name"]: item for item in response.json()["profiles"]}
    candidate = profiles["routerai-deepseek-v4-flash-latest"]
    assert candidate["provider"] == "routerai"
    assert candidate["model"] == "~deepseek/deepseek-v4-flash-latest"
    assert candidate["configured"] is True


def test_admin_catalog_includes_immers_without_exposing_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("IMMERS_API_KEY", "immers-must-not-leak")
    monkeypatch.setenv("IMMERS_BASE_URL", "https://immers.example/v1")
    monkeypatch.setenv("IMMERS_DEFAULT_MODEL", "deepseek-v4-flash-0731")

    response = _client().get("/api/admin/llm-settings")

    assert response.status_code == 200
    body = response.json()
    profiles = {item["profile_name"]: item for item in body["profiles"]}
    immers = profiles["immers-primary"]
    assert immers["provider"] == "immers"
    assert immers["model"] == "deepseek-v4-flash-0731"
    assert immers["configured"] is True
    assert immers["model_allowed"] is True
    assert immers["capabilities"]["reasoning"] is True
    assert immers["capabilities"]["structured_output"] is None
    assert immers["capabilities"]["json_mode"] is True
    dumped = json.dumps(body)
    assert "immers-must-not-leak" not in dumped
    assert "https://immers.example/v1" not in dumped


def test_admin_can_save_immers_for_reasoning(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("IMMERS_API_KEY", "immers-key")
    monkeypatch.setenv("IMMERS_BASE_URL", "https://immers.example/v1")
    monkeypatch.setenv("IMMERS_DEFAULT_MODEL", "deepseek-v4-flash-0731")
    csrf = "csrf-test"
    body = _client().get("/api/admin/llm-settings").json()
    settings = body["record"]["settings"]
    settings["reasoning"]["profile_name"] = "immers-primary"
    settings["reasoning"]["model_id"] = None

    response = _client().put(
        "/api/admin/llm-settings",
        json={"settings": settings, "reason": "switch reasoning to immers"},
        cookies={"aimeton_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    resolved = response.json()["resolved"]["reasoning"]
    assert resolved["provider"] == "immers"
    assert resolved["model"] == "deepseek-v4-flash-0731"
    assert resolved["configured"] is True



def test_immers_probe_inherit_matches_production_json_object_transport(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("IMMERS_API_KEY", "immers-key")
    monkeypatch.setenv("IMMERS_BASE_URL", "https://immers.example/v1")
    monkeypatch.setenv("IMMERS_DEFAULT_MODEL", "deepseek-v4-flash-0731")
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "model": "deepseek-v4-flash-0731",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '{"ok":true,"message":"ready"}'},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout: FakeClient())
    csrf = "csrf-test"
    response = _client().post(
        "/api/admin/llm-settings/test",
        json={
            "role": "reasoning",
            "settings": {
                "profile_name": "immers-primary",
                "model_id": None,
                "temperature": 0.1,
                "max_tokens": 512,
                "timeout_seconds": 30,
                "output_mode": "inherit",
                "reasoning_mode": "inherit",
                "reasoning_effort": None,
            },
        },
        cookies={"aimeton_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["effective_output_mode"] == "json_object"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert "structured_outputs" not in captured["payload"]
