from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

from app.trace_context import bind_trace_identity, current_trace_identity

from app.search_gateway import SearchDiagnostics

from app.adaptive_external_sources import collect_external_sources_adaptive
from app.dadata_report_bridge import enrich_identity_with_dadata
from app.evidence_block_ledger import build_evidence_ledger, compact_source_list_in_place
from app.external_sources import (
    extract_identity_anchors,
    source_type,
    query_plan,
)
from app.external_verification import verify_external_sources
from app.heuristics import heuristic_analysis
from app.identity_anchor_guard import guard_identity_anchors
from app.routerai_runtime import run_bounded_routerai_analysis as analyze_with_routerai
from app.models import EvidenceSource, IntelligenceSource, SiteAnalysis
from app.research_control import deep_research_enabled, current_research
from app.search_result_triage import SearchTriageSummary, triage_search_candidates
from datetime import datetime, timezone


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


async def _run_verified_enriched_site_analysis(
    url: str,
    title: str,
    text: str,
    *,
    research_queries: list[tuple[str, str]] | None = None,
) -> SiteAnalysis:
    """Analyze crawled first-party evidence plus verified external primary documents.

    Search results remain discovery hints until the fast triage selects fetch-worthy
    candidates and the document pipeline confirms primary identity. DaData may
    corroborate/normalize INN/OGRN as a non-authoritative registry mirror.
    """
    deep = deep_research_enabled()
    if current_research() and current_research().stop_requested:
        result = heuristic_analysis(url, title, text)
        result.research_status = {**current_research().snapshot(), "stage": "stopped_partial"}
        return result
    company_hint = title.split("—")[0].split("|")[0].strip() or _host(url)
    anchors = guard_identity_anchors(extract_identity_anchors(text, url), text)
    anchors, dadata_result, dadata_facts, dadata_notes = await enrich_identity_with_dadata(
        anchors
    )

    planned_queries = research_queries if research_queries is not None else query_plan(company_hint, anchors=anchors)
    search_triage = SearchTriageSummary(total=0, selected=0, rejected=0)
    try:
        external_sources, notes, diagnostics = await collect_external_sources_adaptive(
            company_hint,
            url,
            region=anchors.primary_region,
            max_sources=None if deep else 100,
            anchors=anchors,
            query_overrides=research_queries,
        )
        if deep and not any(item.url == url for item in external_sources):
            external_sources.insert(0, IntelligenceSource(
                id="OFFICIAL", title=title or url, url=url,
                accessed_at=datetime.now(timezone.utc).isoformat(), source_class="official",
                query_kind="official", classification_state="classified",
            ))
        external_sources, search_triage = await triage_search_candidates(
            external_sources,
            company_name=company_hint,
            anchors=anchors,
            official_url=url,
        )
        notes.append(
            "Fast search triage: "
            f"total={search_triage.total}, selected={search_triage.selected}, "
            f"rejected={search_triage.rejected}, model_used={search_triage.model_used}."
        )
    except Exception as exc:
        external_sources = []
        notes = [f"Внешний поиск/triage недоступен ({type(exc).__name__}); профиль неполный."]
        diagnostics = SearchDiagnostics(state="unavailable")
    if deep and not any(item.url == url for item in external_sources):
        # First-party acquisition must not disappear because a cheap triage model or
        # search provider was unavailable.
        external_sources.insert(0, IntelligenceSource(
            id="OFFICIAL", title=title or url, url=url,
            accessed_at=datetime.now(timezone.utc).isoformat(), source_class="official",
            query_kind="official", classification_state="classified",
        ))
    verified = await verify_external_sources(
        external_sources,
        company_name=company_hint,
        anchors=anchors,
        max_documents=None if deep else 24,
        preserve_blocks=deep,
        include_official=True,
    )

    # Requisites may only occur on a discovered first-party subpage. Use
    # that fetched content to form one bounded registry follow-up wave.
    if not anchors.inn and not anchors.ogrn and not (current_research() and current_research().stop_requested):
        official_text = "\n".join(
            item.evidence_quote or "" for item in verified if item.source_class == "official"
        )
        discovered = guard_identity_anchors(extract_identity_anchors(official_text, url), official_text)
        if discovered.inn or discovered.ogrn:
            anchors, dadata_result, dadata_facts, follow_notes = await enrich_identity_with_dadata(discovered)
            dadata_notes.extend(follow_notes)
            identifier = anchors.inn or anchors.ogrn
            follow_plan = [
                ("registry", f'"{identifier}" site:egrul.nalog.ru'),
                ("finance", f'"{identifier}" site:bo.nalog.ru'),
                ("registry", f'"{identifier}" реквизиты филиалы'),
            ]
            planned_queries = planned_queries + follow_plan
            try:
                more, more_notes, more_diagnostics = await collect_external_sources_adaptive(
                    company_hint, url, max_sources=None if deep else 12, anchors=anchors, query_overrides=follow_plan,
                )
                more, follow_triage = await triage_search_candidates(
                    more,
                    company_name=company_hint,
                    anchors=anchors,
                    official_url=url,
                )
                for item in more:
                    item.id = "R-" + item.id
                more_verified = await verify_external_sources(
                    more, company_name=company_hint, anchors=anchors, max_documents=None if deep else 8,
                    preserve_blocks=deep, timeout_seconds=25,
                )
                external_sources.extend(more)
                verified.extend(more_verified)
                notes.extend(more_notes)
                notes.append(
                    "Fast registry follow-up triage: "
                    f"total={follow_triage.total}, selected={follow_triage.selected}, rejected={follow_triage.rejected}."
                )
                diagnostics = SearchDiagnostics.aggregate([diagnostics, more_diagnostics])
                search_triage = search_triage.model_copy(update={
                    "total": search_triage.total + follow_triage.total,
                    "selected": search_triage.selected + follow_triage.selected,
                    "rejected": search_triage.rejected + follow_triage.rejected,
                    "deterministic_selected": search_triage.deterministic_selected + follow_triage.deterministic_selected,
                    "model_selected": search_triage.model_selected + follow_triage.model_selected,
                    "model_used": search_triage.model_used or follow_triage.model_used,
                    "model_unavailable": search_triage.model_unavailable or follow_triage.model_unavailable,
                })
            except Exception as exc:
                notes.append(f"Уточняющая проверка реквизитов не завершена ({type(exc).__name__}).")

    # Transitional verifier output still contains block-shaped child records. Collapse
    # them into an explicit nested ledger before any expensive extraction or UI assembly.
    ledger = build_evidence_ledger(verified)
    verified = ledger.documents
    compact_source_list_in_place(external_sources)
    extraction_records = ledger.extraction_records()

    try:
        analysis = await analyze_with_routerai(
            url,
            title,
            text,
            extraction_records,
        )
    except Exception as exc:
        analysis = heuristic_analysis(url, title, text)
        analysis.readiness.provider_states["routerai"] = (
            "not_configured"
            if isinstance(exc, RuntimeError)
            and "ROUTERAI_API_KEY" in str(exc)
            else "failed"
        )
        analysis.risks_and_assumptions.append(
            f"Использован резервный локальный анализ: {type(exc).__name__}."
        )

    existing_fact_keys = {(fact.field, fact.value, fact.note) for fact in analysis.company_facts}
    for fact in dadata_facts:
        key = (fact.field, fact.value, fact.note)
        if key not in existing_fact_keys:
            analysis.company_facts.append(fact)
            existing_fact_keys.add(key)

    known_ids = {source.id for source in analysis.sources}
    for source in verified:
        if not source.evidence_quote or source.id in known_ids:
            continue
        analysis.sources.append(
            EvidenceSource(
                id=source.id,
                title=source.document_title or source.title,
                url=source.document_url or source.url,
                accessed_at=source.document_accessed_at or source.accessed_at,
                evidence_quote=source.evidence_quote,
                source_type=source_type(source.source_class),
                evidence_level=source.evidence_level,
                document_url=source.document_url,
                document_title=source.document_title,
                document_accessed_at=source.document_accessed_at,
                document_digest=source.document_digest,
                evidence_locator=source.evidence_locator,
                evidence_digest=source.evidence_digest,
                fetch_path=source.fetch_path,
            )
        )
        known_ids.add(source.id)

    discovery_count = sum(1 for source in external_sources if source.lifecycle_state == "discovery_hint")
    candidate_count = sum(1 for source in external_sources if source.lifecycle_state == "source_candidate")
    evidence_count = sum(1 for source in external_sources if source.lifecycle_state == "evidence")
    anchor_parts = [
        f"domain={anchors.domain}" if anchors.domain else None,
        f"region={anchors.primary_region}" if anchors.primary_region else None,
        "inn=present" if anchors.inn else None,
        "ogrn=present" if anchors.ogrn else None,
        f"phones={len(anchors.phones)}" if anchors.phones else None,
    ]
    analysis.risks_and_assumptions.extend(dadata_notes)
    analysis.risks_and_assumptions.append(
        "Identity anchors для внешнего поиска: "
        + (", ".join(part for part in anchor_parts if part) or "не извлечены")
        + "."
    )
    analysis.risks_and_assumptions.append(
        "Контур внешних источников после fast triage: "
        f"discovery_hint={discovery_count}, source_candidate={candidate_count}, "
        f"verified_documents={evidence_count}, retained_blocks={ledger.block_count}."
    )
    analysis.risks_and_assumptions.append(
        "Evidence ledger отделяет первичный документ от вложенных evidence blocks; "
        "block records раскрываются только на внутренней границе extraction и сохраняют parent source id."
    )
    analysis.risks_and_assumptions.append(
        "Поисковые сниппеты не считаются evidence; fast model управляет только приоритетом fetch. "
        "Внешний источник включается в доказательную базу только после загрузки primary document, "
        "подтверждения primary identity и block-level evidence triage."
    )
    analysis.risks_and_assumptions.append(
        f"Search gateway state={diagnostics.state}; attempts={len(diagnostics.attempts)}."
    )
    analysis.risks_and_assumptions.extend(notes)
    for source in external_sources:
        if source.preflight_decision in {"exclude", "uncertain"}:
            action = "Исключён из полного анализа" if source.preflight_decision == "exclude" else "Оставлен в пуле при неопределённой классификации"
            analysis.risks_and_assumptions.append(f"{action}: {source.url}. {source.preflight_reason}")

    if dadata_result is not None:
        analysis.readiness.provider_states["dadata"] = dadata_result.state.value
        if dadata_result.state.value == "registry_mirror_verified":
            identity_vertical = next(
                (vertical for vertical in analysis.readiness.required_verticals if vertical.code == "identity"),
                None,
            )
            if identity_vertical and identity_vertical.state in {"not_searched", "degraded"}:
                identity_vertical.state = "partially_verified"
    else:
        analysis.readiness.provider_states["dadata"] = (
            "not_attempted_no_identifier"
            if any("not_attempted" in note for note in dadata_notes)
            else "unavailable"
        )

    analysis.readiness.provider_states["search"] = diagnostics.state
    analysis.research_queries = [query for _, query in planned_queries]
    analysis.research_status = {
        "extraction_input_coverage_complete": False,
        **analysis.research_status,
        "stage": "intermediate_report",
        "search_state": diagnostics.state,
        "search_results_triaged": search_triage.total,
        "search_results_selected": search_triage.selected,
        "search_results_rejected": search_triage.rejected,
        "search_triage_model_used": search_triage.model_used,
        "search_triage_model_unavailable": search_triage.model_unavailable,
        "discovery_hints": discovery_count,
        "source_candidates": candidate_count,
        "evidence_records": evidence_count,
        "evidence_records_raw": ledger.raw_evidence_records,
        "evidence_child_records_compacted": ledger.child_records_compacted,
        "evidence_blocks_retained": ledger.block_count,
        "duplicate_verified_documents_removed": ledger.duplicate_documents_removed,
        "duplicate_evidence_blocks_removed": ledger.duplicate_blocks_removed,
        "extraction_evidence_records": len(extraction_records),
        "verified_documents": len(verified),
        "unverified_documents": discovery_count + candidate_count,
        "preflight_excluded_documents": sum(s.preflight_decision == "exclude" for s in external_sources),
        "official_input_chars": len(text),
        "registry_authority_verified": False,
    }
    if evidence_count:
        analysis.readiness.evidence_quality = max(
            analysis.readiness.evidence_quality,
            min(1.0, 0.25 + 0.08 * len(verified)),
        )
    if current_research():
        analysis.research_status.update(current_research().snapshot())
        if current_research().stop_requested:
            analysis.research_status["stage"] = "stopped_partial"
    return analysis


async def run_verified_enriched_site_analysis(url: str, title: str, text: str, **kwargs) -> SiteAnalysis:
    if current_trace_identity() is not None:
        return await _run_verified_enriched_site_analysis(url, title, text, **kwargs)
    identity = uuid4().hex
    with bind_trace_identity(f"audit-{identity}", f"analysis-{identity}"):
        return await _run_verified_enriched_site_analysis(url, title, text, **kwargs)
