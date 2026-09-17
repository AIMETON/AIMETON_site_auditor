from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx

from app.evidence_block_ledger import build_evidence_ledger, compact_source_list_in_place
from app.external_sources import (
    collect_external_sources,
    source_type,
    verified_evidence_level,
)
from app.document_pipeline import get_document_pipeline
from app.document_pipeline.models import FetchPolicy
from app.heuristics import heuristic_analysis
from app.routerai_runtime import run_bounded_routerai_analysis as analyze_with_routerai
from app.external_verification import verify_external_sources
from app.external_sources import extract_identity_anchors
from app.identity_anchor_guard import guard_identity_anchors
from app.models import (
    CompanyIntelligenceRequest,
    CompanyIntelligenceResult,
    EvidenceSource,
    IntelligenceSource,
    SiteAnalysis,
)
from app.sef.models import DiscoveryHint, Source, SourceKind
from app.scraper import FetchError


def _runtime_identifier(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


async def _analyze_site_with_sources(
    url: str,
    external_sources: list[IntelligenceSource],
    search_notes: list[str],
) -> SiteAnalysis:
    """Fetch the official document once and promote only that fetched document to evidence."""
    official_candidate = next(
        (
            source
            for source in external_sources
            if source.url == url
            or source.source_class == "official"
        ),
        None,
    )
    source_identity = official_candidate.id if official_candidate else _runtime_identifier("src", url)
    mission_id = _runtime_identifier("mission", url)
    correlation_id = _runtime_identifier("corr", url)
    source = Source(
        id=source_identity,
        mission_id=mission_id,
        correlation_id=correlation_id,
        kind=SourceKind.FIRST_PARTY,
        publisher=(
            official_candidate.title
            if official_candidate is not None
            else "Официальный сайт компании"
        ),
        homepage_url=url,
    )
    hint = DiscoveryHint(
        id=_runtime_identifier("hint", url),
        mission_id=mission_id,
        provider_call_id=_runtime_identifier("provider_call", url),
        correlation_id=correlation_id,
        url=url,
        title=official_candidate.title if official_candidate is not None else "Официальный сайт",
        snippet=(
            official_candidate.snippet
            if official_candidate is not None and official_candidate.snippet.strip()
            else "Кандидат на первичный документ; поисковый текст не используется как evidence."
        ),
        discovered_at=datetime.now(timezone.utc),
    )
    fetched = await get_document_pipeline().fetch_hint(
        hint,
        source,
        FetchPolicy(),
    )
    quote_block = next(
        (
            block
            for block in fetched.blocks
            if len(block.text.strip()) >= 20 and block.locator != "head/title"
        ),
        fetched.blocks[0],
    )
    promoted = get_document_pipeline().promote_quote(
        fetched,
        locator=quote_block.locator,
        quote=quote_block.text[:500],
    )
    page = {
        "final_url": str(fetched.document.url),
        "title": fetched.document.title,
        "text": fetched.normalized_text,
    }
    document_accessed_at = fetched.document.accessed_at.isoformat()
    quote = promoted.evidence.quote

    if official_candidate is not None:
        official_candidate.lifecycle_state = "evidence"
        official_candidate.document_url = page["final_url"]
        official_candidate.document_title = page["title"]
        official_candidate.document_accessed_at = document_accessed_at
        official_candidate.evidence_quote = quote
        official_candidate.document_digest = fetched.normalized_content_digest
        official_candidate.evidence_locator = promoted.evidence.locator
        official_candidate.evidence_digest = promoted.evidence.digest
        official_candidate.fetch_path = fetched.diagnostics.path.value
        official_candidate.evidence_level = verified_evidence_level("official")
        official_candidate.verification_note = (
            "Первичный документ загружен; цитата проверена по стабильному locator и digest."
        )

    await verify_external_sources(
        external_sources, company_name=page["title"],
        anchors=guard_identity_anchors(extract_identity_anchors(page["text"], url), page["text"]),
        preserve_blocks=True, include_official=True,
    )
    raw_evidence = [source for source in external_sources if source.lifecycle_state == "evidence"]
    ledger = build_evidence_ledger(raw_evidence)
    compact_source_list_in_place(external_sources)
    try:
        analysis = await analyze_with_routerai(
            page["final_url"],
            page["title"],
            page["text"],
            ledger.extraction_records(),
        )
    except Exception as exc:
        analysis = heuristic_analysis(page["final_url"], page["title"], page["text"])
        analysis.readiness.provider_states["routerai"] = (
            "not_configured"
            if isinstance(exc, RuntimeError)
            and "ROUTERAI_API_KEY" in str(exc)
            else "failed"
        )
        analysis.risks_and_assumptions.append(
            f"Использован резервный локальный анализ: {type(exc).__name__}."
        )

    if quote:
        evidence_id = official_candidate.id if official_candidate else "DOC1"
        known = {source.id for source in analysis.sources}
        if evidence_id not in known:
            analysis.sources.append(
                EvidenceSource(
                    id=evidence_id,
                    title=page["title"],
                    url=page["final_url"],
                    accessed_at=document_accessed_at,
                    evidence_quote=quote,
                    source_type="official_page",
                    evidence_level="confirmed_fact",
                    document_url=page["final_url"],
                    document_title=page["title"],
                    document_accessed_at=document_accessed_at,
                    document_digest=fetched.normalized_content_digest,
                    evidence_locator=promoted.evidence.locator,
                    evidence_digest=promoted.evidence.digest,
                    fetch_path=fetched.diagnostics.path.value,
                )
            )

    hint_count = sum(1 for source in external_sources if source.lifecycle_state == "discovery_hint")
    candidate_count = sum(1 for source in external_sources if source.lifecycle_state == "source_candidate")
    evidence_count = sum(1 for source in external_sources if source.lifecycle_state == "evidence")
    analysis.risks_and_assumptions.append(
        f"Контур источников: discovery_hint={hint_count}, source_candidate={candidate_count}, verified_documents={evidence_count}, retained_blocks={ledger.block_count}."
    )
    analysis.risks_and_assumptions.append(
        "Evidence blocks хранятся во внутреннем document ledger и раскрываются только на границе extraction; "
        "публичный source list содержит один элемент на первичный документ."
    )
    analysis.risks_and_assumptions.append(
        "Поисковые сниппеты не являются доказательствами; evidence создаётся только после загрузки документа с URL, датой и цитатой."
    )
    ambiguous = sum(1 for source in external_sources if source.classification_state == "ambiguous")
    unknown = sum(1 for source in external_sources if source.classification_state == "unknown")
    if ambiguous or unknown:
        analysis.risks_and_assumptions.append(
            f"Классификация источников требует проверки: ambiguous={ambiguous}, unknown={unknown}."
        )
    analysis.risks_and_assumptions.extend(search_notes)
    analysis.risks_and_assumptions.append(
        "Матрица 4×4 — техническая проекция бизнес-машины по КМ: I коммуникации, II люди, III технологии, IV менеджмент; измерения — внешний контур, внутренний контур, ресурсы/масштаб, результат/риски. Пустая ячейка означает отсутствие подтвержденных данных, а не отсутствие функции."
    )
    return analysis


async def run_company_intelligence(req: CompanyIntelligenceRequest) -> CompanyIntelligenceResult:
    notes: list[str] = []
    official_url = str(req.url) if req.url else None
    sources, search_notes, search_diagnostics = await collect_external_sources(
        req.company_name,
        official_url,
        req.region,
        req.max_sources,
    )
    notes.extend(search_notes)

    if not official_url:
        official = next(
            (
                source
                for source in sources
                if source.source_class == "official"
                or (
                    source.result_kind == "official"
                    and source.classification_state != "ambiguous"
                )
            ),
            None,
        )
        if official:
            official.lifecycle_state = "source_candidate"
            official.verification_note = "Кандидат на официальный источник; ожидает загрузки документа."
            official_url = official.url

    site_analysis = None
    if official_url:
        try:
            site_analysis = await _analyze_site_with_sources(
                official_url,
                sources,
                search_notes,
            )
            official_url = site_analysis.url
        except (FetchError, httpx.HTTPError, ValueError) as exc:
            notes.append(f"Официальный сайт не удалось глубоко проанализировать: {exc}")

    counts = {
        state: sum(1 for source in sources if source.lifecycle_state == state)
        for state in ("discovery_hint", "source_candidate", "evidence")
    }
    scent_summary = [f"{state}: {count}" for state, count in counts.items()]
    if site_analysis:
        scent_summary.extend(signal.signal for signal in site_analysis.economic_signals[:5])

    verified_count = counts["evidence"]
    score = (
        site_analysis.commercial_opportunity.score
        if site_analysis
        else min(45, 15 + verified_count * 10)
    )
    solution = (
        site_analysis.commercial_opportunity.recommended_solution
        if site_analysis
        else "Требуется загрузка и проверка первичных документов; поисковые сниппеты являются только discovery hints."
    )
    return CompanyIntelligenceResult(
        company_name=site_analysis.company_name if site_analysis else req.company_name,
        region=req.region,
        official_url=official_url,
        site_analysis=site_analysis,
        sources=sources,
        scent_summary=scent_summary,
        confidence_notes=notes,
        commercial_score=score,
        recommended_solution=solution,
        status="complete" if site_analysis and verified_count > 0 else "partial",
        search=search_diagnostics,
    )
