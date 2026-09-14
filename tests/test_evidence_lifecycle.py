from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app import company_intelligence_runtime as runtime
from app.document_pipeline import DocumentPipeline
from app.document_pipeline.models import (
    DocumentDiagnostics,
    FetchPath,
    FetchedDocument,
)
from app.external_sources import to_llm_sources
from app.heuristics import heuristic_analysis
from app.models import IntelligenceSource
from app.sef.models import Document, DocumentFetchState


def hint() -> IntelligenceSource:
    return IntelligenceSource(
        id="H1",
        title="Поисковый результат",
        url="https://example.com/about",
        snippet="Компания сообщает о выручке 10 млрд рублей.",
        accessed_at="2026-07-22T00:00:00+00:00",
        source_class="official",
        query_kind="finance",
        result_kind="finance",
        classification_state="ambiguous",
    )


def fetched_document() -> FetchedDocument:
    from app.document_pipeline.extractor import extract_html

    html = (
        "<html><head><title>Example — официальный сайт</title></head><body>"
        "<p>Example производит оборудование. "
        "Подтвержденная информация первичного документа.</p></body></html>"
    )
    extraction = extract_html(html, base_url="https://example.com/about")
    digest = "sha256:" + "1" * 64
    return FetchedDocument(
        document=Document(
            id="doc_test",
            mission_id="mission_test",
            source_id="H1",
            correlation_id="corr_test",
            url="https://example.com/about",
            title="Example — официальный сайт",
            accessed_at=datetime.now(UTC),
            fetch_status=DocumentFetchState.FETCHED,
            content_digest=digest,
            media_type="text/html",
        ),
        raw_content_digest="sha256:" + "2" * 64,
        normalized_content_digest=digest,
        normalized_text=extraction.text,
        blocks=extraction.blocks,
        links=extraction.links,
        tables=extraction.tables,
        diagnostics=DocumentDiagnostics(
            request_fingerprint="sha256:" + "3" * 64,
            path=FetchPath.STATIC,
            raw_bytes=len(html),
            latency_ms=1,
        ),
    )


class FakePipeline:
    def __init__(self, result=None, error=None):
        self.fetch_hint = AsyncMock(return_value=result, side_effect=error)

    promote_quote = staticmethod(DocumentPipeline.promote_quote)


def test_search_snippet_is_not_evidence():
    source = hint()
    assert source.lifecycle_state == "discovery_hint"
    assert source.evidence_level == "unverified_mention"
    assert source.evidence_quote is None
    payload = to_llm_sources([source])[0]
    assert payload["lifecycle_state"] == "discovery_hint"
    assert payload["evidence_level"] == "unverified_mention"


@pytest.mark.asyncio
async def test_fetched_document_promotes_candidate_to_evidence(monkeypatch):
    source = hint()
    pipeline = FakePipeline(result=fetched_document())

    async def fake_llm(url, title, text, sources):
        assert sources[0]["lifecycle_state"] == "evidence"
        assert sources[0]["evidence_level"] == "confirmed_fact"
        assert sources[0]["snippet"] == sources[0]["evidence_quote"]
        assert "10 млрд" not in sources[0]["snippet"]
        return heuristic_analysis(url, title, text)

    monkeypatch.setattr(runtime, "get_document_pipeline", lambda: pipeline)
    monkeypatch.setattr(runtime, "analyze_with_routerai", fake_llm)

    analysis = await runtime._analyze_site_with_sources(
        "https://example.com/about",
        [source],
        [],
    )

    assert source.lifecycle_state == "evidence"
    assert source.document_url == "https://example.com/about"
    assert source.document_accessed_at
    assert source.evidence_quote
    assert source.document_digest
    assert source.evidence_locator == "body/p[1]"
    assert source.evidence_digest
    assert source.fetch_path == "static"
    assert source.evidence_level == "confirmed_fact"
    assert analysis.sources
    evidence = analysis.sources[-1]
    assert evidence.document_url == "https://example.com/about"
    assert evidence.evidence_quote


@pytest.mark.asyncio
async def test_official_candidate_selection_does_not_depend_on_unfetched_redirect(monkeypatch):
    # External discovery is now attempted, but cannot be promoted from an
    # unrelated/unfetched redirect. Keep this verification offline.
    monkeypatch.setattr("app.external_verification.get_document_pipeline",
                        lambda: FakePipeline(error=ValueError("unavailable")))
    unrelated = hint()
    unrelated.url = "https://catalog.example.org/example"
    unrelated.source_class = "registry"
    official = hint()
    pipeline = FakePipeline(result=fetched_document())

    monkeypatch.setattr(runtime, "get_document_pipeline", lambda: pipeline)
    monkeypatch.setattr(
        runtime,
        "analyze_with_routerai",
        AsyncMock(
            return_value=heuristic_analysis(
                "https://example.com/about",
                "Example",
                "Подтвержденная информация первичного документа.",
            )
        ),
    )

    analysis = await runtime._analyze_site_with_sources(
        "https://example.com/about",
        [unrelated, official],
        [],
    )

    assert analysis.url == "https://example.com/about"
    assert official.lifecycle_state == "evidence"
    assert unrelated.lifecycle_state == "source_candidate"
    assert unrelated.evidence_quote is None


@pytest.mark.asyncio
async def test_failed_document_load_does_not_promote_hint(monkeypatch):
    source = hint()
    monkeypatch.setattr(
        runtime,
        "get_document_pipeline",
        lambda: FakePipeline(error=ValueError("document unavailable")),
    )

    with pytest.raises(ValueError):
        await runtime._analyze_site_with_sources(
            "https://example.com/about",
            [source],
            [],
        )

    assert source.lifecycle_state == "discovery_hint"
    assert source.evidence_level == "unverified_mention"
    assert source.evidence_quote is None


def test_evidence_source_requires_traceable_document_fields_when_created_by_runtime():
    source = hint()
    assert source.document_url is None
    assert source.document_accessed_at is None
    assert source.verification_note.startswith("Поисковый сниппет")
