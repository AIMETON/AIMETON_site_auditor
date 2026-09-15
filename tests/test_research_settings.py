from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import LocalAuthProvider, PasswordHasher, SQLiteUserRepository, UserRole
from app.auth_api import get_auth_provider, router as auth_router, CSRF_COOKIE, CSRF_HEADER
from app.research_settings import ResearchSettings, ResearchSettingsRepository, SettingsConflict
from app.research_settings_api import repository, router


@pytest.mark.parametrize("values", [
    {"mission_timeout_seconds": 0}, {"request_timeout_seconds": -1},
    {"llm_call_timeout_seconds": float("inf")}, {"progress_warning_seconds": float("nan")},
    {"request_timeout_seconds": True}, {"token_limit": True}, {"cost_limit_amount": "NaN"},
    {"token_warning": 11, "token_limit": 10},
    {"cost_warning_amount": "10.01", "cost_limit_amount": "10"},
    {"cost_limit_amount": "5", "unknown_price_action": "allow_unpriced"},
    {"owner_id": 3}, {"retry_count": -1},
])
def test_invalid_preferences_cannot_be_saved(values):
    with pytest.raises(ValidationError):
        ResearchSettings(**values)


def test_owner_service_isolation_and_restart(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = ResearchSettingsRepository(path)
    settings = ResearchSettings(mission_timeout_seconds=900, cost_limit_amount=Decimal("500.25"))
    saved = store.save(1, "site-audit", settings, expected_revision=0)
    assert ResearchSettingsRepository(path).get(1, "site-audit") == saved
    assert store.get(2, "site-audit").revision == 0
    assert store.get(1, "company-intelligence").revision == 0
    assert saved.settings.cost_limit_amount == Decimal("500.25")
    assert saved.execution_enabled is False


def test_concurrent_saves_cannot_silently_overwrite(tmp_path):
    store = ResearchSettingsRepository(tmp_path / "runtime.sqlite3")
    def save(seconds):
        try:
            return store.save(1, "site-audit", ResearchSettings(request_timeout_seconds=seconds), expected_revision=0)
        except SettingsConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [15, 30]))
    assert sum(item is not None for item in results) == 1
    assert store.get(1, "site-audit").revision == 1


def test_snapshot_is_immutable_and_null_override_is_explicit(tmp_path):
    store = ResearchSettingsRepository(tmp_path / "runtime.sqlite3")
    store.save(1, "site-audit", ResearchSettings(mission_timeout_seconds=300), expected_revision=0)
    saved = store.snapshot(1, "site-audit", "mission_1", "run_1", overrides={"mission_timeout_seconds": None})
    assert saved["requested"]["mission_timeout_seconds"] is None
    assert saved["effective"] is None
    store.save(1, "site-audit", ResearchSettings(mission_timeout_seconds=600), expected_revision=1)
    assert store.snapshot(1, "site-audit", "mission_1", "run_1", overrides={"mission_timeout_seconds": None}) == saved
    assert store.snapshot(1, "site-audit", "mission_1", "run_2")["settings_revision"] == 2
    with pytest.raises(SettingsConflict):
        store.snapshot(1, "site-audit", "mission_1", "run_1", overrides={"mission_timeout_seconds": 10})
    with pytest.raises(ValidationError):
        store.snapshot(1, "site-audit", "mission_1", "run_3", overrides={"token_limit": -1})


def test_api_requires_auth_csrf_and_derives_owner_from_session(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMETON_COOKIE_SECURE", "false")
    users = SQLiteUserRepository(tmp_path / "auth.sqlite3")
    hasher = PasswordHasher()
    for name in ("first", "second"):
        users.create_user(name, hasher.hash("research settings password"), UserRole.USER)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(router)
    store = ResearchSettingsRepository(tmp_path / "runtime.sqlite3")
    app.dependency_overrides[get_auth_provider] = lambda: LocalAuthProvider(users)
    app.dependency_overrides[repository] = lambda: store
    with TestClient(app) as client:
        url = "/api/user/research-settings/site-audit"
        assert client.get(url).status_code == 401
        client.post("/api/auth/login", json={"username": "first", "password": "research settings password"})
        payload = {"expected_revision": 0, "settings": {"mission_timeout_seconds": 1200}}
        assert client.put(url, json=payload).status_code == 403
        headers = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
        assert client.put(url, json={**payload, "owner_id": 2}, headers=headers).status_code == 422
        response = client.put(url, json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["revision"] == 1
        assert client.put(url, json=payload, headers=headers).status_code == 409
        client.post("/api/auth/login", json={"username": "second", "password": "research settings password"})
        assert client.get(url).json()["revision"] == 0
        assert client.get("/api/user/research-settings/unknown").status_code == 422
