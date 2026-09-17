from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from app.document_preflight import RelevanceVote, screen_document
from app.evidence_triage import (
    BlockTriageDecision,
    BlockTriageResponse,
    EntityRelation,
    EvidenceRole,
    deterministic_block_decision,
    triage_document_blocks,
)
from app.external_sources import IdentityAnchors
from app.models import IntelligenceSource
from app.search_result_triage import SearchCandidateDecision, SearchTriageResponse, triage_search_candidates


def test_third_party_footer_is_publisher_metadata_not_target_evidence():
    decision = deterministic_block_decision(
        block_id="B0",
        text='Правообладатель сервиса ООО "ЗУН", ИНН 9999999999',
        locator="footer/legal",
        company_name="Алекс Дент",
        anchors=IdentityAnchors(inn="2462215501"),
        source_query_kind="registry",
        source_is_official=False,
    )
    assert decision is not None
    assert not decision.keep
    assert decision.entity_relation == EntityRelation.PUBLISHER
    assert decision.role == EvidenceRole.PUBLISHER_METADATA


def test_related_company_block_is_rejected_even_when_target_inn_is_present():
    decision = deterministic_block_decision(
        block_id="B0",
        text='Похожие компании: ООО "АЛЕКС ДЕНТ", ИНН 2462215501',
        locator="main/related-companies",
        company_name="Алекс Дент",
        anchors=IdentityAnchors(inn="2462215501"),
        source_query_kind="registry",
        source_is_official=False,
    )
    assert decision is not None
    assert not decision.keep
    assert decision.role == EvidenceRole.RELATED_ENTITY


@pytest.mark.asyncio
async def test_fast_block_model_routes_ambiguous_content_without_promoting_publisher():
    blocks = [
        NS(text="Клиника оказывает имплантацию и протезирование пациентам.", locator="main/services"),
        NS(text='Правообладатель каталога ООО "ЗУН"', locator="footer/legal"),
    ]

    async def request(phase, model_type, **kwargs):
        assert phase == "evidence_block_triage"
        return BlockTriageResponse(decisions=[
            BlockTriageDecision(
                block_id="B0",
                keep=True,
                relevance="high",
                entity_relation="target",
                query_kind="other",
                role="primary_fact",
                confidence=.9,
                reason="services_of_target",
            )
        ])

    result = await triage_document_blocks(
        blocks,
        company_name="Алекс Дент",
        anchors=IdentityAnchors(),
        document_url="https://catalog.example/aleksdent",
        document_title="Алекс Дент",
        source_query_kind="other",
        source_is_official=False,
        request_json=request,
    )
    assert result.model_used
    assert [item.block_id for item in result.kept] == ["B0"]
    assert result.kept[0].query_kind == "other"
    assert result.decisions[1].entity_relation == EntityRelation.PUBLISHER


@pytest.mark.asyncio
async def test_triage_model_failure_does_not_expand_ambiguous_third_party_evidence():
    async def unavailable(*args, **kwargs):
        raise RuntimeError("provider down")

    result = await triage_document_blocks(
        [NS(text="В компании работает команда специалистов и используется современное оборудование.", locator="main/p")],
        company_name="Алекс Дент",
        anchors=IdentityAnchors(),
        document_url="https://catalog.example/card",
        document_title="Карточка организации",
        source_query_kind="other",
        source_is_official=False,
        request_json=unavailable,
    )
    assert result.model_unavailable
    assert result.kept == []


@pytest.mark.asyncio
async def test_search_triage_is_discovery_only_and_selects_fetch_candidates():
    sources = [
        IntelligenceSource(
            id="S1", title='ООО "АЛЕКС ДЕНТ" ИНН 2462215501',
            url="https://registry.example/aleks", accessed_at="2026-09-17T00:00:00Z",
            source_class="registry", query_kind="registry",
        ),
        IntelligenceSource(
            id="S2", title="Стоматологическая отрасль Красноярска",
            url="https://news.example/article", accessed_at="2026-09-17T00:00:00Z",
            source_class="news", query_kind="news",
        ),
    ]

    async def request(phase, model_type, **kwargs):
        assert phase == "search_result_triage"
        return SearchTriageResponse(decisions=[
            SearchCandidateDecision(
                source_id="S2", action="skip", relation="unrelated",
                query_kind="news", confidence=.9, reason="generic_industry_article",
            )
        ])

    selected, summary = await triage_search_candidates(
        sources,
        company_name="Алекс Дент",
        anchors=IdentityAnchors(inn="2462215501"),
        official_url="https://aleksdent24.ru/",
        request_json=request,
    )
    assert [item.id for item in selected] == ["S1"]
    assert selected[0].lifecycle_state == "discovery_hint"
    assert summary.total == 2 and summary.selected == 1 and summary.rejected == 1


@pytest.mark.asyncio
async def test_large_document_preflight_uses_fast_classifier_by_default(monkeypatch):
    called = []

    async def fast(phase, model_type, **kwargs):
        called.append(phase)
        return RelevanceVote(decision="include", confidence=.99, reason="useful")

    monkeypatch.setattr("app.document_preflight.request_fast_json", fast)
    fetched = NS(
        normalized_text="x" * 50_000,
        blocks=[NS(text="Company", kind="heading")],
        document=NS(title="Company"),
    )
    result = await screen_document(fetched, company_name="Company", anchors=IdentityAnchors())
    assert result.decision == "include"
    assert called == ["document_preflight"]
