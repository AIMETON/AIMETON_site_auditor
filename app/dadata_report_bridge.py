from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
import re


from app.entity_resolution.dadata import (
    DaDataLookupResult,
    RegistryMirrorState,
    get_dadata_registry_mirror_provider,
)
from app.models import CompanyFact


DADATA_NOTE_PREFIX = "DaData registry mirror"


async def enrich_identity_with_dadata(
    anchors: Any,
) -> tuple[Any, DaDataLookupResult | None, list[CompanyFact], list[str]]:
    """Resolve extracted INN/OGRN against DaData without upgrading authority trust.

    DaData is deliberately treated as a registry mirror. It can corroborate and
    normalize entity identity, but it can never close the FNS authority gate.
    The synchronous provider is moved off the async mission worker thread.
    """
    query = getattr(anchors, "inn", None) or getattr(anchors, "ogrn", None)
    if not query:
        return anchors, None, [], [
            f"{DADATA_NOTE_PREFIX}: not_attempted — INN/OGRN не извлечён из first-party evidence."
        ]

    provider = get_dadata_registry_mirror_provider()
    try:
        result = await asyncio.to_thread(provider.lookup, str(query))
    except RuntimeError:
        return anchors, None, [], [
            f"{DADATA_NOTE_PREFIX}: unavailable — provider lookup failed; authority gate ФНС остаётся открытым."
        ]

    notes = [
        f"{DADATA_NOTE_PREFIX}: state={result.state.value}; records={len(result.records)}; "
        f"cache_hit={str(result.cache_hit).lower()}; authority_verified=false."
    ]
    facts: list[CompanyFact] = []
    for record in result.records[:10]:
        fact_note = (
            f"{DADATA_NOTE_PREFIX}; state={result.state.value}; "
            f"authority_verified=false; response_digest={record.response_digest}"
        )
        for field, value in (
            ("legal_name", record.legal_name),
            ("inn", record.inn),
            ("ogrn", record.ogrn),
            ("registration_status", record.status),
        ):
            if value:
                facts.append(
                    CompanyFact(
                        field=field,
                        value=str(value),
                        confidence="Средняя",
                        source_ids=[],
                        note=fact_note,
                    )
                )

    if result.state is RegistryMirrorState.VERIFIED and len(result.records) == 1:
        record = result.records[0]
        anchors = replace(
            anchors,
            legal_name=record.legal_name or getattr(anchors, "legal_name", None),
            inn=record.inn or getattr(anchors, "inn", None),
            ogrn=record.ogrn or getattr(anchors, "ogrn", None),
        )
        notes.append(
            f"{DADATA_NOTE_PREFIX}: идентификаторы нормализованы для дальнейшего discovery; "
            "официальная верификация ФНС всё ещё обязательна."
        )
    elif result.state is RegistryMirrorState.CONFLICTING:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: получены конфликтующие/множественные записи; "
            "identity не повышена до resolved без authority evidence ФНС."
        )
    elif result.state is RegistryMirrorState.UNRESOLVED:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: запись по извлечённому идентификатору не разрешена; "
            "исходные first-party anchors сохранены."
        )

    return anchors, result, facts, notes


_GENERIC_NAME_TOKENS = {
    "ооо", "ао", "пао", "ип", "зао", "оао", "компания", "центр", "клиника",
    "медицинский", "медицинская", "стоматология", "стоматологическая",
}


def _identity_name_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    tokens = {
        token.casefold()
        for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]{3,}", value)
    }
    return {token for token in tokens if token not in _GENERIC_NAME_TOKENS}


def _candidate_match_score(
    record: DaDataPartyRecord,
    *,
    query: str,
    anchors: Any,
    company_hint: str,
    target_scoped: bool,
) -> int:
    score = 0
    if query in {
        str(getattr(anchors, "inn", "") or ""),
        str(getattr(anchors, "ogrn", "") or ""),
    }:
        score += 8
    if target_scoped:
        score += 4

    target_tokens = _identity_name_tokens(getattr(anchors, "legal_name", None))
    target_tokens |= _identity_name_tokens(company_hint)
    record_tokens = _identity_name_tokens(record.legal_name)
    record_tokens |= _identity_name_tokens(record.short_name)
    overlap = target_tokens & record_tokens
    if overlap:
        score += 3 + min(3, len(overlap))
    return score


async def enrich_identifier_candidates_with_dadata(
    anchors: Any,
    candidates: list[tuple[str, str, bool]],
    *,
    company_hint: str,
) -> tuple[Any, DaDataLookupResult | None, list[CompanyFact], list[str], int]:
    """Check every checksum-valid first-party identifier and resolve one target candidate.

    DaData is used to enrich all candidates, including competing entities. A candidate
    is promoted to the target identity only when it wins unambiguously by target
    context/name evidence. Merely being present on the audited domain is insufficient.
    """
    unique: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    for scheme, value, target_scoped in candidates:
        key = (scheme, value)
        if not value or key in seen:
            continue
        seen.add(key)
        unique.append((scheme, value, target_scoped))

    if not unique:
        return anchors, None, [], [
            f"{DADATA_NOTE_PREFIX}: not_attempted — checksum-valid INN/OGRN candidates отсутствуют."
        ], 0

    provider = get_dadata_registry_mirror_provider()
    notes: list[str] = []
    resolved: list[tuple[int, str, DaDataLookupResult, DaDataPartyRecord]] = []
    checked = 0
    for scheme, value, target_scoped in unique:
        checked += 1
        try:
            result = await asyncio.to_thread(provider.lookup, value)
        except RuntimeError:
            notes.append(
                f"{DADATA_NOTE_PREFIX}: candidate={scheme}:{value}; unavailable — lookup failed."
            )
            continue
        notes.append(
            f"{DADATA_NOTE_PREFIX}: candidate={scheme}:{value}; state={result.state.value}; "
            f"records={len(result.records)}; target_scoped={str(target_scoped).lower()}; "
            "authority_verified=false."
        )
        if result.state is not RegistryMirrorState.VERIFIED or len(result.records) != 1:
            continue
        record = result.records[0]
        score = _candidate_match_score(
            record,
            query=value,
            anchors=anchors,
            company_hint=company_hint,
            target_scoped=target_scoped,
        )
        resolved.append((score, value, result, record))

    if not resolved:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: checked={checked}; target candidate не разрешён."
        )
        return anchors, None, [], notes, checked

    resolved.sort(key=lambda item: (-item[0], item[1]))
    best_score, _, best_result, best_record = resolved[0]
    runner_up = resolved[1][0] if len(resolved) > 1 else -1
    if best_score < 4 or best_score == runner_up:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: checked={checked}; candidate ownership ambiguous "
            f"(best_score={best_score}, runner_up={runner_up}); identity не повышена."
        )
        return anchors, None, [], notes, checked

    updated = replace(
        anchors,
        legal_name=best_record.legal_name or getattr(anchors, "legal_name", None),
        inn=best_record.inn or getattr(anchors, "inn", None),
        ogrn=best_record.ogrn or getattr(anchors, "ogrn", None),
    )
    fact_note = (
        f"{DADATA_NOTE_PREFIX}; multi_candidate_resolution=true; "
        f"authority_verified=false; response_digest={best_record.response_digest}"
    )
    facts: list[CompanyFact] = []
    for field, value in (
        ("legal_name", best_record.legal_name),
        ("inn", best_record.inn),
        ("ogrn", best_record.ogrn),
        ("registration_status", best_record.status),
    ):
        if value:
            facts.append(
                CompanyFact(
                    field=field,
                    value=str(value),
                    confidence="Средняя",
                    source_ids=[],
                    note=fact_note,
                )
            )
    notes.append(
        f"{DADATA_NOTE_PREFIX}: checked={checked}; target candidate selected "
        f"with score={best_score}; authority gate ФНС остаётся открытым."
    )
    return updated, best_result, facts, notes, checked
