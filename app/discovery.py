from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.heuristics import heuristic_analysis
from app.hunter_forensic_trace import HunterForensicTrace
from app.hunter_handbook import OPPORTUNITY_PATTERNS, resolve_industries
from app.hunter_lead_fit import classify_lead_fit, lead_fit_rank
from app.hunter_query_intelligence import generate_hunter_query_plan
from app.hunter_search_policy_authority import resolve_hunter_search_policy
from app.hunter_source_role import classify_source_role, role_rank
from app.models import HuntCandidate, HuntFunnel, HuntRequest, HuntResult
from app.scraper import FetchError, fetch_site
from app.search_observer import build_search_wave_telemetry
from app.search_observer_llm import evaluate_search_wave_shadow
from app.search_observer_runtime_steering import run_bounded_continuation_search
from app.search_observer_verified_promotion import verified_continuation_promotion_permit
from app.search_gateway import (
    SearchDiagnostics,
    SearchRequest,
    SearchResponse,
    get_search_gateway,
)
from app.search_gateway.models import GatewayState
from app.trace_ledger import TraceState


EXCLUDED_HOSTS = {
    "vk.com",
    "t.me",
    "youtube.com",
    "rutube.ru",
    "instagram.com",
    "facebook.com",
    "2gis.ru",
    "yandex.ru",
    "google.com",
    "avito.ru",
    "hh.ru",
}

COMMERCIAL_MARKERS = ("товар", "оборудован", "услуг", "подбор", "расчет", "заказать")
COMPLEXITY_MARKERS = ("опт", "производ", "монтаж", "проект", "комплектац", "прайс")
STRONG_INDUSTRY_MARKERS_BY_REQUEST = {
    "стоматология": (
        "стоматол",
        "лечение зуб",
        "имплант",
        "ортодонт",
        "эндодонт",
        "пародонт",
        "протезирован",
        "dental",
        "dentist",
        "stomatolog",
        "implantaci",
        "ortodont",
    ),
}

_TRUE_VALUES = {"1", "true", "yes", "on"}
_STEERING_REGIMES = {"auto", "precision", "balanced", "discovery"}


@dataclass(frozen=True)
class PreScoreResult:
    score: int | None
    status: str
    factors: dict[str, int | None]
    reasons: list[str]


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _observer_runtime_steering_config() -> tuple[bool, str, int]:
    enabled = os.getenv("HUNTER_SEARCH_OBSERVER_STEERING_ENABLED", "").strip().lower() in _TRUE_VALUES
    regime = os.getenv("HUNTER_SEARCH_OBSERVER_STEERING_REGIME", "auto").strip().lower() or "auto"
    if regime not in _STEERING_REGIMES:
        regime = "auto"
    raw_reserve = os.getenv("HUNTER_SEARCH_OBSERVER_RESERVE_QUERIES", "2").strip()
    try:
        reserve_queries = int(raw_reserve)
    except ValueError:
        reserve_queries = 2
    return enabled, regime, max(0, min(10, reserve_queries))


def _candidate_inspection_timeout_seconds() -> float:
    try:
        value = float(os.getenv("HUNTER_CANDIDATE_INSPECTION_TIMEOUT_SECONDS", "45"))
    except ValueError:
        value = 45.0
    return max(5.0, min(120.0, value))


def _build_queries(req: HuntRequest) -> list[str]:
    selected = resolve_industries(req.industries)
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = " ".join(query.split())
        if normalized and normalized not in seen and len(queries) < req.max_queries:
            seen.add(normalized)
            queries.append(normalized)

    for industry in selected:
        variants = [industry["name"], *industry["aliases"]]
        for variant in variants[:4]:
            add(f'{variant} {req.region} официальный сайт компания')
            if req.search_zone:
                add(f'{variant} {req.search_zone} официальный сайт')

        for signal_id in industry.get("signals", [])[:3]:
            signal = OPPORTUNITY_PATTERNS.get(signal_id, signal_id)
            add(f'{industry["name"]} {signal} {req.region} компания')

    for focus in req.focus:
        add(f'{focus} {req.region} компания официальный сайт')

    return queries


def _industry_markers(req: HuntRequest) -> list[str]:
    """Return bounded lexical markers from the normalized user-requested industry.

    We intentionally derive these from the actual requested values rather than every
    broad handbook alias. For example a dentistry hunt should not receive an
    industry-match merely because a result contains the generic word `клиника`.
    Strong, industry-specific synonyms may be added for a concrete requested
    industry so valid specialist pages are not lost when they omit the umbrella term.
    """
    markers: list[str] = []
    seen: set[str] = set()
    for industry in req.industries:
        normalized = " ".join(industry.casefold().replace("ё", "е").split())
        if not normalized:
            continue
        candidates = [normalized]
        for trigger, strong_markers in STRONG_INDUSTRY_MARKERS_BY_REQUEST.items():
            if trigger in normalized or normalized in trigger:
                candidates.extend(strong_markers)
        for token in normalized.replace(",", " ").split():
            clean = token.strip("-—–()[]{}.,:;!?")
            if len(clean) >= 5:
                stem_length = max(5, len(clean) - 3)
                candidates.append(clean[:stem_length])
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                markers.append(candidate)
    return markers[:20]


def _pre_score(req: HuntRequest, title: str, snippet: str, url: str) -> PreScoreResult:
    host = _domain(url)
    title = title.strip()
    snippet = snippet.strip()
    factors: dict[str, int | None] = {
        "region_match": None,
        "industry_match": None,
        "commercial_choice": None,
        "commercial_complexity": None,
        "focus_match": None,
        "local_domain": None,
    }
    reasons: list[str] = []

    if not host or not (title or snippet):
        reasons.append("недостаточно данных: отсутствует домен либо текст поискового результата")
        return PreScoreResult(None, "insufficient_data", factors, reasons)

    haystack = f"{title} {snippet} {url}".casefold().replace("ё", "е")
    region_tokens = [token for token in req.region.replace(",", " ").casefold().split() if len(token) > 3]
    if region_tokens:
        factors["region_match"] = 25 if any(token in haystack for token in region_tokens) else 0
        if factors["region_match"]:
            reasons.append("обнаружено соответствие территории охоты")

    industry_markers = _industry_markers(req)
    if industry_markers:
        factors["industry_match"] = 25 if any(marker in haystack for marker in industry_markers) else 0
        if factors["industry_match"]:
            reasons.append("обнаружено прямое соответствие заданной отрасли")

    factors["commercial_choice"] = 20 if any(marker in haystack for marker in COMMERCIAL_MARKERS) else 0
    if factors["commercial_choice"]:
        reasons.append("есть признаки коммерческого каталога или сложного выбора")

    factors["commercial_complexity"] = 15 if any(marker in haystack for marker in COMPLEXITY_MARKERS) else 0
    if factors["commercial_complexity"]:
        reasons.append("есть признаки содержательной коммерческой задачи")

    if req.focus:
        factors["focus_match"] = 10 if any(focus.casefold() in haystack for focus in req.focus) else 0
        if factors["focus_match"]:
            reasons.append("обнаружено соответствие заданному фокусу охоты")

    factors["local_domain"] = 5 if host.endswith(".ru") else 0
    if factors["local_domain"]:
        reasons.append("используется домен российской зоны")

    score = 20 + sum(value for value in factors.values() if value is not None)
    source_role = classify_source_role(title, snippet, url)
    if source_role in {"supporting_source", "blocked_source"}:
        score = min(score, 45)
        reasons.append("результат является источником для проверки, а не прямым сайтом компании")
    elif factors.get("industry_match") == 0 and factors.get("region_match") == 0:
        score = min(score, 30)
        reasons.append("не обнаружено ни отрасли, ни региона из задания охоты")
    return PreScoreResult(min(score, 100), "calculated", factors, reasons)


def _shallow_candidate(
    title: str,
    snippet: str,
    url: str,
    result: PreScoreResult,
    *,
    qualification: str,
    summary: str,
    recommendation: str,
) -> HuntCandidate:
    source_role = classify_source_role(title, snippet, url)
    lead_fit = classify_lead_fit(
        title=title,
        snippet=snippet,
        url=url,
        source_role=source_role,
    )
    return HuntCandidate(
        company_name=title or _domain(url),
        url=url,
        source_title=title,
        source_snippet=snippet[:500],
        region_confirmed=None,
        preliminary_score=result.score,
        pre_score_status=result.status,
        pre_score_factors=result.factors,
        deep_analysis_performed=False,
        final_score=result.score,
        qualification=qualification,
        business_summary=summary,
        recommended_solution=recommendation,
        reasons=result.reasons,
        lead_fit=lead_fit.fit,
        lead_fit_reason=lead_fit.reason,
        lead_fit_evidence=list(lead_fit.evidence),
        analysis=None,
    )


def _candidate_rank_role(candidate: HuntCandidate) -> str:
    role = classify_source_role(candidate.source_title, candidate.source_snippet, str(candidate.url))
    if role != "direct_candidate":
        return role
    industry_match = candidate.pre_score_factors.get("industry_match")
    region_match = candidate.pre_score_factors.get("region_match")
    if candidate.region_confirmed is False:
        return "possible_candidate"
    if industry_match == 0 and region_match == 0:
        return "noise"
    if industry_match and region_match == 0 and not candidate.deep_analysis_performed:
        return "possible_candidate"
    return "direct_candidate"


def _score_metadata(result: PreScoreResult) -> dict[str, object]:
    metadata: dict[str, object] = {
        "pre_score_status": result.status,
        "pre_score_reasons": result.reasons,
    }
    if result.score is not None:
        metadata["pre_score"] = result.score
    for factor, value in result.factors.items():
        metadata[f"factor_{factor}"] = value
    return metadata


async def run_hunt(req: HuntRequest) -> HuntResult:
    mission_id = f"hunt-{uuid4()}"
    correlation_id = f"corr-{uuid4()}"
    trace = HunterForensicTrace(mission_id, correlation_id)

    query_plan = await generate_hunter_query_plan(
        region=req.region,
        industries=req.industries,
        focus=req.focus,
        max_queries=req.max_queries,
    )
    effective_req = req
    if query_plan is not None:
        effective_req = req.model_copy(
            update={
                "region": query_plan.normalized_region,
                "industries": query_plan.normalized_industries or req.industries,
                "focus": query_plan.normalized_focus or req.focus,
            }
        )
        queries = query_plan.query_variants[: req.max_queries]
        query_intelligence_note = (
            "Query Intelligence: LLM-нормализация применена; "
            f"вариантов поиска: {len(queries)}."
        )
        plan_source = "llm"
    else:
        queries = _build_queries(req)
        query_intelligence_note = "Query Intelligence: fallback на детерминированный план поиска."
        plan_source = "deterministic_fallback"

    trace.append(
        "hunt_plan",
        state=TraceState.SUCCEEDED,
        reason_code="hunter_query_plan_built",
        summary="Hunter search plan and effective thresholds prepared",
        counters={
            "query_count": len(queries),
            "results_per_query": req.results_per_query,
            "max_candidates": req.max_candidates,
            "output_limit": req.output_limit,
        },
        metadata={
            "plan_source": plan_source,
            "input_region": req.region,
            "effective_region": effective_req.region,
            "input_industries": req.industries,
            "effective_industries": effective_req.industries,
            "input_focus": req.focus,
            "effective_focus": effective_req.focus,
            "queries": queries,
            "minimum_pre_score": req.minimum_pre_score,
            "deep_audit_score": req.deep_audit_score,
            "concurrency": req.concurrency,
        },
    )

    raw_results: list[dict] = []
    search_diagnostics: list[SearchDiagnostics] = []
    search_execution_failures: list[str] = []
    gateway = get_search_gateway()
    policy_resolution = resolve_hunter_search_policy()
    policy = policy_resolution.policy
    trace.append(
        "hunt_search_policy_resolved",
        state=TraceState.SUCCEEDED,
        reason_code="hunter_search_policy_authority_resolved",
        summary="Hunter SearchGateway execution policy authority resolved before provider calls",
        metadata={
            "authority": policy_resolution.authority.value,
            "selected_policy_fingerprint": policy_resolution.selected_policy_fingerprint,
            "env_policy_fingerprint": policy_resolution.env_policy_fingerprint,
            "admin_projection_fingerprint": policy_resolution.admin_projection_fingerprint,
            "policy_equivalent": policy_resolution.policy_equivalent,
            "admin_policy_persisted": policy_resolution.admin_policy_persisted,
        },
    )

    async def search_query(query: str):
        try:
            return await gateway.search(
                SearchRequest(
                    query=query,
                    limit=req.results_per_query,
                    mission_id=mission_id,
                    correlation_id=correlation_id,
                ),
                policy,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            search_execution_failures.append(type(exc).__name__)
            trace.append(
                "hunt_search_query_failed",
                state=TraceState.DEGRADED,
                reason_code="search_query_exception_contained",
                summary="One Hunter query failed; remaining queries continue",
                metadata={"error_type": type(exc).__name__},
            )
            return SearchResponse(
                results=[],
                diagnostics=SearchDiagnostics(state=GatewayState.UNAVAILABLE),
            )

    async def search_many(batch: list[str]):
        return await asyncio.gather(*(search_query(query) for query in batch))

    steering_enabled, steering_regime, reserve_queries = _observer_runtime_steering_config()
    steering_result = await run_bounded_continuation_search(
        queries,
        enabled=steering_enabled,
        requested_search_regime=steering_regime,
        requested_reserve_queries=reserve_queries,
        permit=verified_continuation_promotion_permit(),
        search_many=search_many,
        observe_wave=evaluate_search_wave_shadow,
    )
    responses = steering_result.responses
    queries = steering_result.executed_queries
    wave_telemetry = steering_result.first_wave_telemetry
    steering_decision = steering_result.steering_decision
    trace.append(
        "hunt_search_continuation_steering",
        state=TraceState.SUCCEEDED,
        reason_code=(
            "hunter_continuation_steering_evaluated"
            if steering_decision is not None
            else "hunter_continuation_steering_inactive"
        ),
        summary="Bounded continuation steering gate evaluated before reserve execution",
        counters={
            "first_wave_query_count": len(steering_result.wave_plan.first_wave_queries),
            "reserve_query_count": len(steering_result.wave_plan.reserve_queries),
            "executed_query_count": len(steering_result.executed_queries),
            "accepted_direction_count": (
                0 if steering_decision is None else len(steering_decision.accepted_direction_indexes)
            ),
        },
        metadata={
            "enabled_requested": steering_enabled,
            "requested_regime": steering_regime,
            "steering_enabled": steering_result.wave_plan.steering_enabled,
            "reserve_reordered": steering_result.reserve_reordered,
            "routing_changed": steering_result.reserve_reordered,
            "reason_code": steering_result.wave_plan.reason_code,
            "decision_reason_codes": (
                [] if steering_decision is None else list(steering_decision.reason_codes)
            ),
        },
    )
    trace.append(
        "hunt_search_wave_observed",
        state=TraceState.SUCCEEDED,
        reason_code="hunter_search_wave_observed",
        summary="Hunter first-wave yield telemetry captured for Search Observer",
        counters={
            "query_count": wave_telemetry.query_count,
            "result_count": wave_telemetry.result_count,
            "unique_domain_count": wave_telemetry.unique_domain_count,
            "degraded_attempts": wave_telemetry.degraded_attempts,
            "latency_ms_total": wave_telemetry.latency_ms_total,
        },
        metadata={
            "observer_mode": "steering" if steering_result.wave_plan.steering_enabled else "telemetry_only",
            "routing_changed": steering_result.reserve_reordered,
            "duplicate_domain_ratio": wave_telemetry.duplicate_domain_ratio,
            "provider_result_counts": wave_telemetry.provider_result_counts,
            "attempt_states": wave_telemetry.attempt_states,
            "total_cost_by_currency": {
                key: str(value) for key, value in wave_telemetry.total_cost_by_currency.items()
            },
            "direction_count": len(wave_telemetry.directions),
            "direction_yield_preview": [
                (
                    f"{index}:results={item.result_count};domains={item.unique_domain_count};"
                    f"dup={item.duplicate_domain_ratio};latency_ms={item.latency_ms_total};"
                    f"degraded={item.degraded_attempts};cache={str(item.cache_hit).lower()}"
                )
                for index, item in enumerate(wave_telemetry.directions)
            ],
        },
    )

    shadow_observer_enabled = os.getenv("HUNTER_SEARCH_OBSERVER_SHADOW_ENABLED", "").strip().lower() in _TRUE_VALUES
    if shadow_observer_enabled and not steering_result.wave_plan.steering_enabled:
        shadow_recommendation = await evaluate_search_wave_shadow(wave_telemetry)
        if shadow_recommendation is None:
            trace.append(
                "hunt_search_wave_shadow_observer",
                state=TraceState.SKIPPED,
                reason_code="hunter_shadow_observer_unavailable",
                summary="Shadow Search Observer produced no valid advisory recommendation",
                counters={"recommendation_count": 0},
                metadata={"observer_mode": "shadow", "routing_changed": False},
            )
        else:
            action_counts: dict[str, int] = {}
            for item in shadow_recommendation.recommendations:
                action = str(item.action)
                action_counts[action] = action_counts.get(action, 0) + 1
            trace.append(
                "hunt_search_wave_shadow_observer",
                state=TraceState.SUCCEEDED,
                reason_code="hunter_shadow_observer_advisory_captured",
                summary="Shadow Search Observer advisory captured without execution",
                counters={"recommendation_count": len(shadow_recommendation.recommendations)},
                metadata={
                    "observer_mode": "shadow",
                    "routing_changed": False,
                    "sufficient_evidence": shadow_recommendation.sufficient_evidence,
                    "action_counts": action_counts,
                    "summary": shadow_recommendation.summary,
                },
            )

    for response in responses:
        search_diagnostics.append(response.diagnostics)
        raw_results.extend(item.as_legacy_dict() for item in response.results)
    aggregate = SearchDiagnostics.aggregate(search_diagnostics)
    if not raw_results:
        funnel = HuntFunnel()
        trace.append(
            "hunt_funnel_complete",
            state=TraceState.SUCCEEDED,
            reason_code="hunter_no_raw_results",
            summary="Hunter completed with no raw search results",
            counters=funnel.model_dump(),
        )
        return HuntResult(
            trace_mission_id=mission_id,
            trace_attempt_id=correlation_id,
            region=effective_req.region,
            search_zone=req.search_zone,
            queries=queries,
            discovered=0,
            candidates=[],
            funnel=funnel,
            notes=[
                query_intelligence_note,
                *(
                    [f"Изолированы ошибки поисковых направлений: {len(search_execution_failures)}; успешные направления сохранены."]
                    if search_execution_failures else []
                ),
                f"Поиск не дал результатов: gateway state={aggregate.state}.",
            ],
            search=aggregate,
        )

    unique: dict[str, dict] = {}
    excluded_count = 0
    duplicate_count = 0
    pool_omitted_count = 0
    for raw_rank, item in enumerate(raw_results, start=1):
        url = str(item.get("url") or "")
        title = str(item.get("title") or "")
        host = _domain(url)
        identity = host or f"raw-{raw_rank}"
        if not host:
            excluded_count += 1
            trace.append(
                "candidate_excluded",
                state=TraceState.SKIPPED,
                reason_code="missing_candidate_host",
                summary="Search result excluded because no usable host was present",
                identity=identity,
                url=url,
                title=title,
                counters={"raw_rank": raw_rank},
            )
            continue
        if host in EXCLUDED_HOSTS or any(host.endswith(f".{excluded}") for excluded in EXCLUDED_HOSTS):
            excluded_count += 1
            trace.append(
                "candidate_excluded",
                state=TraceState.SKIPPED,
                reason_code="excluded_host",
                summary="Search result excluded by Hunter host policy",
                identity=host,
                url=url,
                title=title,
                counters={"raw_rank": raw_rank},
                metadata={"candidate_host": host},
            )
            continue
        if host in unique:
            duplicate_count += 1
            trace.append(
                "candidate_deduplicated",
                state=TraceState.SKIPPED,
                reason_code="duplicate_domain",
                summary="Duplicate domain removed from Hunter candidate pool",
                identity=host,
                url=url,
                title=title,
                counters={"raw_rank": raw_rank},
                metadata={"candidate_host": host},
            )
            continue
        if len(unique) >= req.max_candidates:
            pool_omitted_count += 1
            trace.append(
                "candidate_pool_omitted",
                state=TraceState.SKIPPED,
                reason_code="max_candidates_reached",
                summary="Unique result omitted because Hunter candidate pool is full",
                identity=host,
                url=url,
                title=title,
                counters={"raw_rank": raw_rank, "max_candidates": req.max_candidates},
                metadata={"candidate_host": host},
            )
            continue
        unique[host] = item
        trace.append(
            "candidate_dedupe_retained",
            state=TraceState.SUCCEEDED,
            reason_code="unique_domain_retained",
            summary="Unique domain retained for Hunter qualification",
            identity=host,
            url=url,
            title=title,
            counters={"raw_rank": raw_rank, "unique_rank": len(unique)},
            metadata={"candidate_host": host},
        )

    async def inspect(item: dict) -> HuntCandidate | None:
        url = str(item.get("url") or "")
        raw_title = str(item.get("title") or "")
        display_title = raw_title or _domain(url)
        snippet = str(item.get("content") or item.get("snippet") or "")
        host = _domain(url) or "unknown"
        result = _pre_score(effective_req, raw_title, snippet, url)
        trace.append(
            "candidate_pre_scored",
            state=TraceState.SUCCEEDED if result.status == "calculated" else TraceState.DEGRADED,
            reason_code="pre_score_calculated" if result.status == "calculated" else "pre_score_insufficient_data",
            summary="Hunter candidate pre-score evaluated",
            identity=host,
            url=url,
            title=display_title,
            metadata=_score_metadata(result),
        )

        if result.status == "insufficient_data":
            trace.append(
                "candidate_observation",
                state=TraceState.DEGRADED,
                reason_code="insufficient_data_retained",
                summary="Candidate retained as observation because pre-score data is insufficient",
                identity=host,
                url=url,
                title=display_title,
            )
            return _shallow_candidate(
                display_title,
                snippet,
                url,
                result,
                qualification="Недостаточно данных",
                summary="Pre-score не рассчитан: поисковый результат не содержит достаточных признаков.",
                recommendation="Уточнить поисковый результат или получить первичный документ до глубокой разведки.",
            )

        assert result.score is not None
        if result.score < req.minimum_pre_score:
            trace.append(
                "candidate_rejected",
                state=TraceState.SKIPPED,
                reason_code="below_minimum_pre_score",
                summary="Candidate rejected below Hunter minimum pre-score",
                identity=host,
                url=url,
                title=display_title,
                counters={"pre_score": result.score, "minimum_pre_score": req.minimum_pre_score},
                metadata=_score_metadata(result),
            )
            return None

        if result.score < req.deep_audit_score:
            trace.append(
                "candidate_observation",
                state=TraceState.SUCCEEDED,
                reason_code="below_deep_audit_score",
                summary="Candidate retained as observation below deep-audit threshold",
                identity=host,
                url=url,
                title=display_title,
                counters={"pre_score": result.score, "deep_audit_score": req.deep_audit_score},
                metadata=_score_metadata(result),
            )
            return _shallow_candidate(
                display_title,
                snippet,
                url,
                result,
                qualification="Наблюдение",
                summary="Кандидат прошёл минимальный pre-score, но не достиг порога глубокой разведки.",
                recommendation="Сохранить в наблюдении и усилить признаки до запуска глубокой обработки.",
            )

        trace.append(
            "candidate_deep_audit_started",
            state=TraceState.STARTED,
            reason_code="deep_audit_threshold_met",
            summary="Candidate crossed deep-audit threshold and site processing started",
            identity=host,
            url=url,
            title=display_title,
            counters={"pre_score": result.score, "deep_audit_score": req.deep_audit_score},
        )
        try:
            page = await fetch_site(url)
            analysis = heuristic_analysis(page["final_url"], page["title"], page["text"])
            regional_text = f'{page["title"]} {page["text"][:12000]}'.lower()
            region_tokens = [token for token in effective_req.region.lower().split() if len(token) > 3]
            region_confirmed = any(token in regional_text for token in region_tokens)
            final_score = round((result.score + analysis.commercial_opportunity.score) / 2)
            reasons = list(result.reasons)
            qualification = analysis.commercial_opportunity.qualification
            source_role = classify_source_role(display_title, snippet, url)
            lead_fit = classify_lead_fit(
                title=display_title,
                snippet=snippet,
                url=url,
                source_role=source_role,
                analysis=analysis,
            )
            if not region_confirmed:
                final_score = min(49, max(0, final_score - 20))
                qualification = "Наблюдение"
                reasons.append("региональная принадлежность не подтверждена; прямой лид понижен до наблюдения")
            trace.append(
                "candidate_deep_audit_completed",
                state=TraceState.SUCCEEDED,
                reason_code="deep_audit_completed",
                summary="Candidate site processed and final Hunter score produced",
                identity=host,
                url=str(analysis.url),
                title=analysis.company_name,
                counters={
                    "pre_score": result.score,
                    "analysis_score": analysis.commercial_opportunity.score,
                    "final_score": final_score,
                },
                metadata={
                    "region_confirmed": region_confirmed,
                    "qualification": qualification,
                    "source_host": host,
                    "source_role": source_role,
                    "lead_fit": lead_fit.fit,
                    "lead_fit_reason": lead_fit.reason,
                    "lead_fit_evidence": list(lead_fit.evidence),
                },
            )
            return HuntCandidate(
                company_name=analysis.company_name,
                url=analysis.url,
                source_title=display_title,
                source_snippet=snippet[:500],
                region_confirmed=region_confirmed,
                preliminary_score=result.score,
                pre_score_status=result.status,
                pre_score_factors=result.factors,
                deep_analysis_performed=True,
                final_score=final_score,
                qualification=qualification,
                business_summary=analysis.business_summary,
                recommended_solution=analysis.commercial_opportunity.recommended_solution,
                reasons=reasons,
                lead_fit=lead_fit.fit,
                lead_fit_reason=lead_fit.reason,
                lead_fit_evidence=list(lead_fit.evidence),
                analysis=analysis,
            )
        except (FetchError, httpx.HTTPError, ValueError) as exc:
            trace.append(
                "candidate_deep_audit_failed",
                state=TraceState.DEGRADED,
                reason_code="deep_audit_fetch_or_analysis_failed",
                summary="Candidate crossed deep-audit threshold but site processing failed",
                identity=host,
                url=url,
                title=display_title,
                counters={"pre_score": result.score},
                metadata={"error_type": type(exc).__name__},
            )
            fallback = _shallow_candidate(
                display_title,
                snippet,
                url,
                result,
                qualification="Наблюдение",
                summary="Порог глубокой разведки достигнут, но первичный сайт не удалось обработать.",
                recommendation="Повторить загрузку или проверить сайт вручную.",
            )
            fallback.reasons.append(f"глубокая обработка не выполнена: {type(exc).__name__}")
            return fallback

    semaphore = asyncio.Semaphore(req.concurrency)

    async def guarded(item: dict) -> HuntCandidate | None:
        async with semaphore:
            try:
                return await inspect(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                url = str(item.get("url") or "")
                title = str(item.get("title") or _domain(url))
                snippet = str(item.get("content") or item.get("snippet") or "")
                result = _pre_score(effective_req, title, snippet, url)
                trace.append(
                    "candidate_processing_failed",
                    state=TraceState.DEGRADED,
                    reason_code="candidate_exception_contained",
                    summary="Candidate processing failed; shallow observation retained",
                    identity=_domain(url) or "unknown",
                    url=url,
                    title=title,
                    metadata={"error_type": type(exc).__name__},
                )
                fallback = _shallow_candidate(
                    title, snippet, url, result,
                    qualification="Недостаточно данных",
                    summary="Кандидат найден, но его обработка завершилась частично.",
                    recommendation="Повторить проверку первичного сайта.",
                )
                fallback.reasons.append(f"ошибка кандидата изолирована: {type(exc).__name__}")
                return fallback

    candidate_items = list(unique.values())
    task_items = {
        asyncio.create_task(guarded(item)): item
        for item in candidate_items
    }
    done, pending = await asyncio.wait(
        task_items,
        timeout=_candidate_inspection_timeout_seconds(),
    )
    inspected = [task.result() for task in done]
    timed_out_candidates = 0
    for task in pending:
        task.cancel()
        item = task_items[task]
        url = str(item.get("url") or "")
        title = str(item.get("title") or _domain(url))
        snippet = str(item.get("content") or item.get("snippet") or "")
        score = _pre_score(effective_req, title, snippet, url)
        if score.status == "calculated" and score.score is not None and score.score < req.minimum_pre_score:
            continue
        fallback = _shallow_candidate(
            title, snippet, url, score,
            qualification="Недостаточно данных",
            summary="Кандидат найден; глубокая проверка отложена из-за общего лимита времени.",
            recommendation="Продолжить проверку кандидата отдельным аудитом.",
        )
        fallback.reasons.append("общий лимит времени Hunter: глубокая проверка не завершена")
        inspected.append(fallback)
        timed_out_candidates += 1
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    candidates = [candidate for candidate in inspected if candidate is not None]
    candidates.sort(
        key=lambda candidate: (
            role_rank(_candidate_rank_role(candidate)),
            lead_fit_rank(candidate.lead_fit),
            candidate.pre_score_status == "calculated",
            candidate.final_score if candidate.final_score is not None else -1,
            candidate.preliminary_score if candidate.preliminary_score is not None else -1,
        ),
        reverse=True,
    )
    returned = candidates[: req.output_limit]
    for rank, candidate in enumerate(candidates, start=1):
        url = str(candidate.url)
        host = _domain(url) or f"candidate-{rank}"
        if rank <= req.output_limit:
            trace.append(
                "candidate_returned",
                state=TraceState.SUCCEEDED,
                reason_code="within_output_limit",
                summary="Qualified candidate returned in Hunter response",
                identity=host,
                url=url,
                title=candidate.company_name,
                counters={"output_rank": rank, "final_score": candidate.final_score or 0},
                metadata={
                    "qualification": candidate.qualification,
                    "deep_analysis_performed": candidate.deep_analysis_performed,
                    "region_confirmed": candidate.region_confirmed,
                    "industry_match": candidate.pre_score_factors.get("industry_match"),
                    "source_role": _candidate_rank_role(candidate),
                    "lead_fit": candidate.lead_fit,
                    "lead_fit_reason": candidate.lead_fit_reason,
                    "lead_fit_evidence": candidate.lead_fit_evidence,
                },
            )
        else:
            trace.append(
                "candidate_output_omitted",
                state=TraceState.SKIPPED,
                reason_code="output_limit_reached",
                summary="Qualified candidate omitted from response by Hunter output limit",
                identity=host,
                url=url,
                title=candidate.company_name,
                counters={"qualified_rank": rank, "output_limit": req.output_limit},
                metadata={
                    "qualification": candidate.qualification,
                    "region_confirmed": candidate.region_confirmed,
                    "industry_match": candidate.pre_score_factors.get("industry_match"),
                    "source_role": _candidate_rank_role(candidate),
                    "lead_fit": candidate.lead_fit,
                    "lead_fit_reason": candidate.lead_fit_reason,
                },
            )

    funnel = HuntFunnel(
        raw_results=len(raw_results),
        excluded_results=excluded_count,
        duplicate_results=duplicate_count,
        pool_omitted_results=pool_omitted_count,
        unique_candidates=len(unique),
        inspected_candidates=len(inspected),
        qualified_candidates=len(candidates),
        returned_candidates=len(returned),
        output_omitted_candidates=max(0, len(candidates) - len(returned)),
    )
    trace.append(
        "hunt_funnel_complete",
        state=TraceState.SUCCEEDED,
        reason_code="hunter_candidate_funnel_completed",
        summary="Hunter candidate funnel completed with bounded forensic counters",
        counters=funnel.model_dump(),
    )
    return HuntResult(
        trace_mission_id=mission_id,
        trace_attempt_id=correlation_id,
        region=effective_req.region,
        search_zone=req.search_zone,
        queries=queries,
        discovered=len(unique),
        candidates=returned,
        funnel=funnel,
        notes=[
            query_intelligence_note,
            *(
                [f"Изолированы ошибки поисковых направлений: {len(search_execution_failures)}; успешные направления сохранены."]
                if search_execution_failures else []
            ),
            *(
                [f"По общему лимиту времени отложена глубокая проверка кандидатов: {timed_out_candidates}; поверхностные карточки сохранены."]
                if timed_out_candidates else []
            ),
            "План охоты сформирован по Справочнику охотника.",
            "Каждый кандидат получает объяснимый pre-score либо явный статус insufficient_data.",
            f"Глубокая обработка запускается только при pre-score >= {req.deep_audit_score}.",
            "Количество найденных ссылок не входит в формулу pre-score.",
            "Каталоги, рейтинги и редакционные источники сохраняются для проверки, но не конкурируют с прямыми компаниями в lead-ranking.",
            "Внутри сопоставимого качества прямых сайтов коммерчески подтвержденные кандидаты ранжируются выше неизвестных, а явно институциональные остаются в разведке ниже них.",
        ],
        search=aggregate,
    )
