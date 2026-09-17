from __future__ import annotations

from app.research_execution import active_settings

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.document_pipeline import get_document_pipeline
from app.document_preflight import screen_document
from app.document_pipeline.models import FetchPolicy
from app.evidence_triage import triage_document_blocks
from app.models import IntelligenceSource
from app.research_control import current_research, deep_research_enabled
from app.search_gateway.gateway import canonical_url
from app.sef.models import DiscoveryHint, Source, SourceKind


VERIFICATION_PRIORITY = {
    "official": 110,
    "registry": 100,
    "finance": 95,
    "court": 90,
    "arbitration": 90,
    "enforcement": 90,
    "contact": 85,
    "workforce": 80,
    "jobs": 75,
    "news": 70,
    "tender": 70,
    "review": 55,
    "social": 50,
    "unknown": 20,
    "other": 20,
}

EVIDENCE_LEVEL_BY_CLASS = {
    "official": "confirmed_fact",
    "registry": "corroborated_signal",
    "finance": "corroborated_signal",
    "court": "corroborated_signal",
    "arbitration": "corroborated_signal",
    "enforcement": "corroborated_signal",
    "news": "corroborated_signal",
    "tender": "corroborated_signal",
    "patent": "corroborated_signal",
    "workforce": "weak_signal",
    "contact": "weak_signal",
    "review": "weak_signal",
    "social": "weak_signal",
    "jobs": "weak_signal",
    "ownership": "weak_signal",
    "affiliation": "weak_signal",
}

_RELATED_MARKERS = (
    "похожие компании",
    "похожие организации",
    "одноименные компании",
    "одноимённые компании",
    "одноименные организации",
    "одноимённые организации",
    "связанные организации",
    "другие компании",
    "другие организации",
    "рекомендуем также",
    "смотрите также",
)
_RELATED_LOCATORS = ("aside", "sidebar", "related", "recommend", "similar")


def _identifier(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _fold(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _source_kind(source_class: str) -> SourceKind:
    if source_class == "official":
        return SourceKind.FIRST_PARTY
    if source_class in {
        "registry", "finance", "court", "arbitration", "enforcement",
        "tender", "patent",
    }:
        return SourceKind.OFFICIAL_REGISTRY
    if source_class == "news":
        return SourceKind.NEWS_MEDIA
    if source_class in {"social", "review"}:
        return SourceKind.SOCIAL
    return SourceKind.INDUSTRY_CATALOG


def _anchor_values(anchors: Any) -> tuple[list[str], list[str], list[str]]:
    strong_text = [
        str(value).strip()
        for value in (getattr(anchors, "inn", None), getattr(anchors, "ogrn", None))
        if value
    ]
    phone_digits = [
        _digits(str(value))
        for value in getattr(anchors, "phones", ())
        if _digits(str(value))
    ]
    cities = [
        str(value).strip().casefold()
        for value in getattr(anchors, "cities", ())
        if str(value).strip()
    ]
    return strong_text, phone_digits, cities


def _entity_names(company_name: str, anchors: Any) -> list[str]:
    values = [getattr(anchors, "legal_name", None), company_name]
    result: list[str] = []
    for value in values:
        normalized = _fold(str(value or "").strip(" .,-—|"))
        if len(normalized) < 3 or normalized in result:
            continue
        result.append(normalized)
    return result


def _is_related_context(text: str, locator: str = "") -> bool:
    folded = _fold(text)
    locator_folded = _fold(locator)
    return (
        any(marker in folded for marker in _RELATED_MARKERS)
        or any(marker in locator_folded for marker in _RELATED_LOCATORS)
    )


def _strong_anchor_in_primary_context(
    value: str,
    *,
    blocks: list[Any],
    entity_names: list[str],
    document_title: str,
) -> bool:
    pattern = re.compile(r"(?<!\d)" + re.escape(value) + r"(?!\d)")
    title_folded = _fold(document_title)
    title_matches_entity = any(name in title_folded for name in entity_names)
    for index, block in enumerate(blocks):
        block_text = str(getattr(block, "text", "") or "")
        if not pattern.search(block_text):
            continue
        locator = str(getattr(block, "locator", "") or "")
        start, end = max(0, index - 1), min(len(blocks), index + 2)
        window = " ".join(str(getattr(item, "text", "") or "") for item in blocks[start:end])
        if _is_related_context(window, locator):
            continue
        window_folded = _fold(window)
        if title_matches_entity or any(name in window_folded for name in entity_names):
            return True
        # A compact primary-content block with one strong target id is acceptable
        # unless it looks like a related/sidebar listing. This preserves registry
        # cards where company name and requisites are rendered in separate blocks.
        locator_folded = _fold(locator)
        if not any(token in locator_folded for token in ("footer", "aside", "sidebar")) and len(window) <= 4_000:
            strong_ids = re.findall(r"(?<!\d)(?:\d{10}|\d{12}|\d{13}|\d{15})(?!\d)", window)
            if strong_ids.count(value) == 1 and len(set(strong_ids)) <= 2:
                return True
    return False


def document_matches_entity(
    text: str,
    *,
    company_name: str,
    anchors: Any,
    document_url: str,
    blocks: list[Any] | None = None,
    document_title: str = "",
) -> tuple[bool, str]:
    """Require primary-entity evidence before promoting a fetched document.

    A target INN/OGRN appearing in a sidebar, related-company list, footer or other
    mentioned-entity context is not sufficient. First-party documents on the resolved
    official domain remain trusted. Third-party strong identifiers require primary
    document context; name+region remains only a weaker fallback.
    """
    normalized = _fold(text)
    official_domain = str(getattr(anchors, "domain", "") or "").lower()
    if official_domain:
        host = _host(document_url)
        if host == official_domain or host.endswith(f".{official_domain}"):
            return True, "official_domain_match"

    strong_text, phone_digits, cities = _anchor_values(anchors)
    entity_names = _entity_names(company_name, anchors)
    block_list = list(blocks or [])
    if block_list:
        for value in strong_text:
            if _strong_anchor_in_primary_context(
                value,
                blocks=block_list,
                entity_names=entity_names,
                document_title=document_title,
            ):
                return True, "registration_identifier_primary_context_match"
    else:
        for value in strong_text:
            for match in re.finditer(r"(?<!\d)" + re.escape(value) + r"(?!\d)", text):
                context = text[max(0, match.start() - 600): match.end() + 600]
                if _is_related_context(context):
                    continue
                if any(name in _fold(context) for name in entity_names):
                    return True, "registration_identifier_context_match"

    # Phone numbers are useful corroboration, but a number anywhere on a directory
    # page no longer proves that the whole document's primary entity is the target.
    if block_list:
        for index, block in enumerate(block_list):
            block_text = str(getattr(block, "text", "") or "")
            locator = str(getattr(block, "locator", "") or "")
            if _is_related_context(block_text, locator):
                continue
            digits = _digits(block_text)
            if not any(len(phone) >= 10 and phone[-10:] in digits for phone in phone_digits):
                continue
            start, end = max(0, index - 1), min(len(block_list), index + 2)
            window = " ".join(str(getattr(item, "text", "") or "") for item in block_list[start:end])
            if any(name in _fold(window) for name in entity_names):
                return True, "phone_and_entity_context_match"

    # Weak fallback. Prefer resolved legal_name when present; a long SEO title is not
    # treated as a canonical company name simply because it mentions a city.
    legal_name = _fold(getattr(anchors, "legal_name", None))
    fallback_names = [legal_name] if legal_name else [name for name in entity_names if len(name) <= 100]
    if fallback_names and any(name in normalized for name in fallback_names) and any(city in normalized for city in cities):
        return True, "name_and_region_match"

    return False, "identity_not_confirmed"


def _best_quote_block(fetched, *, company_name: str, anchors: Any):
    strong_text, phone_digits, cities = _anchor_values(anchors)
    entity_names = _entity_names(company_name, anchors)

    def score(block) -> tuple[int, int]:
        text = str(block.text).casefold()
        digits = _digits(block.text)
        locator = str(getattr(block, "locator", "") or "")
        value = 0
        if any(item.casefold() in text for item in strong_text):
            value += 100
        if any(phone[-10:] in digits for phone in phone_digits if len(phone) >= 10):
            value += 80
        if any(name in text for name in entity_names):
            value += 40
        if any(city in text for city in cities):
            value += 30
        if locator == "head/title":
            value -= 50
        if _is_related_context(str(block.text), locator):
            value -= 250
        if any(marker in _fold(locator) for marker in ("footer", "aside", "sidebar")):
            value -= 100
        return value, min(len(block.text), 2_000)

    candidates = [block for block in fetched.blocks if len(block.text.strip()) >= 20]
    if not candidates:
        return fetched.blocks[0]
    return max(candidates, key=score)


async def verify_external_sources(
    sources: list[IntelligenceSource],
    *,
    company_name: str,
    anchors: Any,
    max_documents: int | None = 24,
    preserve_blocks: bool = False,
    include_official: bool = False,
    timeout_seconds: float | None = 60,
) -> list[IntelligenceSource]:
    """Fetch, identity-check and triage discovery hints before exposing evidence."""
    official_domain = str(getattr(anchors, "domain", "") or "").lower()
    candidates = sorted(
        (
            source
            for source in sources
            if source.lifecycle_state == "discovery_hint"
            and not (
                not include_official
                and official_domain
                and (
                    _host(str(source.url)) == official_domain
                    or _host(str(source.url)).endswith(f".{official_domain}")
                )
            )
        ),
        key=lambda source: (
            -VERIFICATION_PRIORITY.get(source.source_class, 10),
            source.id,
        ),
    )[:max_documents]

    robots = None
    if deep_research_enabled() and official_domain:
        from app.evidence_crawler.factory import get_evidence_crawler
        from app.evidence_crawler.models import BootstrapCrawlPolicy
        official_url = next((item.url for item in sources if _host(item.url) == official_domain), f"https://{official_domain}/")
        robots = await get_evidence_crawler()._load_robots(
            f"{urlparse(official_url).scheme}://{official_domain}/", BootstrapCrawlPolicy(),
        )
    pending = list(candidates)
    seen_urls = {canonical_url(str(item.url)) for item in sources}
    pipeline = get_document_pipeline()
    verified: list[IntelligenceSource] = []
    mission_id = _identifier("mission_external_verify", company_name)
    correlation_id = _identifier("corr_external_verify", company_name)

    async def verify_one(source_item):
        url = str(source_item.url)
        host = _host(url)
        if not host:
            return
        if robots is not None and host == official_domain and not robots.allows(url):
            source_item.verification_note = "Обход официальной страницы не разрешён robots policy."
            return
        source_item.lifecycle_state = "source_candidate"
        source_item.verification_note = "Первичный документ запрошен; поисковый сниппет не используется как evidence."

        sef_source = Source(
            id=_identifier("source", url),
            mission_id=mission_id,
            correlation_id=correlation_id,
            kind=_source_kind(source_item.source_class),
            publisher=source_item.title[:500] or host,
            homepage_url=url,
        )
        hint = DiscoveryHint(
            id=_identifier("hint", url),
            mission_id=mission_id,
            provider_call_id=_identifier("provider_call", url),
            correlation_id=correlation_id,
            url=url,
            title=source_item.title[:1000] or host,
            snippet=(source_item.snippet.strip() or "Discovery candidate; snippet is not evidence.")[:4000],
            discovered_at=datetime.now(timezone.utc),
        )
        try:
            fetched = await pipeline.fetch_hint(
                hint,
                sef_source,
                FetchPolicy(
                    allowed_hosts=frozenset({host}),
                    timeout_seconds=25,
                    max_bytes=2_000_000,
                    allow_crawl4ai=True,
                    allow_browser=True,
                ),
            )
        except Exception as exc:
            source_item.verification_note = (
                f"Первичный документ не загружен ({type(exc).__name__}); источник остаётся кандидатом."
            )
            return

        matches, match_reason = document_matches_entity(
            fetched.normalized_text,
            company_name=company_name,
            anchors=anchors,
            document_url=str(fetched.document.url),
            blocks=list(getattr(fetched, "blocks", [])),
            document_title=str(getattr(fetched.document, "title", "") or ""),
        )
        if not matches:
            source_item.verification_note = (
                "Первичный документ загружен, но primary identity конкретной компании не подтверждена; "
                "источник не повышен до evidence."
            )
            return

        if deep_research_enabled() and official_domain and _host(str(fetched.document.url)) == official_domain:
            for link in getattr(fetched, "links", []):
                link_url = canonical_url(str(link.url))
                if (_host(link_url) != official_domain or link_url in seen_urls
                        or re.search(r"\.(?:png|jpe?g|gif|webp|svg|css|js|zip|mp4|mp3)$", urlparse(link_url).path, re.I)):
                    continue
                seen_urls.add(link_url)
                discovered = IntelligenceSource(
                    id=_identifier("D", link_url), title=link.text or link_url, url=link_url,
                    accessed_at=datetime.now(timezone.utc).isoformat(), query_kind="official",
                    source_class="official", classification_state="classified",
                    verification_note="Ссылка из загруженного официального документа; ожидает проверки.",
                )
                sources.append(discovered)
                pending.append(discovered)

        screening = await screen_document(fetched, company_name=company_name, anchors=anchors)
        source_item.preflight_decision = screening.decision
        source_item.preflight_reason = screening.reason
        control = current_research()
        if control:
            control.checkpoint(f"preflight/{source_item.id}", {
                **screening.model_dump(), "url": url, "digest": fetched.normalized_content_digest,
            })
        if screening.decision == "exclude":
            source_item.verification_note = f"Исключён из полного анализа после двух проходов: {screening.reason}"
            return
        if control and control.stop_requested:
            source_item.verification_note = "Остановлен после предварительной классификации."
            return

        block = _best_quote_block(fetched, company_name=company_name, anchors=anchors)
        quote = block.text.strip()[:800]
        promoted = pipeline.promote_quote(
            fetched,
            locator=block.locator,
            quote=quote,
        )
        source_item.lifecycle_state = "evidence"
        source_item.document_url = str(fetched.document.url)
        source_item.document_title = fetched.document.title
        source_item.document_accessed_at = fetched.document.accessed_at.isoformat()
        source_item.document_digest = fetched.normalized_content_digest
        source_item.evidence_quote = promoted.evidence.quote
        source_item.evidence_locator = promoted.evidence.locator
        source_item.evidence_digest = promoted.evidence.digest
        source_item.fetch_path = fetched.diagnostics.path.value
        source_item.evidence_level = EVIDENCE_LEVEL_BY_CLASS.get(
            source_item.source_class,
            "weak_signal",
        )
        source_item.verification_note = (
            f"Первичный документ загружен; primary identity подтверждена ({match_reason}); "
            "цитата закреплена locator+digest."
        )
        verified.append(source_item)
        if preserve_blocks:
            source_is_official = bool(
                official_domain
                and (
                    _host(str(fetched.document.url)) == official_domain
                    or _host(str(fetched.document.url)).endswith(f".{official_domain}")
                )
            )
            triage = await triage_document_blocks(
                list(fetched.blocks),
                company_name=company_name,
                anchors=anchors,
                document_url=str(fetched.document.url),
                document_title=str(getattr(fetched.document, "title", "") or ""),
                source_query_kind=source_item.query_kind,
                source_is_official=source_is_official,
            )
            if control:
                control.checkpoint(f"triage/{source_item.id}", {
                    "url": url,
                    "blocks_total": len(triage.decisions),
                    "blocks_kept": len(triage.kept),
                    "model_used": triage.model_used,
                    "model_unavailable": triage.model_unavailable,
                })
            for decision in triage.kept:
                block_index = int(decision.block_id[1:])
                evidence_block = fetched.blocks[block_index]
                for offset in range(0, len(evidence_block.text), 4000):
                    fragment = evidence_block.text[offset:offset + 4000]
                    if not fragment.strip():
                        continue
                    promotion = pipeline.promote_quote(
                        fetched, locator=evidence_block.locator, quote=fragment,
                    )
                    record = source_item.model_copy(deep=True)
                    record.id = f"{source_item.id}-b{block_index}-{offset}"
                    record.query_kind = decision.query_kind
                    record.evidence_quote = promotion.evidence.quote
                    record.evidence_locator = promotion.evidence.locator
                    record.evidence_digest = promotion.evidence.digest
                    record.verification_note = (
                        source_item.verification_note
                        + f" Evidence triage: {decision.entity_relation}/{decision.query_kind}; {decision.reason}."
                    )
                    verified.append(record)

    semaphore = asyncio.Semaphore(4)

    async def bounded(source_item):
        async with semaphore:
            if current_research() and current_research().stop_requested:
                return
            try:
                await verify_one(source_item)
            except Exception as exc:
                source_item.verification_note = (
                    f"Проверка документа не завершена ({type(exc).__name__}); "
                    "полнота evidence не подтверждена."
                )

    try:
        if deep_research_enabled():
            index = 0
            while index < len(pending):
                if current_research().stop_requested:
                    break
                batch = pending[index:index + 4]
                index += len(batch)
                await asyncio.gather(*(bounded(item) for item in batch))
                current_research().documents_attempted += len(batch)
                current_research().frontier_size = len(pending) - index
                current_research().checkpoint("acquisition", {
                    "sources": [item.model_dump(mode="json") for item in sources],
                    "evidence": [item.model_dump(mode="json") for item in verified],
                    "documents_attempted": index, "frontier_size": len(pending),
                })
        else:
            await asyncio.wait_for(
                asyncio.gather(*(bounded(item) for item in candidates)),
                timeout=None if active_settings() else timeout_seconds,
            )
    except TimeoutError:
        for item in candidates:
            if item.lifecycle_state != "evidence":
                item.verification_note = "Лимит времени проверки; документ не проверен."
    # Stable output order regardless of network completion order.
    verified.sort(key=lambda item: item.id)
    known_ids = {item.id for item in sources}
    sources.extend(item for item in verified if item.id not in known_ids)

    return verified
