import asyncio
import json
import sqlite3
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.research_control import ResearchControl, bind_research
from app.research_execution import PolicyUnavailable, ResearchInterrupted, compile_policy, run_controlled
from app.research_settings import ResearchSettings, ResearchSettingsRepository, SettingsConflict


def settings(**values):
    return ResearchSettings(unknown_price_action="allow_unpriced", hard_limit_action="stop", **values)


@pytest.mark.parametrize("value", [
    ResearchSettings(), ResearchSettings(cost_limit_amount="1"),
    ResearchSettings(cost_warning_amount="1"), ResearchSettings(token_limit=10),
    ResearchSettings(token_warning=10), ResearchSettings(mission_timeout_seconds=10, unknown_price_action="allow_unpriced"),
])
def test_unsupported_guarantees_are_rejected(value):
    with pytest.raises(PolicyUnavailable):
        compile_policy(value)


def test_effective_snapshot_is_immutable_and_checks_revision(tmp_path):
    store = ResearchSettingsRepository(tmp_path / "runtime.db")
    store.save(1, "site-audit", settings(mission_timeout_seconds=900), expected_revision=0)
    saved = store.snapshot(1, "site-audit", "mission", "run", enforce=True, expected_revision=1)
    assert saved["effective"]["mission_timeout_seconds"] == 900
    assert saved["enforcement_state"] == "active"
    store.save(1, "site-audit", settings(mission_timeout_seconds=10), expected_revision=1)
    assert store.snapshot(1, "site-audit", "mission", "run", enforce=True, expected_revision=1) == saved
    with pytest.raises(SettingsConflict):
        store.snapshot(1, "site-audit", "mission", "new-run", enforce=True, expected_revision=1)


@pytest.mark.asyncio
async def test_warning_does_not_cancel_and_null_total_deadline_is_uncapped(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    control = ResearchControl(settings=settings(progress_warning_seconds=0.01))
    async def work():
        await asyncio.sleep(0.04)
        assert control.progress_warning
        return "retained"
    assert await run_controlled(control, work) == "retained"
    assert not control.stop_requested


@pytest.mark.asyncio
async def test_llm_wall_clock_timeout_uses_selected_value(monkeypatch):
    from app.routerai_strict_request import request_json_strict
    class Answer(BaseModel):
        value: str
    monkeypatch.setenv("ROUTERAI_API_KEY", "fake")
    original = httpx.AsyncClient
    cancelled = asyncio.Event()
    async def provider(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(provider), **kw))
    control = ResearchControl(settings=settings(llm_call_timeout_seconds=0.01))
    with bind_research(control), pytest.raises(TimeoutError):
        await request_json_strict("phase", Answer, system="s", prompt="p", max_tokens=10, timeout_seconds=900)
    assert cancelled.is_set()
    assert control.llm_calls == 1 and control.llm_usage_reports == 0


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.auth_api as auth_api
    from app.auth import LocalAuthProvider, PasswordHasher, SQLiteUserRepository, UserRole
    from app.main import app
    from app.mission_orchestrator import reset_mission_orchestrator
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("AIMETON_TRACE_DB", str(tmp_path / "trace.db"))
    monkeypatch.setenv("AIMETON_COOKIE_SECURE", "false")
    users = SQLiteUserRepository(tmp_path / "auth.db")
    users.create_user("tester", PasswordHasher().hash("a long test password"), UserRole.USER)
    provider = LocalAuthProvider(users)
    monkeypatch.setitem(app.dependency_overrides, auth_api.get_auth_provider, lambda: provider)
    monkeypatch.setattr(auth_api, "get_auth_provider", lambda: provider)
    reset_mission_orchestrator()
    session = TestClient(app)
    assert session.post("/api/auth/login", json={"username": "tester", "password": "a long test password"}).status_code == 200
    session.headers["X-CSRF-Token"] = session.cookies["aimeton_csrf"]
    yield session
    session.close()
    reset_mission_orchestrator()


def save(client, service, values):
    result = client.put("/api/user/research-settings/" + service,
                        json={"expected_revision": 0, "settings": values.model_dump(mode="json")})
    assert result.status_code == 200
    return result.json()


@pytest.mark.parametrize("path,service,payload", [
    ("/api/analyze/start", "site-audit", {"url": "https://example.org"}),
    ("/api/analyze", "site-audit", {"url": "https://example.org"}),
    ("/api/company-intelligence", "company-intelligence", {"company_name": "Example"}),
])
def test_total_timeout_includes_acquisition_and_never_reports_success(client, monkeypatch, tmp_path, path, service, payload):
    import app.main as main
    import app.analysis_async_api as async_api
    saved = save(client, service, settings(mission_timeout_seconds=0.02))
    assert saved["execution_enabled"]
    cancelled = []
    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(main, "fetch_site", blocked)
    monkeypatch.setattr(async_api, "fetch_site", blocked)
    monkeypatch.setattr(main, "run_company_intelligence", blocked)
    # Omission of the revision cannot bypass a saved policy.
    response = client.post(path, json=payload)
    if path.endswith("/start"):
        assert response.status_code == 202
        status = client.get(response.json()["status_url"]).json()
        assert status["state"] == "failed"
        assert status["research"]["stop_reason"] == "mission_timeout"
        assert status["result"] is None
    else:
        assert response.status_code == 408
        assert response.json()["detail"] == "mission_timeout"
    assert cancelled == [True]
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        rows = db.execute("SELECT payload FROM research_settings_snapshots").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["effective"]["mission_timeout_seconds"] == 0.02


@pytest.mark.parametrize("deep", [False, True])
def test_saved_cost_cap_cannot_be_bypassed_by_omitting_revision(client, monkeypatch, deep):
    import app.analysis_async_api as async_api
    save(client, "site-audit", ResearchSettings(cost_limit_amount="500"))
    async def unexpected(*args, **kwargs):
        pytest.fail("budget preflight allowed acquisition")
    monkeypatch.setattr(async_api, "fetch_site", unexpected)
    result = client.post("/api/analyze/start", json={"url": "https://example.org", "deep_research": deep, "unlimited_llm_budget": deep})
    assert result.status_code == 409
    assert result.json()["detail"].startswith("budget_enforcement_unavailable")


@pytest.mark.asyncio
async def test_mission_timeout_cancels_search_instead_of_leaving_shielded_work(tmp_path, monkeypatch):
    from app.search_gateway.gateway import SearchGateway
    from app.search_gateway.models import SearchPolicy, SearchRequest
    from app.search_gateway.providers import SearchProvider
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    class Provider(SearchProvider):
        name = "fake"
        paid = False
        cost_amount = Decimal(0)
        cost_currency = "RUB"
        configured = True
        stopped = False
        async def search(self, request, *, timeout_seconds):
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped = True
    provider = Provider()
    gateway = SearchGateway([provider])
    control = ResearchControl(settings=settings(mission_timeout_seconds=0.02))
    with pytest.raises(ResearchInterrupted, match="mission_timeout"):
        await run_controlled(control, lambda: gateway.search(
            SearchRequest(query="example", mission_id="m", correlation_id="c"),
            SearchPolicy(provider_order=("fake",))))
    assert provider.stopped
    assert not gateway._inflight


@pytest.mark.asyncio
async def test_search_retry_count_and_backoff_come_from_user_settings():
    from app.search_gateway.gateway import SearchGateway
    from app.search_gateway.models import SearchPolicy, SearchRequest
    from app.search_gateway.providers import SearchProvider, ProviderError
    class Provider(SearchProvider):
        name = "fake"
        paid = False
        cost_amount = Decimal(0)
        cost_currency = "RUB"
        configured = True
        calls = []
        async def search(self, request, *, timeout_seconds):
            self.calls.append(timeout_seconds)
            raise ProviderError("retryable")
    provider = Provider()
    pauses = []
    async def sleep(seconds):
        pauses.append(seconds)
    gateway = SearchGateway([provider], sleep=sleep)
    with bind_research(ResearchControl(settings=settings(retry_count=2, retry_backoff_seconds=0.125, request_timeout_seconds=77))):
        await gateway.search(SearchRequest(query="example", mission_id="m", correlation_id="c"),
                             SearchPolicy(provider_order=("fake",), retries=0, timeout_seconds=1))
    assert provider.calls == [77, 77, 77]
    assert pauses == [0.125, 0.125]


def test_successful_run_keeps_its_snapshot_when_preferences_change(client, monkeypatch, tmp_path):
    import app.main as main
    from app.heuristics import heuristic_analysis
    from app.research_execution import active_settings
    saved = save(client, "site-audit", settings(mission_timeout_seconds=900, request_timeout_seconds=77))
    async def fetch(url):
        assert active_settings().request_timeout_seconds == 77
        store = ResearchSettingsRepository(tmp_path / "runtime.db")
        store.save(saved["owner_id"], "site-audit", settings(mission_timeout_seconds=10), expected_revision=1)
        return {"final_url": url, "title": "Example", "text": "Evidence"}
    async def analyze(url, title, text):
        assert active_settings().mission_timeout_seconds == 900
        assert active_settings().request_timeout_seconds == 77
        return heuristic_analysis(url, title, text)
    monkeypatch.setattr(main, "fetch_site", fetch)
    monkeypatch.setattr(main, "run_enriched_site_analysis", analyze)
    response = client.post("/api/analyze", json={"url": "https://example.org", "research_settings_revision": 1})
    assert response.status_code == 200
    assert response.json()["mission_id"]
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        payload = json.loads(db.execute("SELECT payload FROM research_settings_snapshots").fetchone()[0])
    assert payload["settings_revision"] == 1
    assert payload["effective"]["request_timeout_seconds"] == 77
    assert client.post("/api/analyze", json={"url": "https://example.org", "research_settings_revision": 1}).status_code == 409
