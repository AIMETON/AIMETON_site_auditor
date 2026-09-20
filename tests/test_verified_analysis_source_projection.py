import pytest

from app import verified_analysis as audit
from app.heuristics import heuristic_analysis
from app.models import CompanyFact, EvidenceSource, IntelligenceSource


def _verified(identifier: str, *, quote: str, digest: str):
    return IntelligenceSource(
        id=identifier,
        title="Registry",
        url="https://registry.example/company",
        accessed_at="2026-09-17T00:00:00Z",
        source_class="registry",
        query_kind="registry",
        lifecycle_state="evidence",
        evidence_level="corroborated_signal",
        document_url="https://registry.example/company",
        document_title="Registry",
        document_accessed_at="2026-09-17T00:00:00Z",
        document_digest="sha256:" + "a" * 64,
        evidence_quote=quote,
        evidence_locator="body/main",
        evidence_digest=digest,
        fetch_path="static",
        verification_note="Evidence triage: target/registry; retained target block.",
    )


@pytest.mark.asyncio
async def test_audit_public_ledger_remaps_routerai_child_sources(monkeypatch):
    parent = _verified("H1", quote="anchor", digest="sha256:" + "1" * 64)
    child = _verified("H1-b2-0", quote="ИНН 2462215501", digest="sha256:" + "2" * 64)
    discovered = [parent]

    async def collect(*args, **kwargs):
        return discovered, [], audit.SearchDiagnostics(state="success")

    async def triage(items, **kwargs):
        return items, audit.SearchTriageSummary(total=len(items), selected=len(items), rejected=0)

    async def verify(*args, **kwargs):
        return [parent, child]

    async def synthesize(url, title, text, sources):
        result = heuristic_analysis(url, title, text)
        result.sources = [
            EvidenceSource(
                id="H1-b2-0",
                title="Registry chunk",
                url="https://registry.example/company",
                accessed_at="2026-09-17T00:00:00Z",
                evidence_quote="ИНН 2462215501",
                source_type="registry",
                evidence_level="corroborated_signal",
            )
        ]
        result.company_facts = [
            CompanyFact(field="inn", value="2462215501", source_ids=["H1-b2-0"])
        ]
        return result

    async def no_dadata(anchors):
        return anchors, None, [], ["DaData not_attempted"]

    monkeypatch.setattr(audit, "collect_external_sources_adaptive", collect)
    monkeypatch.setattr(audit, "triage_search_candidates", triage)
    monkeypatch.setattr(audit, "verify_external_sources", verify)
    monkeypatch.setattr(audit, "analyze_with_routerai", synthesize)
    monkeypatch.setattr(audit, "enrich_identity_with_dadata", no_dadata)

    result = await audit._run_verified_enriched_site_analysis(
        "https://example.org", "Example", "Example company page"
    )

    assert [source.id for source in result.sources] == ["H1"]
    assert len(result.sources[0].evidence_blocks) == 1
    assert result.sources[0].evidence_blocks[0].evidence_quote == "ИНН 2462215501"
    assert result.company_facts[0].source_ids == ["H1"]
    assert result.research_status["verified_documents"] == 1
    assert result.research_status["evidence_blocks_retained"] == 1


@pytest.mark.asyncio
async def test_audit_deduplicates_same_fetched_document_before_routerai(monkeypatch):
    parent = _verified("H1", quote="anchor", digest="sha256:" + "3" * 64)
    child = _verified("H1-b1-0", quote="requisites", digest="sha256:" + "4" * 64)
    duplicate = _verified("H9", quote="duplicate anchor", digest="sha256:" + "5" * 64)
    duplicate.url = "https://search.example/redirect"
    duplicate.document_url = "https://registry.example/company?utm_source=search"
    duplicate.document_digest = "sha256:" + "b" * 64
    duplicate_child = _verified("H9-b5-0", quote="director", digest="sha256:" + "6" * 64)
    duplicate_child.url = duplicate.url
    duplicate_child.document_url = duplicate.document_url
    duplicate_child.document_digest = duplicate.document_digest
    duplicate_child.query_kind = "ownership"
    discovered = [parent]
    routed_ids = []

    async def collect(*args, **kwargs):
        return discovered, [], audit.SearchDiagnostics(state="success")

    async def triage(items, **kwargs):
        return items, audit.SearchTriageSummary(total=len(items), selected=len(items), rejected=0)

    async def verify(*args, **kwargs):
        return [parent, child, duplicate, duplicate_child]

    async def synthesize(url, title, text, sources):
        routed_ids.extend(item["id"] for item in sources)
        return heuristic_analysis(url, title, text)

    async def no_dadata(anchors):
        return anchors, None, [], ["DaData not_attempted"]

    monkeypatch.setattr(audit, "collect_external_sources_adaptive", collect)
    monkeypatch.setattr(audit, "triage_search_candidates", triage)
    monkeypatch.setattr(audit, "verify_external_sources", verify)
    monkeypatch.setattr(audit, "analyze_with_routerai", synthesize)
    monkeypatch.setattr(audit, "enrich_identity_with_dadata", no_dadata)

    result = await audit._run_verified_enriched_site_analysis(
        "https://example.org", "Example", "Example company page"
    )

    assert routed_ids == ["H1", "H1-b1-0", "H1-b5-0"]
    assert result.research_status["postfetch_duplicate_documents"] == 1
    assert result.research_status["verified_documents"] == 1
    assert len(result.sources) == 1
    assert {block.evidence_quote for block in result.sources[0].evidence_blocks} == {"requisites", "director"}


@pytest.mark.asyncio
async def test_official_requisites_evidence_triggers_identifier_followup_and_dadata(monkeypatch):
    parent = IntelligenceSource(
        id="OFFICIAL",
        title="Реквизиты Алекс Дент",
        url="https://aleksdent24.ru/company/requisites/",
        accessed_at="2026-09-19T00:00:00Z",
        source_class="official",
        query_kind="official",
        classification_state="classified",
        lifecycle_state="evidence",
        evidence_level="confirmed_fact",
        document_url="https://aleksdent24.ru/company/requisites/",
        document_title="Реквизиты Алекс Дент",
        document_accessed_at="2026-09-19T00:00:00Z",
        document_digest="sha256:" + "a" * 64,
        evidence_quote="Реквизиты клиники Алекс Дент",
        evidence_locator="body/main",
        evidence_digest="sha256:" + "b" * 64,
        fetch_path="static",
    )
    child_specs = [
        ("OFFICIAL-b4-0", 'ООО "АЛЕКС ДЕНТ"', "c"),
        ("OFFICIAL-b5-0", "ОГРН:", "d"),
        ("OFFICIAL-b6-0", "1112468013030", "e"),
        ("OFFICIAL-b7-0", "ИНН:", "f"),
        ("OFFICIAL-b8-0", "2462215501", "1"),
    ]
    children = []
    for child_id, quote, digest_char in child_specs:
        child = parent.model_copy(deep=True)
        child.id = child_id
        child.query_kind = "registry"
        child.evidence_quote = quote
        child.evidence_digest = "sha256:" + digest_char * 64
        children.append(child)

    collect_calls = 0

    async def collect(*args, **kwargs):
        nonlocal collect_calls
        collect_calls += 1
        if kwargs.get("query_overrides"):
            return [], [], audit.SearchDiagnostics(state="success")
        return [parent], [], audit.SearchDiagnostics(state="success")

    async def triage(items, **kwargs):
        return items, audit.SearchTriageSummary(
            total=len(items), selected=len(items), rejected=0
        )

    verify_calls = 0

    async def verify(*args, **kwargs):
        nonlocal verify_calls
        verify_calls += 1
        return [parent, *children] if verify_calls == 1 else []

    dadata_anchors = []
    batch_calls = []

    async def dadata(anchors):
        dadata_anchors.append(anchors)
        return anchors, None, [], []

    async def dadata_batch(anchors, candidates, *, company_hint):
        batch_calls.append((anchors, candidates, company_hint))
        return anchors, None, [], ["batch checked"], len(candidates)

    async def synthesize(url, title, text, sources):
        return heuristic_analysis(url, title, text)

    monkeypatch.setattr(audit, "collect_external_sources_adaptive", collect)
    monkeypatch.setattr(audit, "triage_search_candidates", triage)
    monkeypatch.setattr(audit, "verify_external_sources", verify)
    monkeypatch.setattr(audit, "enrich_identity_with_dadata", dadata)
    monkeypatch.setattr(audit, "enrich_identifier_candidates_with_dadata", dadata_batch)
    monkeypatch.setattr(audit, "analyze_with_routerai", synthesize)

    result = await audit._run_verified_enriched_site_analysis(
        "https://aleksdent24.ru/",
        "Стоматология Красноярск цены доступные | Стоматология Алекс Дент",
        "Стоматология Алекс Дент в Красноярске",
    )

    assert len(dadata_anchors) == 1
    assert dadata_anchors[0].inn is None
    assert len(batch_calls) == 1
    _, candidates, company_hint = batch_calls[0]
    assert company_hint == "Стоматология Красноярск цены доступные | Стоматология Алекс Дент"
    assert ("inn", "2462215501", True) in candidates
    assert ("ogrn", "1112468013030", True) in candidates
    facts = {(fact.field, fact.value): fact for fact in result.company_facts}
    assert facts[("inn", "2462215501")].source_ids == ["OFFICIAL"]
    assert facts[("ogrn", "1112468013030")].source_ids == ["OFFICIAL"]
    assert result.research_status["deterministic_first_party_identifier_count"] == 2
    assert result.research_status["dadata_identifier_candidates_checked"] == 2
    assert collect_calls == 2
