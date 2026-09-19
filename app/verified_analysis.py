from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

from app.trace_context import bind_trace_identity, current_trace_identity
from app.evidence_quality import assess_evidence_quality
from app.identity_readiness import assess_identity_readiness, identity_release_blocker

from app.search_gateway import SearchDiagnostics

from app.adaptive_external_sources import collect_external_sources_adaptive
from app.dadata_report_bridge import enrich_identity_with_dadata
from app.evidence_source_projection import (
    collapse_source_ids,
    collapse_verified_evidence,
    deduplicate_verified_documents,
    merge_document_sources,
)
from app.external_sources import (
    extract_identity_anchors,
    project_llm_sources,
    query_plan,
)
from app.external_verification import verify_external_sources
from app.heuristics import heuristic_analysis
from app.identity_anchor_guard import guard_identity_anchors
from app.routerai_runtime import run_bounded_routerai_analysis as analyze_with_routerai
from app.models import IntelligenceSource, SiteAnalysis, SourceKind
from app.research_control import deep_research_enabled, current_research
from app.search_result_triage import SearchTriageSummary, triage_search_candidates
from app.research_coverage_controller import (
    MAX_PROGRESSIVE_WAVES,
    assess_coverage,
    gap_wave,
    initial_wave,
    optional_wave,
)
from app.search_gateway.gateway import canonical_url
from datetime import datetime, timezone


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _remap_analysis_source_ids(analysis: SiteAnalysis) -> None:
    for fact in analysis.company_facts:
        fact.source_ids = collapse_source_ids(fact.source_ids)
    for signal in analysis.economic_signals:
        signal.source_ids = collapse_source_ids(signal.source_ids)
    for cell in analysis.business_machine_4x4:
        cell.source_ids = collapse_source_ids(cell.source_ids)


def _merge_search_triage(
    left: SearchTriageSummary,
    right: SearchTriageSummary,
) -> SearchTriageSummary:
    return left.model_copy(update={
        "total": left.total + right.total,
        "selected": left.selected + right.selected,
        "rejected": left.rejected + right.rejected,
        "deterministic_selected": left.deterministic_selected + right.deterministic_selected,
        "model_selected": left.model_selected + right.model_selected,
        "model_used": left.model_used or right.model_used,
        "model_unavailable": left.model_unavailable or right.model_unavailable,
    })


def _append_unique_wave_sources(
    existing: list[IntelligenceSource],
    incoming: list[IntelligenceSource],
    *,
    prefix: str,
) -> list[IntelligenceSource]:
    seen = {canonical_url(str(item.url)) for item in existing}
    selected: list[IntelligenceSource] = []
    for item in incoming:
        key = canonical_url(str(item.url))
        if not key or key in seen:
            continue
        seen.add(key)
        item.id = f"{prefix}-{item.id}"
        existing.append(item)
        selected.append(item)
    return selected


async def _search_verify_wave(
    *,
    plan: list[tuple[SourceKind, str]],
    prefix: str,
    company_name: str,
    official_url: str,
    anchors,
    deep: bool,
    existing_sources: list[IntelligenceSource],
) -> tuple[list[IntelligenceSource], list[str], SearchDiagnostics, SearchTriageSummary, int]:
    if not plan:
        return [], [], SearchDiagnostics(state="unavailable"), SearchTriageSummary(total=0, selected=0, rejected=0), 0

    sources, notes, diagnostics = await collect_external_sources_adaptive(
        company_name,
        official_url,
        region=anchors.primary_region,
        max_sources=None if deep else 12,
        anchors=anchors,
        query_overrides=plan,
    )
    sources, triage = await triage_search_candidates(
        sources,
        company_name=company_name,
        anchors=anchors,
        official_url=official_url,
    )
    unique_sources = _append_unique_wave_sources(
        existing_sources,
        sources,
        prefix=prefix,
    )
    verified = await verify_external_sources(
        unique_sources,
        company_name=company_name,
        anchors=anchors,
        max_documents=None if deep else 8,
        preserve_blocks=deep,
        include_official=True,
        timeout_seconds=25,
    )
    return verified, notes, diagnostics, triage, len(unique_sources)


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

    full_plan = research_queries if research_queries is not None else query_plan(company_hint, anchors=anchors)
    progressive_search = bool(deep and research_queries is None)
    initial_plan = initial_wave(full_plan) if progressive_search else research_queries
    attempted_queries = list(initial_plan if progressive_search else full_plan)
    search_waves_executed = 1
    optional_wave_model_used = False
    optional_wave_model_unavailable = False
    search_triage = SearchTriageSummary(total=0, selected=0, rejected=0)
    try:
        external_sources, notes, diagnostics = await collect_external_sources_adaptive(
            company_hint,
            url,
            region=anchors.primary_region,
            max_sources=None if deep else 100,
            anchors=anchors,
            query_overrides=initial_plan if progressive_search else research_queries,
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
        if progressive_search:
            notes.append(
                "Progressive search wave 1: "
                f"queries={len(initial_plan)}, full_plan={len(full_plan)}."
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
            attempted_queries.extend(follow_plan)
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
                more = _append_unique_wave_sources(external_sources, more, prefix="R")
                more_verified = await verify_external_sources(
                    more, company_name=company_hint, anchors=anchors, max_documents=None if deep else 8,
                    preserve_blocks=deep, timeout_seconds=25,
                )
                verified.extend(more_verified)
                notes.extend(more_notes)
                notes.append(
                    "Fast registry follow-up triage: "
                    f"total={follow_triage.total}, selected={follow_triage.selected}, rejected={follow_triage.rejected}."
                )
                diagnostics = SearchDiagnostics.aggregate([diagnostics, more_diagnostics])
                search_triage = _merge_search_triage(search_triage, follow_triage)
            except Exception as exc:
                notes.append(f"Уточняющая проверка реквизитов не завершена ({type(exc).__name__}).")

    coverage = assess_coverage(
        verified,
        [kind for kind, _ in attempted_queries],
    )

    if (progressive_search and search_waves_executed < MAX_PROGRESSIVE_WAVES
            and not (current_research() and current_research().stop_requested)):
        attempted_query_text = {query for _, query in attempted_queries}
        gaps = gap_wave(
            full_plan,
            coverage,
            already_attempted=attempted_query_text,
        )
        if gaps:
            search_waves_executed += 1
            attempted_queries.extend(gaps)
            try:
                (
                    gap_verified,
                    gap_notes,
                    gap_diagnostics,
                    gap_triage,
                    gap_selected,
                ) = await _search_verify_wave(
                    plan=gaps,
                    prefix=f"W{search_waves_executed}",
                    company_name=company_hint,
                    official_url=url,
                    anchors=anchors,
                    deep=deep,
                    existing_sources=external_sources,
                )
                verified.extend(gap_verified)
                notes.extend(gap_notes)
                notes.append(
                    "Progressive mandatory gap wave: "
                    f"queries={len(gaps)}, selected_urls={gap_selected}, "
                    f"verified_records={len(gap_verified)}."
                )
                diagnostics = SearchDiagnostics.aggregate([diagnostics, gap_diagnostics])
                search_triage = _merge_search_triage(search_triage, gap_triage)
            except Exception as exc:
                notes.append(
                    f"Progressive mandatory gap wave не завершена ({type(exc).__name__})."
                )
            coverage = assess_coverage(
                verified,
                [kind for kind, _ in attempted_queries],
            )

    if (progressive_search and search_waves_executed < MAX_PROGRESSIVE_WAVES
            and not (current_research() and current_research().stop_requested)):
        selection = await optional_wave(
            full_plan,
            coverage,
            company_name=company_hint,
            anchors=anchors,
            already_attempted={query for _, query in attempted_queries},
        )
        optional_wave_model_used = selection.model_used
        optional_wave_model_unavailable = selection.model_unavailable
        optional_plan = list(selection.queries)
        if optional_plan:
            search_waves_executed += 1
            attempted_queries.extend(optional_plan)
            try:
                (
                    optional_verified,
                    optional_notes,
                    optional_diagnostics,
                    optional_triage,
                    optional_selected,
                ) = await _search_verify_wave(
                    plan=optional_plan,
                    prefix=f"W{search_waves_executed}",
                    company_name=company_hint,
                    official_url=url,
                    anchors=anchors,
                    deep=deep,
                    existing_sources=external_sources,
                )
                verified.extend(optional_verified)
                notes.extend(optional_notes)
                notes.append(
                    "Progressive optional recovery/enrichment wave: "
                    f"queries={len(optional_plan)}/{selection.candidate_count}, "
                    f"selected_urls={optional_selected}, "
                    f"verified_records={len(optional_verified)}, "
                    f"model_used={selection.model_used}, "
                    f"model_unavailable={selection.model_unavailable}."
                )
                diagnostics = SearchDiagnostics.aggregate([diagnostics, optional_diagnostics])
                search_triage = _merge_search_triage(search_triage, optional_triage)
            except Exception as exc:
                notes.append(
                    f"Progressive optional wave не завершена ({type(exc).__name__})."
                )
            coverage = assess_coverage(
                verified,
                [kind for kind, _ in attempted_queries],
            )

    if progressive_search:
        notes.append(
            "Progressive search coverage: "
            f"waves={search_waves_executed}, "
            f"missing={','.join(coverage.missing_verticals) or 'none'}, "
            f"searched_without_evidence={','.join(coverage.searched_without_evidence) or 'none'}."
        )

    verified, postfetch_duplicate_documents = deduplicate_verified_documents(verified)
    if postfetch_duplicate_documents:
        notes.append(
            "Post-fetch document dedup before RouterAI extraction: "
            f"duplicates={postfetch_duplicate_documents}."
        )

    llm_sources, llm_projection = project_llm_sources(verified)
    if llm_projection.duplicate_official_quotes_removed:
        notes.append(
            "Extraction-only first-party quote dedup: "
            f"input={llm_projection.input_records}, "
            f"output={llm_projection.output_records}, "
            f"duplicates_removed={llm_projection.duplicate_official_quotes_removed}."
        )

    try:
        analysis = await analyze_with_routerai(
            url,
            title,
            text,
            llm_sources,
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

    document_evidence = collapse_verified_evidence(verified)
    analysis.sources = merge_document_sources(analysis.sources, document_evidence)
    _remap_analysis_source_ids(analysis)
    evidence_quality = assess_evidence_quality(analysis.sources)
    analysis.readiness.evidence_quality = evidence_quality.score
    identity = assess_identity_readiness(analysis.company_facts, sources=analysis.sources)
    analysis.readiness.identity_state = identity.state
    analysis.readiness.release_blockers = [
        blocker
        for blocker in analysis.readiness.release_blockers
        if not blocker.startswith("identity_")
    ]
    identity_blocker = identity_release_blocker(identity.state)
    if identity_blocker:
        analysis.readiness.release_blockers.append(identity_blocker)
    identity_vertical = next(
        (vertical for vertical in analysis.readiness.required_verticals if vertical.code == "identity"),
        None,
    )
    if identity_vertical is not None:
        identity_vertical.state = {
            "resolved": "verified",
            "provisional": "partially_verified",
            "conflicting": "degraded",
            "unresolved": "not_searched",
        }[identity.state]

    discovery_count = sum(1 for source in external_sources if source.lifecycle_state == "discovery_hint")
    candidate_count = sum(1 for source in external_sources if source.lifecycle_state == "source_candidate")
    evidence_count = len(document_evidence)
    evidence_blocks = sum(len(source.evidence_blocks) for source in document_evidence)
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
        f"verified_documents={evidence_count}, retained_blocks={evidence_blocks}."
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
    analysis.research_queries = [query for _, query in attempted_queries]
    analysis.research_status = {
        "extraction_input_coverage_complete": False,
        **analysis.research_status,
        "stage": "intermediate_report",
        "search_state": diagnostics.state,
        "search_progressive_enabled": progressive_search,
        "search_waves_executed": search_waves_executed,
        "search_queries_available": len(full_plan),
        "search_queries_executed": len(attempted_queries),
        "search_coverage_complete": coverage.search_complete,
        "search_coverage_missing_verticals": ",".join(coverage.missing_verticals),
        "search_coverage_searched_no_evidence": ",".join(coverage.searched_without_evidence),
        "search_optional_wave_model_used": optional_wave_model_used,
        "search_optional_wave_model_unavailable": optional_wave_model_unavailable,
        "search_results_triaged": search_triage.total,
        "search_results_selected": search_triage.selected,
        "search_results_rejected": search_triage.rejected,
        "search_triage_model_used": search_triage.model_used,
        "search_triage_model_unavailable": search_triage.model_unavailable,
        "discovery_hints": discovery_count,
        "source_candidates": candidate_count,
        "evidence_records": evidence_count,
        "evidence_blocks_retained": evidence_blocks,
        "postfetch_duplicate_documents": postfetch_duplicate_documents,
        "transitional_extraction_records": len(verified),
        "llm_source_records_input": llm_projection.input_records,
        "llm_source_records_output": llm_projection.output_records,
        "llm_duplicate_official_quotes_removed": llm_projection.duplicate_official_quotes_removed,
        "verified_documents": evidence_count,
        "unverified_documents": discovery_count + candidate_count,
        "preflight_excluded_documents": sum(s.preflight_decision == "exclude" for s in external_sources),
        "official_input_chars": len(text),
        "registry_authority_verified": False,
        "evidence_quality_unique_documents": evidence_quality.unique_documents,
        "evidence_quality_traceable_documents": evidence_quality.traceable_documents,
        "evidence_quality_confirmed_documents": evidence_quality.confirmed_documents,
        "evidence_quality_corroborated_documents": evidence_quality.corroborated_documents,
        "evidence_quality_weak_documents": evidence_quality.weak_documents,
        "identity_state": identity.state,
        "identity_critical_conflicts": identity.unresolved_critical_conflicts,
        "identity_conflict_fields": ",".join(identity.conflict_fields),
        "identity_authoritative_inn_count": len(identity.authoritative_identifiers["inn"]),
        "identity_authoritative_ogrn_count": len(identity.authoritative_identifiers["ogrn"]),
    }
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