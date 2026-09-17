from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.adaptive_external_sources as adaptive
from app.external_sources import IdentityAnchors
from app.search_gateway.models import (
    GatewayState,
    SearchDiagnostics,
    SearchItem,
    SearchPolicy,
    SearchResponse,
)


def _response(url: str, *, state: GatewayState = GatewayState.SUCCESS) -> SearchResponse:
    return SearchResponse(
        results=[
            SearchItem(
                url=url,
                title="Тестовая компания",
                snippet="Красноярск ИНН ОГРН",
                provider="fake",
            )
        ],
        diagnostics=SearchDiagnostics(
            state=state,
            selected_provider="fake",
            attempts=[],
            total_cost_by_currency={"USD": Decimal("0")},
        ),
    )


def _success(url: str = "https://example.org/found") -> SearchResponse:
    return _response(url)


def _empty() -> SearchResponse:
    return SearchResponse(
        results=[],
        diagnostics=SearchDiagnostics(
            state=GatewayState.DEGRADED,
            selected_provider=None,
            attempts=[],
            total_cost_by_currency={},
        ),
    )


def test_relaxed_query_preserves_registration_identifiers_without_region_duplication() -> None:
    anchors = IdentityAnchors(
        domain="example.org",
        inn="1234567890",
        ogrn="1234567890123",
        cities=("Красноярск",),
    )

    query = adaptive.relaxed_query(
        "registry",
        "Тестовая компания",
        region="Красноярск",
        anchors=anchors,
    )

    assert "1234567890" in query
    assert "1234567890123" in query
    assert query.count("Красноярск") == 0
    assert '"' not in query


@pytest.mark.asyncio
async def test_exact_success_does_not_issue_relaxed_fallback(monkeypatch) -> None:
    calls: list[str] = []

    class FakeGateway:
        async def search(self, request, _policy):
            calls.append(request.query)
            return _success(f"https://example.org/{len(calls)}")

    monkeypatch.setattr(adaptive, "get_search_gateway", lambda: FakeGateway())
    monkeypatch.setattr(adaptive, "resolve_hunter_search_policy", lambda: SimpleNamespace(policy=SearchPolicy()))

    sources, notes, diagnostics = await adaptive.collect_external_sources_adaptive(
        "Тестовая компания",
        "https://example.org/",
        region="Красноярск",
        max_sources=100,
        anchors=IdentityAnchors(domain="example.org", cities=("Красноярск",)),
    )

    assert len(calls) == 20
    assert sources
    assert all("query_variant=exact" in source.verification_note for source in sources)
    assert not any("relaxed fallback" in note for note in notes)
    assert diagnostics.state is GatewayState.SUCCESS


@pytest.mark.asyncio
async def test_degraded_exact_with_results_does_not_duplicate_search_wave(monkeypatch) -> None:
    calls: list[str] = []

    class FakeGateway:
        async def search(self, request, _policy):
            calls.append(request.query)
            return _response(f"https://example.org/{len(calls)}", state=GatewayState.DEGRADED)

    monkeypatch.setattr(adaptive, "get_search_gateway", lambda: FakeGateway())
    monkeypatch.setattr(adaptive, "resolve_hunter_search_policy", lambda: SimpleNamespace(policy=SearchPolicy()))

    sources, notes, diagnostics = await adaptive.collect_external_sources_adaptive(
        "Тестовая компания",
        "https://example.org/",
        region="Красноярск",
        max_sources=100,
        anchors=IdentityAnchors(domain="example.org", cities=("Красноярск",)),
    )

    assert len(calls) == 20
    assert sources
    assert not any("relaxed fallback" in note for note in notes)
    assert diagnostics.state is GatewayState.DEGRADED


@pytest.mark.asyncio
async def test_empty_exact_query_runs_one_relaxed_fallback(monkeypatch) -> None:
    calls: list[str] = []

    class FakeGateway:
        async def search(self, request, _policy):
            calls.append(request.query)
            if '"' in request.query:
                return _empty()
            return _success(f"https://relaxed.example/{len(calls)}")

    monkeypatch.setattr(adaptive, "get_search_gateway", lambda: FakeGateway())
    monkeypatch.setattr(adaptive, "resolve_hunter_search_policy", lambda: SimpleNamespace(policy=SearchPolicy()))

    sources, notes, diagnostics = await adaptive.collect_external_sources_adaptive(
        "Тестовая компания",
        "https://example.org/",
        region="Красноярск",
        max_sources=100,
        anchors=IdentityAnchors(domain="example.org", cities=("Красноярск",)),
    )

    assert len(calls) > 17
    assert any('"' not in query for query in calls)
    assert sources
    assert any("query_variant=relaxed" in source.verification_note for source in sources)
    assert any("relaxed fallback" in note for note in notes)
    assert diagnostics.state is GatewayState.DEGRADED


@pytest.mark.asyncio
async def test_tracking_variants_collapse_to_one_discovery_source(monkeypatch) -> None:
    class FakeGateway:
        async def search(self, request, _policy):
            return SearchResponse(
                results=[
                    SearchItem(url="https://example.org/card?utm_source=a", title="A", snippet="x", provider="fake"),
                    SearchItem(url="https://example.org/card?gclid=123", title="A", snippet="x", provider="fake"),
                ],
                diagnostics=SearchDiagnostics(
                    state=GatewayState.SUCCESS,
                    selected_provider="fake",
                    attempts=[],
                    total_cost_by_currency={},
                ),
            )

    monkeypatch.setattr(adaptive, "get_search_gateway", lambda: FakeGateway())
    monkeypatch.setattr(adaptive, "resolve_hunter_search_policy", lambda: SimpleNamespace(policy=SearchPolicy()))

    sources, notes, _ = await adaptive.collect_external_sources_adaptive(
        "Тестовая компания",
        "https://example.org/",
        max_sources=100,
        anchors=IdentityAnchors(domain="example.org"),
        query_overrides=[("registry", "company registry")],
    )

    assert len(sources) == 1
    assert any("canonical URL dedup" in note for note in notes)
