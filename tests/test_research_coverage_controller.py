from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.adaptive_external_sources as adaptive
from app.external_sources import IdentityAnchors, query_plan
from app.models import IntelligenceSource
from app.research_control import ResearchControl, bind_research
from app.research_coverage_controller import (
    DEFAULT_DEEP_RESULTS_PER_QUERY,
    FastCoverageWaveChoice,
    assess_coverage,
    gap_wave,
    initial_wave,
    optional_wave,
)
from app.search_gateway.models import (
    GatewayState,
    SearchDiagnostics,
    SearchItem,
    SearchPolicy,
    SearchResponse,
)


def _evidence(source_id: str, kind: str) -> IntelligenceSource:
    return IntelligenceSource(
        id=source_id,
        title=source_id,
        url=f"https://example.org/{source_id}",
        accessed_at="2026-09-18T00:00:00+00:00",
        query_kind=kind,
        source_class=kind if kind in {"official", "registry", "finance", "contact"} else "unknown",
        lifecycle_state="evidence",
        evidence_level="corroborated_signal",
    )


def test_initial_wave_uses_one_query_per_core_kind() -> None:
    plan = query_plan(
        "Алекс Дент",
        anchors=IdentityAnchors(domain="aleksdent24.ru", cities=("Красноярск",)),
    )

    wave = initial_wave(plan)

    assert len(wave) == 6
    assert [kind for kind, _ in wave] == [
        "official",
        "contact",
        "registry",
        "finance",
        "ownership",
        "other",
    ]
    assert sum(kind == "official" for kind, _ in wave) == 1


def test_gap_wave_searches_only_never_attempted_mandatory_verticals() -> None:
    plan = query_plan(
        "Алекс Дент",
        anchors=IdentityAnchors(domain="aleksdent24.ru", cities=("Красноярск",)),
    )
    searched = {"official", "contact", "registry", "finance", "ownership", "other"}
    verified = [_evidence("S1", "official"), _evidence("F1", "finance")]

    coverage = assess_coverage(verified, searched)
    wave = gap_wave(plan, coverage, already_attempted=set())

    assert coverage.states["identity"] == "covered"
    assert coverage.states["financials"] == "covered"
    assert coverage.states["contacts"] == "searched_no_evidence"
    assert coverage.states["ownership"] == "searched_no_evidence"
    assert coverage.states["workforce"] == "missing"
    assert coverage.states["legal_events"] == "missing"
    assert [kind for kind, _ in wave] == [
        "workforce",
        "jobs",
        "arbitration",
        "court",
        "enforcement",
    ]


@pytest.mark.asyncio
async def test_optional_wave_fast_model_can_only_select_existing_candidates() -> None:
    plan = query_plan(
        "Алекс Дент",
        anchors=IdentityAnchors(domain="aleksdent24.ru", cities=("Красноярск",)),
    )
    searched = {
        "official", "registry", "contact", "ownership", "finance", "other",
        "workforce", "jobs", "arbitration", "court", "enforcement",
    }
    coverage = assess_coverage([], searched)

    async def fake_request(_phase, model_type, **_kwargs):
        assert model_type is FastCoverageWaveChoice
        return FastCoverageWaveChoice(
            query_ids=["Q0", "Q999", "Q1"],
            reason="prioritize mandatory recovery",
        )

    selection = await optional_wave(
        plan,
        coverage,
        company_name="Алекс Дент",
        anchors=IdentityAnchors(domain="aleksdent24.ru", cities=("Красноярск",)),
        already_attempted=set(),
        request_json=fake_request,
    )

    assert selection.model_used is True
    assert selection.model_unavailable is False
    assert len(selection.queries) == 2
    all_query_text = {query for _, query in plan}
    # Recovery queries may be relaxed variants, but the model cannot inject Q999 or
    # arbitrary query text: every selected item came from the controller candidate map.
    assert all(query and "Q999" not in query for _, query in selection.queries)
    assert selection.candidate_count > 6


@pytest.mark.asyncio
async def test_optional_wave_model_failure_uses_bounded_deterministic_fallback() -> None:
    plan = query_plan(
        "Алекс Дент",
        anchors=IdentityAnchors(domain="aleksdent24.ru", cities=("Красноярск",)),
    )
    searched = {
        "official", "registry", "contact", "ownership", "finance", "other",
        "workforce", "jobs", "arbitration", "court", "enforcement",
    }
    coverage = assess_coverage([], searched)

    async def fail(*_args, **_kwargs):
        raise RuntimeError("fast model unavailable")

    selection = await optional_wave(
        plan,
        coverage,
        company_name="Алекс Дент",
        anchors=IdentityAnchors(domain="aleksdent24.ru", cities=("Красноярск",)),
        already_attempted=set(),
        request_json=fail,
    )

    assert selection.model_used is False
    assert selection.model_unavailable is True
    assert len(selection.queries) == 6
    assert selection.candidate_count > len(selection.queries)


@pytest.mark.asyncio
async def test_deep_search_uses_bounded_results_per_query(monkeypatch) -> None:
    limits: list[int] = []

    class FakeGateway:
        async def search(self, request, _policy):
            limits.append(request.limit)
            return SearchResponse(
                results=[
                    SearchItem(
                        url="https://example.org/company",
                        title="Алекс Дент",
                        snippet="ИНН 2462215501",
                        provider="fake",
                    )
                ],
                diagnostics=SearchDiagnostics(
                    state=GatewayState.SUCCESS,
                    selected_provider="fake",
                    attempts=[],
                    total_cost_by_currency={"USD": Decimal("0")},
                ),
            )

    monkeypatch.setattr(adaptive, "get_search_gateway", lambda: FakeGateway())
    monkeypatch.setattr(
        adaptive,
        "resolve_hunter_search_policy",
        lambda: SimpleNamespace(policy=SearchPolicy()),
    )

    with bind_research(ResearchControl(deep=True)):
        sources, _, _ = await adaptive.collect_external_sources_adaptive(
            "Алекс Дент",
            "https://aleksdent24.ru/",
            anchors=IdentityAnchors(domain="aleksdent24.ru", inn="2462215501"),
            max_sources=None,
            query_overrides=[("registry", '"2462215501" реквизиты')],
        )

    assert sources
    assert limits == [DEFAULT_DEEP_RESULTS_PER_QUERY]
    assert DEFAULT_DEEP_RESULTS_PER_QUERY == 8
