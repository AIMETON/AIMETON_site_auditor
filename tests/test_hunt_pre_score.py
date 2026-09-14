from __future__ import annotations
import asyncio

import pytest

from app import discovery
from app.hunter_search_policy_authority import HunterSearchPolicyAuthority, ResolvedHunterSearchPolicy
from app.models import HuntRequest
from app.search_gateway.models import (
    GatewayState,
    SearchDiagnostics,
    SearchItem,
    SearchPolicy,
    SearchResponse,
)


@pytest.fixture(autouse=True)
def _stub_canonical_hunter_policy(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "resolve_hunter_search_policy",
        lambda: ResolvedHunterSearchPolicy(
            policy=SearchPolicy(),
            authority=HunterSearchPolicyAuthority.ADMIN,
            selected_policy_fingerprint="sha256:test-admin",
            env_policy_fingerprint="sha256:test-env",
            admin_projection_fingerprint="sha256:test-admin",
            policy_equivalent=False,
            admin_policy_persisted=True,
        ),
    )


def _request(**overrides) -> HuntRequest:
    data = {
        "region": "Красноярск",
        "industries": ["стоматология"],
        "focus": ["автоматизация"],
        "minimum_pre_score": 35,
        "deep_audit_score": 60,
        "max_queries": 1,
        "results_per_query": 10,
    }
    data.update(overrides)
    return HuntRequest(**data)


def _gateway_for(fake_search):
    class FakeGateway:
        async def search(self, request, _policy):
            raw = await fake_search(request.query, request.limit)
            return SearchResponse(
                results=[
                    SearchItem(
                        url=item["url"],
                        title=item.get("title", ""),
                        snippet=item.get("content", ""),
                        provider="fake",
                    )
                    for item in raw
                ],
                diagnostics=SearchDiagnostics(
                    state=GatewayState.SUCCESS,
                    selected_provider="fake",
                ),
            )

    return FakeGateway()


def _degraded_gateway_for(fake_search):
    class FakeGateway:
        async def search(self, request, _policy):
            raw = await fake_search(request.query, request.limit)
            return SearchResponse(
                results=[
                    SearchItem(
                        url=item["url"],
                        title=item.get("title", ""),
                        snippet=item.get("content", ""),
                        provider="fallback",
                    )
                    for item in raw
                ],
                diagnostics=SearchDiagnostics(
                    state=GatewayState.DEGRADED,
                    selected_provider="fallback",
                    fallback_used=True,
                ),
            )

    return FakeGateway()


def test_pre_score_is_explainable_and_independent_of_link_count():
    req = _request()
    first = discovery._pre_score(
        req,
        "Стоматология Красноярск — каталог услуг",
        "Проектирование, подбор и автоматизация записи",
        "https://clinic.ru/services",
    )
    second = discovery._pre_score(
        req,
        "Стоматология Красноярск — каталог услуг",
        "Проектирование, подбор и автоматизация записи",
        "https://clinic.ru/services?many=links",
    )

    assert first.status == "calculated"
    assert first.score == second.score
    assert first.factors["region_match"] == 25
    assert first.factors["commercial_choice"] == 20
    assert first.factors["focus_match"] == 10


def test_missing_text_is_explicit_insufficient_data_not_zero():
    result = discovery._pre_score(_request(), "", "", "https://example.ru")

    assert result.status == "insufficient_data"
    assert result.score is None
    assert result.factors["region_match"] is None


@pytest.mark.asyncio
async def test_deep_processing_is_not_called_below_threshold(monkeypatch):
    req = _request(deep_audit_score=100)
    monkeypatch.setattr(discovery, "_build_queries", lambda _req: ["query"])

    async def fake_search(_query: str, _limit: int):
        return [{
            "url": "https://clinic.ru",
            "title": "Стоматология Красноярск",
            "content": "Каталог услуг и подбор лечения",
        }]

    async def forbidden_fetch(_url: str):
        raise AssertionError("fetch_site must not run below deep_audit_score")

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: _gateway_for(fake_search))
    monkeypatch.setattr(discovery, "fetch_site", forbidden_fetch)

    result = await discovery.run_hunt(req)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.pre_score_status == "calculated"
    assert candidate.deep_analysis_performed is False
    assert candidate.analysis is None


@pytest.mark.asyncio
async def test_deep_processing_runs_only_after_threshold(monkeypatch):
    req = _request(deep_audit_score=50)
    monkeypatch.setattr(discovery, "_build_queries", lambda _req: ["query"])

    async def fake_search(_query: str, _limit: int):
        return [{
            "url": "https://clinic.ru",
            "title": "Стоматология Красноярск — каталог услуг",
            "content": "Производство, проект и автоматизация записи",
        }]

    calls = {"fetch": 0}

    async def fake_fetch(url: str):
        calls["fetch"] += 1
        return {
            "final_url": url,
            "title": "Клиника Красноярск",
            "text": "Красноярск стоматология услуги запись автоматизация " * 30,
        }

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: _gateway_for(fake_search))
    monkeypatch.setattr(discovery, "fetch_site", fake_fetch)

    result = await discovery.run_hunt(req)

    assert calls["fetch"] == 1
    assert result.candidates[0].deep_analysis_performed is True
    assert result.candidates[0].analysis is not None


@pytest.mark.asyncio
async def test_insufficient_candidate_never_starts_deep_processing(monkeypatch):
    req = _request(minimum_pre_score=0, deep_audit_score=0)
    monkeypatch.setattr(discovery, "_build_queries", lambda _req: ["query"])

    async def fake_search(_query: str, _limit: int):
        return [{"url": "https://example.ru", "title": "", "content": ""}]

    async def forbidden_fetch(_url: str):
        raise AssertionError("insufficient_data candidate must not be fetched")

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: _gateway_for(fake_search))
    monkeypatch.setattr(discovery, "fetch_site", forbidden_fetch)

    result = await discovery.run_hunt(req)

    assert result.candidates[0].pre_score_status == "insufficient_data"
    assert result.candidates[0].preliminary_score is None


@pytest.mark.asyncio
async def test_degraded_gateway_state_is_visible_in_hunt_api(monkeypatch):
    req = _request(deep_audit_score=100)
    monkeypatch.setattr(discovery, "_build_queries", lambda _req: ["query"])

    async def fake_search(_query: str, _limit: int):
        return [{
            "url": "https://clinic.ru",
            "title": "Стоматология Красноярск",
            "content": "Каталог услуг",
        }]

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: _degraded_gateway_for(fake_search))

    result = await discovery.run_hunt(req)

    assert result.search is not None
    assert result.search.state == GatewayState.DEGRADED
    assert result.search.fallback_used is True

@pytest.mark.asyncio
async def test_search_query_exception_is_contained_and_returns_partial_result(monkeypatch):
    class BrokenGateway:
        async def search(self, _request, _policy):
            raise RuntimeError("provider_transport_broke")

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: BrokenGateway())
    monkeypatch.setattr(discovery, "generate_hunter_query_plan", lambda **kwargs: _async_value(None))
    result = await discovery.run_hunt(_request(max_queries=2))

    assert result.candidates == []
    assert result.search.state == "unavailable"
    assert any("Изолированы ошибки поисковых направлений: 2" in note for note in result.notes)


async def _async_value(value):
    return value

@pytest.mark.asyncio
async def test_unexpected_candidate_failure_is_isolated_as_shallow_observation(monkeypatch):
    req = _request(deep_audit_score=50)
    monkeypatch.setattr(discovery, "_build_queries", lambda _req: ["query"])

    async def fake_search(_query: str, _limit: int):
        return [{"url": "https://clinic.ru", "title": "Стоматология Красноярск", "content": "услуги стоматология Красноярск"}]
    async def broken_fetch(_url: str):
        raise RuntimeError("unexpected_fetch_runtime_error")

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: _gateway_for(fake_search))
    monkeypatch.setattr(discovery, "fetch_site", broken_fetch)
    result = await discovery.run_hunt(req)

    assert len(result.candidates) == 1
    assert not result.candidates[0].deep_analysis_performed
    assert any("RuntimeError" in reason for reason in result.candidates[0].reasons)

@pytest.mark.asyncio
async def test_candidate_phase_has_no_aggregate_timeout_and_reports_progress(monkeypatch):
    req = _request(deep_audit_score=50)
    monkeypatch.setattr(discovery, "_build_queries", lambda _req: ["query"])

    async def fake_search(_query: str, _limit: int):
        return [{"url": "https://clinic.ru", "title": "Стоматология Красноярск", "content": "услуги стоматология Красноярск"}]
    async def slow_fetch(_url: str):
        await asyncio.sleep(0.03)
        return {
            "final_url": "https://clinic.ru",
            "title": "Клиника Красноярск",
            "text": "Красноярск стоматология услуги " * 30,
        }

    monkeypatch.setattr(discovery, "get_search_gateway", lambda: _gateway_for(fake_search))
    monkeypatch.setattr(discovery, "fetch_site", slow_fetch)
    progress = []
    result = await discovery.run_hunt(req, on_progress=progress.append)

    assert len(result.candidates) == 1
    assert result.candidates[0].deep_analysis_performed is True
    assert progress[-1]["phase"] == "inspecting_candidates"
    assert progress[-1]["processed_candidates"] == 1
    assert progress[-1]["total_candidates"] == 1
    assert len(progress[-1]["candidates"]) == 1
    assert not any("лимиту времени" in note for note in result.notes)
