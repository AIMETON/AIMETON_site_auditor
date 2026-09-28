from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.evidence_triage import (
    BlockTriageDecision,
    BlockTriageResponse,
    triage_document_blocks,
)
from app.external_sources import IdentityAnchors
from app.models import IntelligenceSource
from app.research_control import ResearchControl, bind_research
from app.search_result_triage import (
    SearchCandidateDecision,
    SearchTriageResponse,
    triage_search_candidates,
)


@pytest.mark.asyncio
async def test_deep_search_triage_keeps_fast_llm_for_ambiguous_candidates(monkeypatch) -> None:
    monkeypatch.setenv("AIMETON_COMPILED_TWO_CALL", "1")
    monkeypatch.setenv("AIMETON_MINIMAL_LLM_ROUTING", "1")
    calls: list[str] = []

    async def fast(phase, model_type, **kwargs):
        calls.append(phase)
        return SearchTriageResponse(decisions=[
            SearchCandidateDecision(
                source_id="H1",
                action="fetch",
                relation="possible_target",
                query_kind="news",
                confidence=.8,
                reason="semantic relevance requires fetch",
            )
        ])

    source = IntelligenceSource(
        id="H1",
        title="Generic result",
        url="https://example.net/item",
        snippet="Possible business context without exact entity anchors",
        accessed_at="2026-09-20T00:00:00+00:00",
        source_class="news",
        query_kind="other",
        result_kind="other",
    )

    with bind_research(ResearchControl(deep=True)):
        selected, summary = await triage_search_candidates(
            [source],
            company_name="Target Company",
            anchors=IdentityAnchors(),
            official_url="https://target.example/",
            request_json=fast,
        )

    assert calls == ["search_result_triage"]
    assert [item.id for item in selected] == ["H1"]
    assert summary.model_used is True
    assert summary.model_selected == 1


@pytest.mark.asyncio
async def test_deep_evidence_triage_keeps_fast_llm_for_ambiguous_blocks(monkeypatch) -> None:
    monkeypatch.setenv("AIMETON_COMPILED_TWO_CALL", "1")
    monkeypatch.setenv("AIMETON_MINIMAL_LLM_ROUTING", "1")
    calls: list[str] = []

    async def fast(phase, model_type, **kwargs):
        calls.append(phase)
        return BlockTriageResponse(decisions=[
            BlockTriageDecision(
                block_id="B0",
                keep=True,
                relevance="medium",
                entity_relation="target",
                query_kind="news",
                role="context",
                confidence=.8,
                reason="target context",
            )
        ])

    blocks = [
        SimpleNamespace(
            text="General market commentary that may describe the target company context",
            locator="main/p[1]",
        )
    ]

    with bind_research(ResearchControl(deep=True)):
        outcome = await triage_document_blocks(
            blocks,
            company_name="Target Company",
            anchors=IdentityAnchors(),
            document_url="https://example.net/article",
            document_title="Article",
            source_query_kind="news",
            source_is_official=False,
            request_json=fast,
        )

    assert calls == ["evidence_block_triage"]
    assert outcome.model_used is True
    assert outcome.decisions[0].keep is True
