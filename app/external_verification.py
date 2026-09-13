from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.document_pipeline import get_document_pipeline
from app.document_pipeline.models import FetchPolicy
from app.models import IntelligenceSource
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


def _identifier(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


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


def document_matches_entity(
    text: str,
    *,
    company_name: str,
    anchors: Any,
    document_url: str,
) -> tuple[bool, str]:
    """Require document-level identity evidence before promoting a search hint.

    Strong registration identifiers or phone numbers are sufficient. Otherwise
    the document must mention both the company name and a known city. First-party
    documents on the already resolved official domain are accepted directly.
    """
    normalized = " ".join(text.split()).casefold()
    official_domain = str(getattr(anchors, "domain", "") or "").lower()
    if official_domain:
        host = _host(document_url)
        if host == official_domain or host.endswith(f".{official_domain}"):
            return True, "official_domain_match"

    strong_text, phone_digits, cities = _anchor_values(anchors)
    for value in strong_text:
        if re.search(r"(?<!\d)" + re.escape(value) + r"(?!\d)", normalized):
            return True, "registration_identifier_match"

    document_digits = _digits(text)
    for phone in phone_digits:
        if len(phone) >= 10 and phone[-10:] in document_digits:
            return True, "phone_match"

    company = " ".join(company_name.split()).casefold()
    if company and company in normalized and any(city in normalized for city in cities):
        return True, "name_and_region_match"

    return False, "identity_not_confirmed"


def _best_quote_block(fetched, *, company_name: str, anchors: Any):
    strong_text, phone_digits, cities = _anchor_values(anchors)
    company = " ".join(company_name.split()).casefold()

    def score(block) -> tuple[int, int]:
        text = block.text.casefold()
        digits = _digits(block.text)
        value = 0
        if any(item.casefold() in text for item in strong_text):
            value += 100
        if any(phone[-10:] in digits for phone in phone_digits if len(phone) >= 10):
            value += 80
        if company and company in text:
            value += 40
        if any(city in text for city in cities):
            value += 30
        if block.locator == "head/title":
            value -= 50
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
    max_documents: int = 24,
    preserve_blocks: bool = False,
    include_official: bool = False,
    timeout_seconds: float = 60,
) -> list[IntelligenceSource]:
    """Fetch and verify high-value discovery hints before exposing them as evidence.

    Search snippets never satisfy verification. A source is promoted only after
    its primary document is fetched and the fetched body confirms the resolved
    entity using official-domain, registration-id, phone, or name+region anchors.
    """
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

    pipeline = get_document_pipeline()
    verified: list[IntelligenceSource] = []
    mission_id = _identifier("mission_external_verify", company_name)
    correlation_id = _identifier("corr_external_verify", company_name)

    async def verify_one(source_item):
        url = str(source_item.url)
        host = _host(url)
        if not host:
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
        )
        if not matches:
            source_item.verification_note = (
                "Первичный документ загружен, но identity конкретной компании не подтверждена; "
                "источник не повышен до evidence."
            )
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
            f"Первичный документ загружен; identity подтверждена ({match_reason}); "
            "цитата закреплена locator+digest."
        )
        verified.append(source_item)
        if preserve_blocks:
            # The identity quote is an anchor, not a summary of the document.
            # Preserve every fetched block as independently locatable evidence.
            for block_index, evidence_block in enumerate(fetched.blocks):
                for offset in range(0, len(evidence_block.text), 4000):
                    fragment = evidence_block.text[offset:offset + 4000]
                    if not fragment.strip():
                        continue
                    promotion = pipeline.promote_quote(
                        fetched, locator=evidence_block.locator, quote=fragment,
                    )
                    record = source_item.model_copy(deep=True)
                    record.id = f"{source_item.id}-b{block_index}-{offset}"
                    record.evidence_quote = promotion.evidence.quote
                    record.evidence_locator = promotion.evidence.locator
                    record.evidence_digest = promotion.evidence.digest
                    verified.append(record)

    semaphore = asyncio.Semaphore(4)

    async def bounded(source_item):
        async with semaphore:
            try:
                await verify_one(source_item)
            except Exception as exc:
                source_item.verification_note = (
                    f"Проверка документа не завершена ({type(exc).__name__}); "
                    "полнота evidence не подтверждена."
                )

    try:
        await asyncio.wait_for(
            asyncio.gather(*(bounded(item) for item in candidates)),
            timeout=timeout_seconds,
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
