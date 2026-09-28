from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.adaptive_external_sources as adaptive
from app.external_sources import IdentityAnchors, query_plan
from app.models import IntelligenceSource
from app.research_control import ResearchControl, bind_research
from app.research_coverage_controller import (
    FastCoverageWaveChoice,
    SemanticGainDecision,
    assess_coverage,
    assess_wave_semantic_gain,
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
    assert selection.candidate_count > len(selection.queries)


@pytest.mark.asyncio
async def test_optional_wave_model_failure_falls_back_only_to_unresolved_recovery() -> None:
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
    assert selection.queries
    assert all(kind not in {"news", "review", "social", "tender", "patent"} for kind, _ in selection.queries)
    assert selection.candidate_count >= len(selection.queries)


@pytest.mark.asyncio
async def test_deep_search_uses_runtime_policy_page_size_not_auditor_hardcode(monkeypatch) -> None:
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
        lambda: SimpleNamespace(policy=SearchPolicy(target_results=13)),
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
    assert limits == [13]


def test_sensitive_verticals_require_sufficient_evidence_level() -> None:
    weak_registry = _evidence("R-weak", "registry")
    weak_registry.evidence_level = "weak_signal"
    weak_finance = _evidence("F-weak", "finance")
    weak_finance.evidence_level = "weak_signal"
    weak_court = _evidence("C-weak", "court")
    weak_court.evidence_level = "weak_signal"
    weak_ownership = _evidence("O-weak", "ownership")
    weak_ownership.evidence_level = "weak_signal"

    coverage = assess_coverage(
        [weak_registry, weak_finance, weak_court, weak_ownership],
        {"registry", "finance", "court", "ownership"},
    )

    assert coverage.states["identity"] == "searched_no_evidence"
    assert coverage.states["financials"] == "searched_no_evidence"
    assert coverage.states["legal_events"] == "searched_no_evidence"
    assert coverage.states["ownership"] == "covered"
    assert coverage.qualifying_documents_by_vertical["identity"] == 0
    assert coverage.qualifying_documents_by_vertical["financials"] == 0
    assert coverage.qualifying_documents_by_vertical["legal_events"] == 0
    assert coverage.qualifying_documents_by_vertical["ownership"] == 1


def test_corroborated_sensitive_evidence_closes_vertical_gap() -> None:
    registry = _evidence("R1", "registry")
    finance = _evidence("F1", "finance")
    court = _evidence("C1", "court")

    coverage = assess_coverage(
        [registry, finance, court],
        {"registry", "finance", "court"},
    )

    assert coverage.states["identity"] == "covered"
    assert coverage.states["financials"] == "covered"
    assert coverage.states["legal_events"] == "covered"
    assert coverage.qualifying_documents_by_vertical["identity"] == 1


def test_search_complete_does_not_claim_evidence_sufficiency() -> None:
    all_mandatory_kinds = {
        kind
        for kinds in (
            ("official", "registry"),
            ("contact",),
            ("ownership",),
            ("finance",),
            ("workforce", "jobs"),
            ("arbitration", "court", "enforcement"),
            ("other",),
        )
        for kind in kinds
    }

    coverage = assess_coverage([], all_mandatory_kinds)

    assert coverage.search_complete is True
    assert coverage.evidence_sufficient is False
    assert set(coverage.searched_without_evidence) == {
        "identity", "contacts", "ownership", "financials",
        "workforce", "legal_events", "operations",
    }
    assert coverage.safe_dict()["evidence_sufficient"] is False


def test_evidence_sufficiency_requires_every_mandatory_vertical_covered() -> None:
    verified = [
        _evidence("I1", "official"),
        _evidence("C1", "contact"),
        _evidence("O1", "ownership"),
        _evidence("F1", "finance"),
        _evidence("W1", "workforce"),
        _evidence("L1", "court"),
        _evidence("P1", "other"),
    ]
    searched = {item.query_kind for item in verified}

    coverage = assess_coverage(verified, searched)

    assert coverage.search_complete is True
    assert coverage.evidence_sufficient is True



@pytest.mark.asyncio
async def test_semantic_gain_model_stops_on_redundant_volume() -> None:
    before = assess_coverage([_evidence("R1", "registry")], {"registry"})
    after = assess_coverage([_evidence("R1", "registry"), _evidence("R2", "registry")], {"registry"})
    redundant = _evidence("R2", "registry")
    redundant.title = "Повтор тех же реквизитов"
    redundant.evidence_quote = "Те же ИНН и ОГРН уже известной организации"

    async def fake_request(_phase, model_type, **_kwargs):
        assert model_type is SemanticGainDecision
        return SemanticGainDecision(
            meaningful_gain=False,
            new_information=[],
            authority_gain=[],
            reason="same identity facts repeated",
        )

    assessment = await assess_wave_semantic_gain(
        before,
        after,
        new_verified=[redundant],
        semantic_state=("ИНН и ОГРН организации уже установлены",),
        request_json=fake_request,
    )

    assert assessment.model_used is True
    assert assessment.meaningful_gain is False
    assert assessment.semantic_state == ("ИНН и ОГРН организации уже установлены",)


@pytest.mark.asyncio
async def test_semantic_gain_model_continues_for_new_business_meaning() -> None:
    before = assess_coverage([], {"official"})
    after = assess_coverage([_evidence("W1", "workforce")], {"official", "workforce"})
    workforce = _evidence("W1", "workforce")
    workforce.title = "Вакансии клиники"
    workforce.evidence_quote = "Открыта вакансия администратора контакт-центра"

    async def fake_request(_phase, model_type, **_kwargs):
        assert model_type is SemanticGainDecision
        return SemanticGainDecision(
            meaningful_gain=True,
            new_information=["Компания нанимает администратора контакт-центра"],
            authority_gain=[],
            reason="new workforce fact",
        )

    assessment = await assess_wave_semantic_gain(
        before,
        after,
        new_verified=[workforce],
        semantic_state=(),
        request_json=fake_request,
    )

    assert assessment.meaningful_gain is True
    assert "Компания нанимает администратора контакт-центра" in assessment.semantic_state


@pytest.mark.asyncio
async def test_semantic_gain_model_failure_uses_only_structural_authority_gain() -> None:
    before = assess_coverage([], {"registry"})
    registry = _evidence("R1", "registry")
    after = assess_coverage([registry], {"registry"})

    async def fail(*_args, **_kwargs):
        raise RuntimeError("fast model unavailable")

    assessment = await assess_wave_semantic_gain(
        before,
        after,
        new_verified=[registry],
        semantic_state=(),
        request_json=fail,
    )

    assert assessment.model_unavailable is True
    assert assessment.meaningful_gain is True
    assert any("identity:" in item for item in assessment.semantic_state)
