from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.evidence_triage import triage_document_blocks
from app.external_sources import IdentityAnchors
from app.models import IntelligenceSource
from app.research_control import ResearchControl, bind_research
from app.search_result_triage import triage_search_candidates


@pytest.mark.asyncio
async def test_compiled_deep_search_triage_does_not_call_llm(monkeypatch) -> None:
    monkeypatch.setenv("AIMETON_COMPILED_TWO_CALL", "1")
    monkeypatch.setenv("AIMETON_MINIMAL_LLM_ROUTING", "1")

    async def forbidden(*args, **kwargs):
        raise AssertionError("LLM triage must not run in compiled deep mode")

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
            request_json=forbidden,
        )

    assert selected == []
    assert summary.model_used is False
    assert summary.model_unavailable is False


@pytest.mark.asyncio
async def test_compiled_deep_evidence_triage_rejects_ambiguous_without_llm(monkeypatch) -> None:
    monkeypatch.setenv("AIMETON_COMPILED_TWO_CALL", "1")
    monkeypatch.setenv("AIMETON_MINIMAL_LLM_ROUTING", "1")

    async def forbidden(*args, **kwargs):
        raise AssertionError("LLM evidence triage must not run in compiled deep mode")

    blocks = [
        SimpleNamespace(
            text="General market commentary without target identity anchors",
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
            request_json=forbidden,
        )

    assert outcome.model_used is False
    assert outcome.model_unavailable is False
    assert outcome.decisions[0].keep is False
    assert outcome.decisions[0].reason == "compiled_deep_deterministic_triage"
