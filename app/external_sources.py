from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.heuristics import heuristic_analysis
from app.llm import analyze_with_routerai
from app.models import IntelligenceSource, SiteAnalysis, SourceKind
from app.search_gateway import (
    SearchDiagnostics,
    SearchRequest,
    get_search_gateway,
    search_policy_from_env,
)


HOST_CLASSES: dict[str, set[str]] = {
    "registry": {"companies.rbc.ru", "rusprofile.ru", "www.rusprofile.ru", "checko.ru", "www.checko.ru", "list-org.com", "www.list-org.com", "audit-it.ru", "www.audit-it.ru", "egrul.nalog.ru", "bo.nalog.ru"},
    "finance": {"bo.nalog.ru", "audit-it.ru", "www.audit-it.ru", "companies.rbc.ru", "checko.ru", "www.checko.ru"},
    "workforce": {"hh.ru", "www.hh.ru", "superjob.ru", "www.superjob.ru", "zarplata.ru", "www.zarplata.ru"},
    "contact": {"2gis.ru", "www.2gis.ru", "yandex.ru", "www.yandex.ru"},
    "review": {"flamp.ru", "www.flamp.ru", "zoon.ru", "www.zoon.ru", "otzovik.com", "www.otzovik.com", "irecommend.ru", "www.irecommend.ru"},
    "social": {"vk.com", "www.vk.com", "ok.ru", "www.ok.ru", "t.me", "telegram.me", "youtube.com", "www.youtube.com", "rutube.ru", "www.rutube.ru", "dzen.ru", "www.dzen.ru"},
    "tender": {"zakupki.gov.ru", "www.zakupki.gov.ru", "rostender.info", "www.rostender.info", "b2b-center.ru", "www.b2b-center.ru"},
    "patent": {"new.fips.ru", "fips.ru", "www.fips.ru", "patents.google.com"},
    "court": {"sudrf.ru", "www.sudrf.ru", "mos-gorsud.ru", "www.mos-gorsud.ru", "sudact.ru", "www.sudact.ru"},
    "arbitration": {"kad.arbitr.ru", "arbitr.ru", "www.arbitr.ru", "ras.arbitr.ru"},
    "enforcement": {"fssp.gov.ru", "www.fssp.gov.ru", "bankrot.fedresurs.ru", "fedresurs.ru", "www.fedresurs.ru"},
    "aggregator": {"spark-interfax.ru", "www.spark-interfax.ru", "sbis.ru", "www.sbis.ru", "companies.rbc.ru"},
}

NEWS_MARKERS = ("news", "vedomosti", "kommersant", "rbc.ru", "tass.ru", "ria.ru", "interfax", "ngs.ru")
RESULT_MARKERS: list[tuple[SourceKind, tuple[str, ...]]] = [
    ("jobs", ("ваканс", "работодатель", "карьера", "работа в компании")),
    ("court", ("суд", "иск", "решение суда", "судебн")),
    ("arbitration", ("арбитраж", "истец", "ответчик", "дело №")),
    ("news", ("новости", "сообщает", "опубликовал", "пресс-релиз")),
    ("registry", ("инн", "огрн", "егрюл", "регистрац")),
    ("finance", ("выручка", "прибыль", "бухгалтерская отчетность", "финансовые показатели")),
    ("contact", ("телефон", "email", "адрес", "контакты")),
    ("official", ("официальный сайт", "официальная страница")),
]


@dataclass(frozen=True)
class IdentityAnchors:
    domain: str | None = None
    legal_name: str | None = None
    inn: str | None = None
    ogrn: str | None = None
    cities: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()

    @property
    def primary_region(self) -> str | None:
        return self.cities[0] if self.cities else None


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _first_group(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def extract_identity_anchors(text: str, official_url: str | None) -> IdentityAnchors:
    """Extract high-confidence identity hints from already crawled official evidence.

    These values are used only to constrain discovery queries; downstream search
    snippets remain discovery hints until primary documents are fetched/verified.
    """
    compact = " ".join(text.split())
    legal_match = re.search(
        r"\b((?:ООО|АО|ПАО)\s*[«\"]?[^\n|;]{2,80}?[»\"]?)(?=\s+(?:ИНН|КПП|ОГРН|Лиценз|Адрес|Телефон)|[.,;]|$)",
        compact,
        re.IGNORECASE,
    )
    legal_name = legal_match.group(1).strip(" .,") if legal_match else None
    inn = _first_group(r"\bИНН\s*[:№]?\s*(\d{10}|\d{12})\b", compact)
    ogrn = _first_group(r"\bОГРН\s*[:№]?\s*(\d{13}|\d{15})\b", compact)

    cities: list[str] = []
    for match in re.finditer(
        r"(?:\bг\.?\s*|город\s+)([А-ЯЁ][А-Яа-яЁё-]{2,40})",
        compact,
        re.IGNORECASE,
    ):
        city = match.group(1).strip(" ,.;")
        normalized = city[:1].upper() + city[1:]
        if normalized.casefold() not in {item.casefold() for item in cities}:
            cities.append(normalized)
        if len(cities) >= 3:
            break

    phones: list[str] = []
    for raw in re.findall(r"(?:\+7|8)\s*\(?\d{3,4}\)?(?:[\s\-–‒]?\d){6,8}", compact):
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("8") and len(digits) in {11, 12}:
            digits = "7" + digits[1:]
        if digits.startswith("7") and 11 <= len(digits) <= 12:
            normalized = "+" + digits[:11]
            if normalized not in phones:
                phones.append(normalized)
        if len(phones) >= 3:
            break

    return IdentityAnchors(
        domain=_host(official_url or "") or None,
        legal_name=legal_name,
        inn=inn,
        ogrn=ogrn,
        cities=tuple(cities),
        phones=tuple(phones),
    )


def classify_source_domain(url: str, official_host: str | None) -> SourceKind:
    host = _host(url)
    if not host:
        return "unknown"
    if official_host and (host == official_host or host.endswith(f".{official_host}")):
        return "official"
    for source_class, hosts in HOST_CLASSES.items():
        if host in hosts:
            return source_class  # type: ignore[return-value]
    if any(marker in host for marker in NEWS_MARKERS):
        return "news"
    return "unknown"


def classify_result(title: str, snippet: str) -> SourceKind:
    haystack = f"{title} {snippet}".lower()
    matches = [kind for kind, markers in RESULT_MARKERS if any(marker in haystack for marker in markers)]
    if not matches:
        return "unknown"
    if len(set(matches)) > 1:
        return "other"
    return matches[0]


def classification_state(source_class: SourceKind, result_kind: SourceKind) -> str:
    if source_class == "unknown" and result_kind == "unknown":
        return "unknown"
    if source_class not in {"unknown", "aggregator"} and result_kind not in {"unknown", source_class}:
        return "ambiguous"
    return "classified"


def verified_evidence_level(source_class: SourceKind) -> str:
    if source_class == "official":
        return "confirmed_fact"
    if source_class in {"registry", "finance", "court", "arbitration", "enforcement", "news", "tender", "patent"}:
        return "corroborated_signal"
    if source_class in {"ownership", "affiliation", "workforce", "contact", "review", "social", "jobs"}:
        return "weak_signal"
    return "unverified_mention"


def source_type(source_class: SourceKind) -> str:
    return {
        "official": "official_page", "registry": "registry", "court": "court",
        "arbitration": "arbitration", "enforcement": "enforcement",
        "ownership": "ownership", "affiliation": "affiliation", "finance": "finance",
        "workforce": "workforce", "contact": "contact", "news": "news", "social": "social",
        "review": "review", "jobs": "jobs", "tender": "tender", "patent": "patent",
    }.get(source_class, "external_source")


def _identity_query_base(company_name: str, anchors: IdentityAnchors) -> str:
    if anchors.inn:
        return f'"{company_name}" "{anchors.inn}"'
    if anchors.primary_region:
        return f'"{company_name}" "{anchors.primary_region}"'
    if anchors.domain:
        return f'"{company_name}" "{anchors.domain}"'
    return f'"{company_name}"'


def query_plan(
    company_name: str,
    region: str | None = None,
    anchors: IdentityAnchors | None = None,
) -> list[tuple[SourceKind, str]]:
    anchors = anchors or IdentityAnchors()
    effective_region = region or anchors.primary_region
    suffix = f' "{effective_region}"' if effective_region else ""
    base = _identity_query_base(company_name, anchors)
    domain = anchors.domain
    inn = anchors.inn
    ogrn = anchors.ogrn
    phone = anchors.phones[0] if anchors.phones else None

    official_query = f'site:{domain} "{company_name}"' if domain else f'{base} официальный сайт{suffix}'
    registry_ids = " ".join(f'"{item}"' for item in (inn, ogrn) if item)
    registry_base = registry_ids or base
    contact_anchor = f' "{phone}"' if phone else suffix

    return [
        ("official", official_query),
        ("official", f'site:{domain} о компании команда руководство история' if domain else f'{base} о компании команда'),
        ("official", f'site:{domain} продукция услуги каталог проекты филиалы' if domain else f'{base} продукция услуги филиалы'),
        ("official", f'site:{domain} контакты реквизиты ИНН ОГРН' if domain else f'{base} реквизиты'),
        ("contact", f'{base} телефон email адрес контакты{contact_anchor}'),
        ("registry", f'{registry_base} ИНН ОГРН выписка ЕГРЮЛ{suffix}'),
        ("finance", f'{registry_base} выручка прибыль активы налоги бухгалтерская отчетность'),
        ("workforce", f'{base} численность сотрудников среднесписочная численность{suffix}'),
        ("jobs", f'{base} вакансии работодатель команда сотрудники{suffix}'),
        ("ownership", f'{registry_base} учредитель генеральный директор владелец бенефициар'),
        ("affiliation", f'{registry_base} аффилированные лица связанные компании группа компаний'),
        ("arbitration", f'{registry_base} арбитражный суд истец ответчик дело'),
        ("court", f'{registry_base} суд иск решение взыскание'),
        ("enforcement", f'{registry_base} ФССП исполнительное производство задолженность'),
        ("news", f'{base} новости{suffix}'),
        ("review", f'{base} отзывы клиентов{suffix}'),
        ("social", f'{base}{suffix} site:vk.com OR site:t.me OR site:ok.ru'),
        ("tender", f'{registry_base} тендер OR закупка OR контракт'),
        ("patent", f'{registry_base} патент OR изобретение'),
        ("other", f'{base} продукция услуги клиенты партнеры поставщики{suffix}'),
    ]


async def collect_external_sources(
    company_name: str,
    official_url: str | None,
    region: str | None = None,
    max_sources: int = 60,
    anchors: IdentityAnchors | None = None,
) -> tuple[list[IntelligenceSource], list[str], SearchDiagnostics]:
    notes: list[str] = []
    plan = query_plan(company_name, region, anchors)
    per_query = max(2, min(5, max_sources // max(1, len(plan)) + 1))
    semaphore = asyncio.Semaphore(6)
    mission_id = f"company-{uuid4()}"
    correlation_id = f"corr-{uuid4()}"
    gateway = get_search_gateway()
    policy = search_policy_from_env()

    async def run(kind: SourceKind, query: str):
        async with semaphore:
            response = await gateway.search(
                SearchRequest(
                    query=query,
                    limit=per_query,
                    mission_id=mission_id,
                    correlation_id=correlation_id,
                ),
                policy,
            )
            return kind, query, response

    batches = await asyncio.gather(*(run(kind, query) for kind, query in plan))
    raw: list[dict] = []
    diagnostics: list[SearchDiagnostics] = []
    for kind, query, response in batches:
        diagnostics.append(response.diagnostics)
        if response.diagnostics.state != "success":
            notes.append(
                f"Поиск для типа {kind} выполнен в состоянии {response.diagnostics.state}."
            )
        for item in response.results:
            copy = item.as_legacy_dict()
            copy["_query_kind"] = kind
            copy["_query"] = query
            raw.append(copy)

    official_host = _host(official_url or "") or None
    accessed_at = datetime.now(timezone.utc).isoformat()
    seen: set[str] = set()
    sources: list[IntelligenceSource] = []
    for item in raw:
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = str(item.get("title") or _host(url) or url)[:300]
        snippet = str(item.get("content") or item.get("snippet") or "")[:900]
        query_kind: SourceKind = item.get("_query_kind") or "unknown"
        source_class = classify_source_domain(url, official_host)
        result_kind = classify_result(title, snippet)
        sources.append(IntelligenceSource(
            id=f"H{len(sources) + 1}", title=title, url=url, snippet=snippet,
            accessed_at=accessed_at, query_kind=query_kind, result_kind=result_kind,
            source_class=source_class,
            classification_state=classification_state(source_class, result_kind),
            lifecycle_state="discovery_hint",
            evidence_level="unverified_mention",
            verification_note="Поисковый сниппет; первичный документ не загружен и не проверен.",
        ))
        if len(sources) >= max_sources:
            break
    return sources, notes, SearchDiagnostics.aggregate(diagnostics)


def to_llm_sources(sources: list[IntelligenceSource]) -> list[dict]:
    return [{
        "id": source.id, "title": source.title, "url": source.url,
        "snippet": (
            source.evidence_quote
            if source.lifecycle_state == "evidence"
            else source.snippet
        ),
        "accessed_at": source.accessed_at, "query_kind": source.query_kind,
        "result_kind": source.result_kind, "source_class": source.source_class,
        "classification_state": source.classification_state,
        "lifecycle_state": source.lifecycle_state,
        "verification_note": source.verification_note,
        "source_type": source_type(source.source_class), "evidence_level": source.evidence_level,
        "document_url": source.document_url,
        "document_title": source.document_title,
        "document_accessed_at": source.document_accessed_at,
        "document_digest": source.document_digest,
        "evidence_quote": source.evidence_quote,
        "evidence_locator": source.evidence_locator,
        "evidence_digest": source.evidence_digest,
        "fetch_path": source.fetch_path,
    } for source in sources]


async def run_enriched_site_analysis(url: str, title: str, text: str) -> SiteAnalysis:
    # Lazy import keeps the shared source models independent of orchestration.
    from app.verified_analysis import run_verified_enriched_site_analysis
    return await run_verified_enriched_site_analysis(url, title, text)
