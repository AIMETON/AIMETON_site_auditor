from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models import AnalyzeRequest, ChatRequest, CompanyFact
from app.research_control import ResearchControl, bind_research, deep_research_enabled, record_llm_start, record_llm_usage
from app import routerai_profile_extraction as extraction
from app.routerai_split_synthesis import SplitSynthesisPhaseError


def test_uncapped_consent_is_explicit_and_paired():
    assert not AnalyzeRequest(url="https://example.org").deep_research
    for fields in ({"deep_research": True}, {"unlimited_llm_budget": True}):
        with pytest.raises(ValidationError):
            AnalyzeRequest(url="https://example.org", **fields)
    request = AnalyzeRequest(url="https://example.org", deep_research=True, unlimited_llm_budget=True)
    assert request.deep_research


@pytest.mark.asyncio
async def test_deep_extraction_processes_beyond_fast_path_and_retains_many_people(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    calls = []
    async def request(phase, model_type, **kwargs):
        calls.append((phase, kwargs["max_tokens"]))
        if phase == "profile_identity_core":
            return model_type(company_name="Company", business_summary="Full profile")
        if phase == "profile_management":
            return model_type(company_facts=[{"field": "executives", "value": f"Manager {i}", "source_ids": ["S1"]} for i in range(30)])
        return model_type()
    control = ResearchControl(deep=True)
    with bind_research(control):
        result = await extraction.extract_profile_parallel(
            request_json=request, url="https://example.org", title="Company",
            text="x" * (12000 * 17), external_sources=[], accessed_at="2026-09-13T00:00:00Z",
        )
    assert len(calls) == 21
    assert all(tokens == 8192 for _, tokens in calls)
    assert len(result.company_facts) == 30
    assert result.coverage.complete and result.coverage.extraction_units_processed == 21
    assert control.completed_chunks == 21
    assert not deep_research_enabled()
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        assert db.execute("SELECT COUNT(*) FROM research_run_checkpoints WHERE chunk_key != 'status'").fetchone()[0] == 22


@pytest.mark.asyncio
async def test_output_truncation_subdivides_evidence_without_dropping_facts(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    async def request(phase, model_type, **kwargs):
        prompt = kwargs["prompt"]
        official = prompt.split("OFFICIAL PAGE TEXT CHUNK:\n")[1].split("\nRELEVANT SOURCES CHUNK:")[0]
        if phase == "profile_identity_core":
            if "BEGIN" in official and "TAIL" in official:
                raise SplitSynthesisPhaseError(phase, "OutputTruncated")
            return model_type(company_name="Company", business_summary="Profile",
                company_facts=[CompanyFact(field="other", value=official, source_ids=["S1"])])
        return model_type()
    control = ResearchControl(deep=True)
    with bind_research(control):
        result = await extraction.extract_profile_parallel(request_json=request,
            url="https://example.org", title="Company", text="BEGIN----TAIL", external_sources=[], accessed_at="2026-09-13T00:00:00Z")
    assert "".join(f.value for f in result.company_facts) == "BEGIN----TAIL"
    assert result.coverage.complete


@pytest.mark.asyncio
async def test_stop_preserves_completed_chunks_and_marks_incomplete(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    control = ResearchControl(deep=True)
    calls = []
    async def request(phase, model_type, **kwargs):
        calls.append(phase)
        control.stop_requested = True
        return model_type(company_name="Company", business_summary="Partial",
            company_facts=[CompanyFact(field="products", value="Retained")])
    with bind_research(control):
        result = await extraction.extract_profile_parallel(request_json=request,
            url="https://example.org", title="Company", text="x" * 25000, external_sources=[], accessed_at="2026-09-13T00:00:00Z")
    assert len(calls) == 1
    assert result.company_facts[0].value == "Retained"
    assert not result.coverage.complete
    assert control.completed_chunks == 1


def test_usage_is_recorded_per_run_without_a_total_token_ceiling(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    first, second = ResearchControl(deep=True), ResearchControl(deep=True)
    with bind_research(first):
        record_llm_start()
        record_llm_usage({"usage": {"prompt_tokens": 1000000, "completion_tokens": 1000000}})
    with bind_research(second):
        record_llm_start()
    assert first.prompt_tokens == 1000000 and first.completion_tokens == 1000000
    assert second.prompt_tokens == 0 and first.llm_calls == second.llm_calls == 1


def test_anonymous_user_cannot_authorize_uncapped_spend(monkeypatch, tmp_path):
    from app.main import app
    monkeypatch.setenv("AIMETON_AUTH_DB", str(tmp_path / "auth.db"))
    client = TestClient(app)
    response = client.post('/api/analyze/start', json={"url": "https://example.org",
            "deep_research": True, "unlimited_llm_budget": True})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deep_mode_has_no_standard_outer_deadline(monkeypatch):
    from app import routerai_runtime as runtime
    from app.heuristics import heuristic_analysis
    async def synthesize(*args):
        return heuristic_analysis("https://example.org", "Company", "text")
    original_wait_for = asyncio.wait_for
    observed = []
    async def wait_for(awaitable, timeout):
        observed.append(timeout)
        return await original_wait_for(awaitable, timeout)
    monkeypatch.setattr(runtime, "analyze_with_routerai_split_v2", synthesize)
    monkeypatch.setattr(runtime.asyncio, "wait_for", wait_for)
    with bind_research(ResearchControl(deep=True)):
        await runtime.run_bounded_routerai_analysis("https://example.org", "Company", "text")
    assert observed == [None]


def test_deep_official_frontier_keeps_hubs_and_audit_pages_but_skips_catalog_leaves():
    from app import external_verification as verify

    kept = [
        "https://example.org/services/",
        "https://example.org/company/",
        "https://example.org/company/requisites/",
        "https://example.org/company/employees/",
        "https://example.org/company/licenses/",
        "https://example.org/company/price-list/",
    ]
    skipped = [
        "https://example.org/services/ortodontiya/",
        "https://example.org/services/implantatsiya/",
        "https://example.org/blog/2026/some-post/",
        "https://example.org/catalog/category/product-123/",
    ]

    assert all(verify._official_link_is_audit_relevant(url) for url in kept)
    assert not any(verify._official_link_is_audit_relevant(url) for url in skipped)


@pytest.mark.asyncio
async def test_deep_acquisition_has_no_24_document_cap_and_reuses_robots(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.external_sources import IdentityAnchors
    from app.models import IntelligenceSource
    from app import external_verification as verify
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    fetched_urls = []
    async def robots(*args): return NS(allows=lambda url: not url.endswith('/blocked'))
    monkeypatch.setattr('app.evidence_crawler.factory.get_evidence_crawler', lambda: NS(_load_robots=robots))
    class Pipeline:
        async def fetch_hint(self, hint, source, policy):
            url = str(hint.url)
            fetched_urls.append(url)
            index = int(url.rsplit('/', 1)[1])
            text = f"Company document {index} with verified facts"
            links = [NS(url=f"https://example.org/{index + 1}", text="Next")] if index < 27 else []
            links += [NS(url="https://example.org/blocked", text="Blocked")]
            return NS(normalized_text=text, blocks=[NS(text=text, locator='body/p[1]')], links=links,
                document=NS(url=url, title='Company', accessed_at=datetime.now(timezone.utc)),
                normalized_content_digest='sha256:' + 'a' * 64, diagnostics=NS(path=NS(value='static')))
        def promote_quote(self, fetched, *, locator, quote):
            return NS(evidence=NS(quote=quote, locator=locator, digest='sha256:' + 'b' * 64))
    monkeypatch.setattr(verify, 'get_document_pipeline', lambda: Pipeline())
    sources = [IntelligenceSource(id='ROOT', title='Company', url='https://example.org/1',
        source_class='official', query_kind='official', accessed_at='2026-09-13T00:00:00Z')]
    control = ResearchControl(deep=True)
    with bind_research(control):
        evidence = await verify.verify_external_sources(sources, company_name='Company',
            anchors=IdentityAnchors(domain='example.org'), max_documents=None, include_official=True, preserve_blocks=True)
    assert len(fetched_urls) == 27
    assert any(item.document_url == 'https://example.org/27' for item in evidence)
    assert not any(url.endswith('/blocked') for url in fetched_urls)


@pytest.mark.asyncio
async def test_stop_is_owner_scoped_and_starts_no_new_llm_calls(monkeypatch, tmp_path):
    from app import analysis_async_api as api
    from app import auth_api
    from app.research_control import CONTROLS, ResearchStopped
    from starlette.requests import Request
    monkeypatch.setenv('AIMETON_RUNTIME_DB', str(tmp_path / 'runtime.db'))
    monkeypatch.setattr(auth_api, 'get_auth_provider', lambda: None)
    monkeypatch.setattr(auth_api, '_resolve_session', lambda *args: NS(failure=None, user=NS(id=2)))
    monkeypatch.setattr(auth_api, '_require_csrf', lambda *args: None)
    control = ResearchControl(owner_id=1, deep=True)
    monkeypatch.setitem(CONTROLS, 'test-owned', control)
    request = Request({'type': 'http', 'headers': []})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await api.stop_deep_research('test-owned', request)
    assert error.value.status_code == 404 and not control.stop_requested
    monkeypatch.setattr(auth_api, '_resolve_session', lambda *args: NS(failure=None, user=NS(id=1)))
    monkeypatch.setattr(api, '_append_event', lambda *args, **kwargs: None)
    result = await api.stop_deep_research('test-owned', request)
    assert result['state'] == 'stop_requested'
    with bind_research(control), pytest.raises(ResearchStopped):
        record_llm_start()
    assert control.llm_calls == 0

@pytest.mark.asyncio
async def test_subdivision_keeps_successful_child_when_later_child_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    async def request(phase, model_type, **kwargs):
        official = kwargs["prompt"].split("OFFICIAL PAGE TEXT CHUNK:\n")[1].split("\nRELEVANT SOURCES CHUNK:")[0]
        if phase != "profile_identity_core":
            return model_type()
        if "BEGIN" in official and "TAIL" in official:
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        if "TAIL" in official:
            raise RuntimeError("provider_unavailable")
        return model_type(company_name="Company", business_summary="Partial",
                          company_facts=[CompanyFact(field="other", value=official)])
    with bind_research(ResearchControl(deep=True)):
        result = await extraction.extract_profile_parallel(request_json=request,
            url="https://example.org", title="Company", text="BEGIN----TAIL",
            external_sources=[], accessed_at="2026-09-13T00:00:00Z")
    assert any("BEGIN" in fact.value for fact in result.company_facts)
    assert not result.coverage.complete


def test_official_requisites_identifier_blocks_are_forced_into_evidence():
    from app import external_verification as verify

    blocks = [
        NS(text="О клинике Алекс Дент", locator="body/main/p[1]"),
        NS(
            text='ООО "АЛЕКС ДЕНТ" ИНН 2462215501 ОГРН 1112468013030',
            locator="body/main/div[4]",
        ),
        NS(
            text='Правообладатель каталога ИНН 9999999999 ОГРН 1234567890123',
            locator="footer/legal",
        ),
    ]

    assert verify._official_identity_block_indices(
        blocks,
        company_name="Алекс Дент",
        anchors=NS(legal_name='ООО "АЛЕКС ДЕНТ"'),
        document_title="Реквизиты Алекс Дент",
    ) == [1]

    split_blocks = [
        NS(text="О клинике Алекс Дент", locator="body/main/p[1]"),
        NS(text="ОГРН:", locator="body/main/div[4]/span[1]"),
        NS(text="1112468013030", locator="body/main/div[4]/span[2]"),
        NS(text="ИНН:", locator="body/main/div[5]/span[1]"),
        NS(text="2462215501", locator="body/main/div[5]/span[2]"),
        NS(text="ИНН: 9999999999", locator="footer/legal"),
    ]
    assert verify._official_identity_block_indices(
        split_blocks,
        company_name="Алекс Дент",
        anchors=NS(legal_name='ООО "АЛЕКС ДЕНТ"'),
        document_title="Реквизиты Алекс Дент",
    ) == [1, 2, 3, 4]


def test_first_party_registry_candidates_survive_without_target_context():
    from app import external_verification as verify

    blocks = [
        NS(text="Платёжные и юридические реквизиты", locator="body/main/h2"),
        NS(text="ОГРН:", locator="body/main/div[4]/span[1]"),
        NS(text="1112468013030", locator="body/main/div[4]/span[2]"),
        NS(text="ИНН:", locator="body/main/div[5]/span[1]"),
        NS(text="2462215501", locator="body/main/div[5]/span[2]"),
        NS(text="ИНН: 7707083893", locator="footer/legal"),
    ]

    assert verify._official_identifier_candidate_block_indices(blocks) == [1, 2, 3, 4]


def test_first_party_registry_candidates_require_valid_checksum():
    from app import external_verification as verify

    blocks = [
        NS(text="ИНН:", locator="body/main/div[1]"),
        NS(text="1234567890", locator="body/main/div[2]"),
        NS(text="ОГРН:", locator="body/main/div[3]"),
        NS(text="1234567890123", locator="body/main/div[4]"),
    ]

    assert verify._official_identifier_candidate_block_indices(blocks) == []


@pytest.mark.asyncio
async def test_verify_retains_registry_candidates_even_when_triage_keeps_none(monkeypatch):
    from datetime import datetime, timezone
    from app import external_verification as verify
    from app.external_sources import IdentityAnchors
    from app.models import IntelligenceSource

    blocks = [
        NS(text="Юридическая информация", locator="body/main/h2"),
        NS(text="ОГРН:", locator="body/main/div[4]/span[1]"),
        NS(text="1112468013030", locator="body/main/div[4]/span[2]"),
        NS(text="ИНН:", locator="body/main/div[5]/span[1]"),
        NS(text="2462215501", locator="body/main/div[5]/span[2]"),
    ]

    class Pipeline:
        async def fetch_hint(self, hint, source, policy):
            return NS(
                normalized_text=" ".join(block.text for block in blocks),
                blocks=blocks,
                links=[],
                document=NS(
                    url="https://example.org/legal/",
                    title="Юридическая информация",
                    accessed_at=datetime.now(timezone.utc),
                ),
                normalized_content_digest="sha256:" + "a" * 64,
                diagnostics=NS(path=NS(value="static")),
            )

        def promote_quote(self, fetched, *, locator, quote):
            return NS(evidence=NS(
                quote=quote,
                locator=locator,
                digest="sha256:" + "b" * 64,
            ))

    async def screen(*args, **kwargs):
        return NS(decision="include", reason="official first-party", model_dump=lambda: {
            "decision": "include",
            "reason": "official first-party",
        })

    async def triage(*args, **kwargs):
        return NS(
            decisions=[],
            kept=[],
            model_used=False,
            model_unavailable=False,
        )

    monkeypatch.setattr(verify, "get_document_pipeline", lambda: Pipeline())
    monkeypatch.setattr(verify, "screen_document", screen)
    monkeypatch.setattr(verify, "triage_document_blocks", triage)

    source = IntelligenceSource(
        id="DOC",
        title="Юридическая информация",
        url="https://example.org/legal/",
        accessed_at="2026-09-20T00:00:00Z",
        source_class="official",
        query_kind="official",
    )

    verified = await verify.verify_external_sources(
        [source],
        company_name="Example Dental",
        anchors=IdentityAnchors(domain="example.org"),
        include_official=True,
        preserve_blocks=True,
    )

    child = [item for item in verified if item.id.startswith("DOC-b")]
    assert [item.id for item in child] == [
        "DOC-b1-0",
        "DOC-b2-0",
        "DOC-b3-0",
        "DOC-b4-0",
    ]
    assert all("Evidence triage: unknown/registry" in item.verification_note for item in child)
