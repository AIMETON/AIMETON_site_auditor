from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app import audit_dialogue as dialogue
from app import external_verification as verification
from app import verified_analysis as audit
from app.external_sources import IdentityAnchors
from app.heuristics import heuristic_analysis
from app.models import ChatMessage, ChatRequest, CompanyFact, EvidenceSource, IntelligenceSource
from app.routerai_context import compact_routerai_sources
from app.routerai_evidence_units import project_sources
from app.scraper import extract_visible_text


def analysis():
    return heuristic_analysis("https://example.org", "Example", "Компания производит оборудование")


def source(identifier="H1", url="https://registry.example/company"):
    return IntelligenceSource(id=identifier, title="Example", url=url,
        accessed_at="2026-09-13T00:00:00Z", source_class="registry", query_kind="registry")


def test_scraper_keeps_late_facts_and_aggregation_preserves_pages():
    from app.mission_bounded_runtime import _aggregate_evidence
    body = "Начало " + "x" * 46000 + " ПОЗДНИЙ_РУКОВОДИТЕЛЬ"
    _, text = extract_visible_text(f"<html><title>Example</title><p>{body}</p></html>")
    assert "ПОЗДНИЙ_РУКОВОДИТЕЛЬ" in text
    combined = _aggregate_evidence([
        {"final_url": "https://example.org", "text": text},
        {"final_url": "https://example.org/team", "text": "ПОСЛЕДНЯЯ_СТРАНИЦА"},
    ])
    assert text in combined and combined.endswith("ПОСЛЕДНЯЯ_СТРАНИЦА")


def test_verified_evidence_projection_keeps_tail_and_reaches_all_verticals():
    quote = "x" * 1500 + "ПОЗДНИЙ_ФАКТ"
    record = {"id": "R1", "lifecycle_state": "evidence", "evidence_quote": quote,
              "query_kind": "registry", "evidence_digest": "sha256:" + "a" * 64}
    compact = compact_routerai_sources([record])
    assert compact[0]["snippet"] == quote
    assert project_sources(compact, {"other"}, ("id", "snippet"))[0]["snippet"] == quote


@pytest.mark.asyncio
async def test_verification_preserves_every_block_and_rejects_wrong_entity(monkeypatch):
    text = "ИНН 7707083893 " + "x" * 9000 + " ПОЗДНИЙ_ПРОДУКТ"
    block = NS(text=text, locator="body/p[1]")
    fetched = NS(normalized_text=text, blocks=[block],
        document=NS(url="https://registry.example/company", title="Example", accessed_at=datetime.now(timezone.utc)),
        normalized_content_digest="sha256:" + "a" * 64, diagnostics=NS(path=NS(value="static")))
    class Pipeline:
        async def fetch_hint(self, hint, source, policy):
            return fetched
        def promote_quote(self, document, *, locator, quote):
            assert quote in block.text and locator == block.locator
            return NS(evidence=NS(quote=quote, locator=locator,
                digest="sha256:" + hashlib.sha256(quote.encode()).hexdigest()))
    monkeypatch.setattr(verification, "get_document_pipeline", lambda: Pipeline())
    sources = [source()]
    sources[0].classification_state = "ambiguous"  # discovery ambiguity is resolved against the fetched document
    verified = await verification.verify_external_sources(sources, company_name="Example",
        anchors=IdentityAnchors(inn="7707083893"), preserve_blocks=True)
    pieces = [item for item in verified if "-b" in item.id]
    assert "".join(p.evidence_quote for p in sorted(pieces, key=lambda p: int(p.id.rsplit('-', 1)[1]))) == text
    assert all(p.evidence_digest and p.evidence_locator for p in pieces)
    assert len(sources) == len(verified)
    rejected = [source("H2")]
    assert await verification.verify_external_sources(rejected, company_name="Other",
        anchors=IdentityAnchors(inn="1234567890"), preserve_blocks=True) == []
    assert rejected[0].lifecycle_state == "source_candidate"


@pytest.mark.asyncio
async def test_verification_timeout_keeps_partial_state(monkeypatch):
    class Pipeline:
        async def fetch_hint(self, *args):
            await asyncio.Event().wait()
    monkeypatch.setattr(verification, "get_document_pipeline", lambda: Pipeline())
    sources = [source()]
    assert await verification.verify_external_sources(sources, company_name="Example",
        anchors=IdentityAnchors(), timeout_seconds=0.01) == []
    assert "Лимит времени" in sources[0].verification_note


def test_merge_retains_previous_periods_conflicts_and_remaps_sources():
    previous, fresh = analysis(), analysis()
    previous.company_facts = [CompanyFact(field="revenue", value="10", period="2024", source_ids=["S1"])]
    fresh.company_facts = [CompanyFact(field="revenue", value="20", period="2025", source_ids=["S1"]),
                          CompanyFact(field="revenue", value="30", period="2024", source_ids=["S1"])]
    fresh.sources = [EvidenceSource(id="S1", title="New", url=fresh.url, accessed_at="2026-09-13T00:00:00Z", evidence_quote="20")]
    merged = dialogue.merge_profile(previous, fresh, "1234567890abcdef")
    assert [(f.value, f.period) for f in merged.company_facts] == [("10", "2024"), ("20", "2025"), ("30", "2024")]
    assert merged.company_facts[1].source_ids == ["r1234567890ab-S1"]
    assert "сверка" in merged.company_facts[2].note
    assert len(previous.company_facts) == 1
    assert merged.readiness.client_release_eligible is False


@pytest.mark.asyncio
async def test_chat_without_search_keeps_feedback_separate_and_persists(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider call without explicit refinement")
    async def reply(*args): return "Проверим сведения"
    monkeypatch.setattr(dialogue, "fetch_site", forbidden)
    monkeypatch.setattr(dialogue, "chat_with_routerai", reply)
    original = analysis()
    request = ChatRequest(analysis=original, messages=[ChatMessage(role="user", content="У них 50 филиалов")])
    response = await dialogue.run_audit_dialogue(request)
    result = response["analysis"]
    assert result.profile_revision == 1
    assert result.user_clarifications == ["У них 50 филиалов"]
    assert result.company_facts == original.company_facts
    assert response["search_queries"] == []
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as db:
        payload, digest = db.execute("SELECT payload, digest FROM audit_dialogue_revisions").fetchone()
    assert hashlib.sha256(payload.encode()).hexdigest() == digest
    assert json.loads(payload)["after"]["profile_revision"] == 1


@pytest.mark.asyncio
async def test_chat_search_uses_feedback_and_keeps_previous_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    async def fetch(*args): return {"final_url": "https://example.org", "title": "Example", "text": "official"}
    calls = []
    async def enrich(*args, research_queries):
        calls.extend(research_queries)
        fresh = analysis()
        fresh.company_facts = [CompanyFact(field="products", value="Станки")]
        return fresh
    async def reply(*args): return "Найден новый продукт"
    monkeypatch.setattr(dialogue, "fetch_site", fetch)
    monkeypatch.setattr(dialogue, "run_verified_enriched_site_analysis", enrich)
    monkeypatch.setattr(dialogue, "chat_with_routerai", reply)
    req = ChatRequest(analysis=analysis(), refine_search=True,
                      messages=[ChatMessage(role="user", content="Уточни станки и филиалы")])
    response = await dialogue.run_audit_dialogue(req)
    assert len(calls) == 3 and "станки" in calls[0][1]
    assert response["added_facts"] == 1
    async def failure(*args, **kwargs): raise TimeoutError()
    monkeypatch.setattr(dialogue, "run_verified_enriched_site_analysis", failure)
    req.analysis = response["analysis"]
    failed = await dialogue.run_audit_dialogue(req)
    assert failed["analysis"].company_facts == req.analysis.company_facts
    assert failed["analysis"].research_status["stage"] == "refinement_failed"


@pytest.mark.asyncio
async def test_audit_passes_only_verified_evidence_to_llm(monkeypatch):
    sources = [source(), source("H2")]
    async def collect(*args, **kwargs): return sources, [], audit.SearchDiagnostics(state="success")
    async def verify(items, **kwargs):
        items[0].lifecycle_state = "evidence"
        items[0].evidence_quote = "Проверенный факт"
        items[0].evidence_digest = "sha256:" + "a" * 64
        return [items[0]]
    async def synthesize(url, title, text, records):
        assert text.endswith("ПОЗДНИЙ_ФАКТ")
        assert [r["id"] for r in records] == ["H1"]
        return analysis()
    monkeypatch.setattr(audit, "collect_external_sources_adaptive", collect)
    monkeypatch.setattr(audit, "verify_external_sources", verify)
    monkeypatch.setattr(audit, "analyze_with_routerai", synthesize)
    result = await audit._run_verified_enriched_site_analysis("https://example.org", "Example", "x" * 46000 + "ПОЗДНИЙ_ФАКТ")
    assert result.research_status["discovery_hints"] == 1
    assert result.research_status["evidence_records"] == 1
    assert result.readiness.client_release_eligible is False


@pytest.mark.asyncio
async def test_rollback_monolith_refuses_destructive_long_input(monkeypatch):
    from app import routerai_runtime as runtime
    from app.routerai_evidence_units import EvidenceCoverageOverflow
    monkeypatch.setenv("ROUTERAI_SPLIT_SYNTHESIS", "false")
    async def forbidden(*args):
        raise AssertionError("Provider must not receive prefix-cut input")
    monkeypatch.setattr(runtime, "analyze_with_routerai", forbidden)
    with pytest.raises(EvidenceCoverageOverflow, match="legacy_monolith_cannot_cover_input"):
        await runtime.run_bounded_routerai_analysis("https://example.org", "Example", "x" * 30001)
