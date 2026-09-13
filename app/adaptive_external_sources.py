from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.external_sources import (
    IdentityAnchors,
    classification_state,
    classify_result,
    classify_source_domain,
    query_plan,
)
from app.hunter_search_policy_authority import resolve_hunter_search_policy
from app.trace_context import current_trace_identity
from app.research_control import current_research, deep_research_enabled
from app.search_gateway.models import SearchResponse
from app.models import IntelligenceSource, SourceKind
from app.search_gateway import (
    SearchDiagnostics,
    SearchRequest,
    get_search_gateway,
    search_policy_from_env,
)


VERTICAL_TERMS: dict[SourceKind, str] = {
    "official": "официальный сайт",
    "contact": "телефон email адрес контакты",
    "registry": "ИНН ОГРН ЕГРЮЛ реквизиты",
    "finance": "выручка прибыль активы налоги бухгалтерская отчетность",
    "workforce": "численность сотрудники персонал",
    "jobs": "вакансии работодатель команда сотрудники",
    "ownership": "учредитель генеральный директор владелец бенефициар",
    "affiliation": "аффилированные лица связанные компании группа компаний",
    "arbitration": "арбитраж истец ответчик дело",
    "court": "суд иск решение взыскание",
    "enforcement": "ФССП исполнительное производство задолженность",
    "news": "новости",
    "review": "отзывы клиентов",
    "social": "site:vk.com OR site:t.me OR site:ok.ru",
    "tender": "тендер OR закупка OR контракт",
    "patent": "патент OR изобретение",
    "other": "продукция услуги клиенты партнеры поставщики",
}


def relaxed_query(
    kind: SourceKind,
    company_name: str,
    *,
    region: str | None = None,
    anchors: IdentityAnchors | None = None,
) -> str:
    """Build a less brittle query without discarding strong entity anchors.

    Quotes around the company/region are intentionally relaxed. Registration
    identifiers, official domain and phone remain in the query whenever known.
    """
    anchors = anchors or IdentityAnchors()
    effective_region = region or anchors.primary_region
    terms = VERTICAL_TERMS.get(kind, "")

    parts: list[str] = []
    if kind == "official" and anchors.domain:
        parts.append(f"site:{anchors.domain}")
    parts.append(company_name.strip())

    # Strong identity anchors survive relaxation. Prefer registration IDs, then
    # phone/domain, then region. Do not append region twice.
    if anchors.inn:
        parts.append(anchors.inn)
    if anchors.ogrn:
        parts.append(anchors.ogrn)
    if kind == "contact" and anchors.phones:
        parts.append(anchors.phones[0])
    elif anchors.domain and kind != "official" and not (anchors.inn or anchors.ogrn):
        parts.append(anchors.domain)
    if effective_region and not (anchors.inn or anchors.ogrn):
        parts.append(effective_region)

    if terms:
        parts.append(terms)
    return " ".join(part for part in parts if part).strip()


def _should_relax(response) -> bool:
    return not response.results or str(response.diagnostics.state) != "success"


async def collect_external_sources_adaptive(
    company_name: str,
    official_url: str | None,
    region: str | None = None,
    max_sources: int | None = 60,
    anchors: IdentityAnchors | None = None,
    query_overrides: list[tuple[SourceKind, str]] | None = None,
) -> tuple[list[IntelligenceSource], list[str], SearchDiagnostics]:
    """Run exact queries first and one relaxed fallback only where needed."""
    anchors = anchors or IdentityAnchors()
    notes: list[str] = []
    plan = query_overrides if query_overrides is not None else query_plan(company_name, region, anchors)
    per_query = 100 if deep_research_enabled() else max(2, min(5, (max_sources or 60) // max(1, len(plan)) + 1))
    semaphore = asyncio.Semaphore(6)
    trace_identity = current_trace_identity()
    mission_id = trace_identity.mission_id if trace_identity else f"company-{uuid4()}"
    correlation_id = f"corr-{uuid4()}"
    gateway = get_search_gateway()
    policy = resolve_hunter_search_policy().policy

    async def execute(kind: SourceKind, query: str, variant: str):
        async with semaphore:
            if current_research() and current_research().stop_requested:
                return SearchResponse(results=[], diagnostics=SearchDiagnostics(state="unavailable"))
            response = await gateway.search(
                SearchRequest(
                    query=query[:400],
                    limit=per_query,
                    mission_id=mission_id,
                    correlation_id=f"{correlation_id}-{kind}-{variant}",
                ),
                policy,
            )
            return response

    async def run_vertical(kind: SourceKind, exact_query: str):
        exact = await execute(kind, exact_query, "exact")
        responses = [("exact", exact_query, exact)]
        if query_overrides is None and _should_relax(exact):
            fallback = relaxed_query(
                kind,
                company_name,
                region=region,
                anchors=anchors,
            )
            if fallback and fallback != exact_query:
                relaxed = await execute(kind, fallback, "relaxed")
                responses.append(("relaxed", fallback, relaxed))
        return kind, responses

    batches = await asyncio.gather(
        *(run_vertical(kind, exact_query) for kind, exact_query in plan)
    )

    raw: list[dict] = []
    diagnostics: list[SearchDiagnostics] = []
    for kind, responses in batches:
        if len(responses) > 1:
            exact_state = responses[0][2].diagnostics.state
            notes.append(
                f"Поиск для типа {kind}: exact={exact_state}; выполнен relaxed fallback."
            )
        for variant, query, response in responses:
            diagnostics.append(response.diagnostics)
            if response.diagnostics.state != "success":
                notes.append(
                    f"Поиск для типа {kind}/{variant} выполнен в состоянии "
                    f"{response.diagnostics.state}."
                )
            for item in response.results:
                copy = item.as_legacy_dict()
                copy["_query_kind"] = kind
                copy["_query"] = query
                copy["_query_variant"] = variant
                raw.append(copy)

    official_host = None
    if official_url:
        from urllib.parse import urlparse

        official_host = (urlparse(official_url).hostname or "").lower() or None

    accessed_at = datetime.now(timezone.utc).isoformat()
    seen: set[str] = set()
    sources: list[IntelligenceSource] = []
    for item in raw:
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = str(item.get("title") or url)[:300]
        snippet = str(item.get("content") or item.get("snippet") or "")[:900]
        query_kind: SourceKind = item.get("_query_kind") or "unknown"
        source_class = classify_source_domain(url, official_host)
        result_kind = classify_result(title, snippet)
        variant = str(item.get("_query_variant") or "exact")
        sources.append(
            IntelligenceSource(
                id=f"H{len(sources) + 1}",
                title=title,
                url=url,
                snippet=snippet,
                accessed_at=accessed_at,
                query_kind=query_kind,
                result_kind=result_kind,
                source_class=source_class,
                classification_state=classification_state(source_class, result_kind),
                lifecycle_state="discovery_hint",
                evidence_level="unverified_mention",
                verification_note=(
                    "Поисковый сниппет; первичный документ не загружен и не проверен. "
                    f"query_variant={variant}."
                ),
            )
        )
        if max_sources is not None and len(sources) >= max_sources:
            break

    return sources, notes, SearchDiagnostics.aggregate(diagnostics)
