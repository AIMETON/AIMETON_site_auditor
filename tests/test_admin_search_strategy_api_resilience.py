from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin_search_strategy_api as api
from app.auth import User, UserRole
from app.auth_api import require_admin
from app.search_strategy_settings import SearchStrategySettingsRecord


def _admin() -> User:
    return User(id=1, username="admin", role=UserRole.ADMIN, is_active=True)


class _Repository:
    def get(self) -> SearchStrategySettingsRecord:
        return SearchStrategySettingsRecord()


def test_search_strategy_get_survives_optional_observation_failure(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_admin] = _admin
    monkeypatch.setattr(api, "get_search_strategy_settings_repository", lambda: _Repository())

    def fail_observation(_record: SearchStrategySettingsRecord) -> dict:
        raise RuntimeError("sensitive internal diagnostic failure")

    monkeypatch.setattr(api, "_execution_policy_observation", fail_observation)

    response = TestClient(app).get("/api/admin/search-strategies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["record"]["settings"]["global_settings"]["active_tariff"] == "start"
    assert payload["catalog"]
    assert payload["execution_policy_observation"] == {
        "observation_state": "degraded",
        "observation_reason": "runtime_observation_unavailable",
        "routing_changed_by_observation": False,
    }
    assert "sensitive internal diagnostic failure" not in response.text
