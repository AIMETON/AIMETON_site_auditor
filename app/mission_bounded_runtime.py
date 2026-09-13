from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.evidence_crawler.factory import get_evidence_crawler
from app.evidence_crawler.models import BootstrapCrawlPolicy, CrawlStatus
from app.evidence_crawler.search_discovery import discover_same_domain_urls
from app.verified_analysis import run_verified_enriched_site_analysis as run_enriched_site_analysis
from app.mission_contract import Mission, MissionState, utc_now
from app.mission_orchestrator import (
    ActionCandidate,
    ActionType,
    EntryPoint,
    PolicySnapshot,
    default_site_mission_request,
    get_mission_orchestrator,
)
from app.scraper import FetchError, fetch_site, normalize_url

MIN_EVIDENCE_CHARS = 1_500
MAX_EVIDENCE_CHARS = 120_000
MAX_EVIDENCE_PAGE_CHARS = 24_000
PRIMARY_CRAWL_PAGES = 8
MAX_TARGETED_PAGES = 6
MAX_SEARCH_DISCOVERED_PAGES = 4

HIGH_VALUE_TERMS: dict[str, tuple[str, ...]] = {
    "workforce": (
        "doctor",
        "doctors",
        "staff",
        "team",
        "employee",
        "personnel",
        "physician",
        "врач",
        "врачи",
        "специалист",
        "специалисты",
        "команда",
        "сотрудник",
        "персонал",
    ),
    "editorial": (
        "article",
        "articles",
        "blog",
        "news",
        "journal",
        "publication",
        "materials",
        "статья",
        "статьи",
        "новости",
        "блог",
        "публикац",
        "заметки",
    ),
    "documents": (
        "document",
        "documents",
        "license",
        "licenses",
        "документ",
        "лиценз",
        "реквизит",
    ),
    "prices": (
        "price",
        "prices",
        "pricing",
        "cost",
        "стоимост",
        "цена",
        "цены",
        "прайс",
    ),
}
TARGET_CATEGORY_QUOTAS = {
    "workforce": 2,
    "editorial": 2,
    "documents": 1,
    "prices": 1,
}


class BoundedRuntimeRepository(Protocol):
    def append_record(
        self,
        mission_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        digest: str | None = None,
        record_id: str | None = None,
    ) -> str: ...

    def update_state_for_owner(
        self,
        owner_id: int,
        mission_id: str,
        state: MissionState,
    ) -> Mission | None: ...


def _turn(
    repository: BoundedRuntimeRepository,
    mission_id: str,
    *,
    summary: str,
    status: str,
    source_count: int = 0,
    reason_code: str | None = None,
    next_action: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "turn_id": f"{summary}:{mission_id}",
        "status": status,
        "summary": summary,
        "source_count": source_count,
    }
    if status in {"completed", "blocked", "degraded"}:
        payload["completed_at"] = utc_now().isoformat()
    if reason_code:
        payload["reason_code"] = reason_code
    if next_action:
        payload["next_action"] = next_action
    repository.append_record(mission_id, "turn", payload)


def _preferred_target_url(raw: str) -> tuple[str, str | None]:
    """Return a preferred HTTPS target plus an optional HTTP fallback."""
    normalized = normalize_url(raw)
    parsed = urlsplit(normalized)
    if parsed.scheme != "http" or parsed.port not in {None, 80}:
        return normalized, None
    https_netloc = parsed.hostname or ""
    preferred = urlunsplit(("https", https_netloc, parsed.path, parsed.query, ""))
    return preferred, normalized


async def _fetch_preferred_target(raw: str) -> dict[str, str]:
    preferred, fallback = _preferred_target_url(raw)
    try:
        return await fetch_site(preferred)
    except FetchError:
        if fallback is None:
            raise
        return await fetch_site(fallback)


def _high_value_kind(url: str) -> str | None:
    parsed = urlsplit(url)
    haystack = f"{parsed.path} {parsed.query}".casefold()
    for kind, terms in HIGH_VALUE_TERMS.items():
        if any(term in haystack for term in terms):
            return kind
    return None


def _select_diverse_targets(
    urls: list[str],
    *,
    excluded: set[str] | None = None,
) -> list[str]:
    """Select high-value URLs without allowing one site section to monopolize budget."""
    excluded = excluded or set()
    by_kind: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for raw in urls:
        url = str(raw)
        if url in excluded or url in seen:
            continue
        seen.add(url)
        kind = _high_value_kind(url)
        if kind is not None:
            by_kind[kind].append(url)

    selected: list[str] = []
    for kind in ("workforce", "editorial", "documents", "prices"):
        quota = TARGET_CATEGORY_QUOTAS[kind]
        selected.extend(sorted(by_kind.get(kind, []))[:quota])
    return selected[:MAX_TARGETED_PAGES]


def _aggregate_evidence(pages: list[dict[str, str]]) -> str:
    # Fetch/crawl budgets bound acquisition. Never silently prefix-cut pages
    # that were already acquired; extraction owns explicit overflow reporting.
    chunks = []
    for page in pages:
        text = page.get("text") or ""
        if text.strip():
            chunks.append(f"SOURCE: {page.get('final_url', '')}\nTITLE: {page.get('title', '')}\n{text}")
    return "\n\n--- INTERNAL PAGE ---\n\n".join(chunks)


def _make_crawl_plan(orchestrator, target_url: str, *, analysis_id: str):
    snapshot = orchestrator.create_mission(
        default_site_mission_request(target_url, analysis_id=analysis_id),
        entry_point=EntryPoint.UI,
    )
    mission_id = snapshot.contract.mission_id
    host = (urlsplit(target_url).hostname or "").lower()
    if not host:
        raise ValueError("site_host_missing")
    plan = orchestrator.plan(
        mission_id,
        deficits=["bootstrap"],
        candidates=[
            ActionCandidate(
                action_type=ActionType.CRAWL_URL,
                target=target_url,
                deficit_code="bootstrap",
                expected_sufficiency_gain=0.8,
                ai_priority=1.0,
            )
        ],
        policy=PolicySnapshot(
            allowed_hosts=frozenset({host}),
            remaining_actions=20,
        ),
    )
    return mission_id, plan


async def _run_crawl(
    target_url: str,
    *,
    analysis_id: str,
    max_pages: int,
    max_depth: int,
    max_duration_seconds: float,
):
    orchestrator = get_mission_orchestrator()
    mission_id, plan = _make_crawl_plan(
        orchestrator,
        target_url,
        analysis_id=analysis_id,
    )
    return await get_evidence_crawler().run_mission(
        orchestrator,
        mission_id,
        plan=plan,
        policy=BootstrapCrawlPolicy(
            max_pages=max_pages,
            max_depth=max_depth,
            max_duration_seconds=max_duration_seconds,
            max_links_per_page=120,
            allow_browser=True,
        ),
    )


async def _append_verified_crawl_page(
    pages: list[dict[str, str]],
    seen: set[str],
    target_url: str,
    *,
    analysis_id: str,
) -> None:
    """Add a candidate only after it passes the owned Evidence Crawler path."""
    if target_url in seen:
        return
    try:
        targeted = await _run_crawl(
            target_url,
            analysis_id=analysis_id,
            max_pages=1,
            max_depth=0,
            max_duration_seconds=15,
        )
    except (FetchError, httpx.HTTPError, ValueError):
        return
    if targeted.status is CrawlStatus.BLOCKED or not targeted.pages:
        return
    page_url = str(targeted.pages[0].final_url)
    if page_url in seen:
        return
    try:
        fetched = await _fetch_preferred_target(page_url)
    except (FetchError, httpx.HTTPError):
        return
    seen.add(page_url)
    pages.append(fetched)


async def _collect_deep_site_evidence(
    target_ref: str,
    *,
    owned_mission_id: str,
) -> tuple[dict[str, str], int, str]:
    """Collect diverse bounded same-origin evidence before analytical generation."""
    seed = await _fetch_preferred_target(target_ref)
    primary = await _run_crawl(
        seed["final_url"],
        analysis_id=f"owned-{owned_mission_id}-primary",
        max_pages=PRIMARY_CRAWL_PAGES,
        max_depth=3,
        max_duration_seconds=50,
    )
    if primary.status is CrawlStatus.BLOCKED or not primary.pages:
        raise ValueError("deep_crawl_blocked")

    pages: list[dict[str, str]] = [seed]
    seen = {seed["final_url"]}
    for crawled_page in primary.pages:
        page_url = str(crawled_page.final_url)
        if page_url in seen:
            continue
        seen.add(page_url)
        try:
            pages.append(await _fetch_preferred_target(page_url))
        except (FetchError, httpx.HTTPError):
            continue

    domain_hint = (urlsplit(seed["final_url"]).hostname or "").lower().removeprefix("www.")
    discovery = await discover_same_domain_urls(
        seed["final_url"],
        company_name=domain_hint or None,
        mission_id=owned_mission_id,
        correlation_id=f"owned-search-{owned_mission_id}",
        max_urls=MAX_SEARCH_DISCOVERED_PAGES,
    )
    for index, target_url in enumerate(discovery.urls[:MAX_SEARCH_DISCOVERED_PAGES]):
        await _append_verified_crawl_page(
            pages,
            seen,
            str(target_url),
            analysis_id=f"owned-{owned_mission_id}-search-{index}",
        )

    target_candidates = _select_diverse_targets(
        [str(url) for url in primary.discovered_urls],
        excluded=seen,
    )
    for index, target_url in enumerate(target_candidates):
        await _append_verified_crawl_page(
            pages,
            seen,
            target_url,
            analysis_id=f"owned-{owned_mission_id}-target-{index}",
        )

    evidence_text = _aggregate_evidence(pages)
    if len(evidence_text) < MIN_EVIDENCE_CHARS:
        raise ValueError("insufficient_site_evidence")
    return seed, len(pages), evidence_text


async def run_owned_site_analysis(
    repository: BoundedRuntimeRepository,
    *,
    owner_id: int,
    mission: Mission,
) -> None:
    """Execute one bounded owner-scoped site-analysis mission."""
    if mission.owner_id != owner_id or mission.state is not MissionState.RUNNING:
        return

    try:
        _turn(repository, mission.id, summary="planning_started", status="running")
        _turn(repository, mission.id, summary="site_fetch_started", status="running")
        _turn(repository, mission.id, summary="deep_crawl_started", status="running")
        seed, internal_page_count, evidence_text = await _collect_deep_site_evidence(
            mission.target_ref,
            owned_mission_id=mission.id,
        )
        _turn(
            repository,
            mission.id,
            summary="site_fetch_completed",
            status="running",
            source_count=internal_page_count,
        )
        _turn(
            repository,
            mission.id,
            summary="deep_crawl_completed",
            status="running",
            source_count=internal_page_count,
        )

        result = await run_enriched_site_analysis(
            seed["final_url"],
            seed["title"],
            evidence_text,
        )
        report_payload = result.model_copy(
            update={"mission_id": mission.id}
        ).model_dump(mode="json")
        repository.append_record(mission.id, "report_payload", report_payload)
        repository.append_record(
            mission.id,
            "report_metadata",
            {
                "report_id": f"report:{mission.id}",
                "status": "completed",
                "format": "json",
                "content_type": "application/json",
                "available": True,
                "release_level": "preliminary",
                "blocked_reason": None,
                "created_at": utc_now().isoformat(),
            },
        )
        _turn(
            repository,
            mission.id,
            summary="analysis_completed",
            status="completed",
            source_count=len(result.sources),
        )
        repository.update_state_for_owner(owner_id, mission.id, MissionState.COMPLETED)
    except (FetchError, httpx.HTTPError, ValueError):
        _turn(
            repository,
            mission.id,
            summary="analysis_failed",
            status="blocked",
            reason_code="site_evidence_insufficient",
            next_action="inspect_collection_trace",
        )
        repository.update_state_for_owner(owner_id, mission.id, MissionState.BLOCKED)
    except Exception:
        _turn(
            repository,
            mission.id,
            summary="analysis_failed",
            status="blocked",
            reason_code="bounded_runtime_failed",
            next_action="inspect_runtime_trace",
        )
        repository.update_state_for_owner(owner_id, mission.id, MissionState.BLOCKED)
