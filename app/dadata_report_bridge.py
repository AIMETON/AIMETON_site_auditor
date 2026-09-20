from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
import re


from app.entity_resolution.dadata import (
    DaDataLookupResult,
    DaDataPartyRecord,
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


def _identity_name_compact(value: str | None) -> str:
    """Normalize brand/legal-name spacing and punctuation without fuzzy substringing."""
    tokens = sorted(
        _identity_name_tokens(value),
        key=lambda token: (
            re.search(re.escape(token), str(value or ""), re.IGNORECASE).start()
            if re.search(re.escape(token), str(value or ""), re.IGNORECASE)
            else 10_000
        ),
    )
    compact = "".join(tokens)
    return compact if len(compact) >= 6 else ""


def _identity_name_compacts(value: str | None) -> set[str]:
    """Return exact compact forms for meaningful title/name segments."""
    raw = str(value or "").strip()
    if not raw:
        return set()
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:\||—|–|\s-\s)\s*", raw)
        if part.strip()
    ]
    compacts = {_identity_name_compact(part) for part in parts}
    compacts.discard("")
    return compacts


def _candidate_match_score(
    record: DaDataPartyRecord,
    *,
    query: str,
    anchors: Any,
    company_hint: str,
    target_scoped: bool,
) -> int:
    score = 0
    # Candidate identifiers extracted from the same site are hypotheses, not
    # pre-trusted identity. Do not let an earlier first-match extractor make
    # itself the winner merely by being present in anchors.
    if target_scoped:
        score += 2

    target_tokens = _identity_name_tokens(getattr(anchors, "legal_name", None))
    target_tokens |= _identity_name_tokens(company_hint)
    record_tokens = _identity_name_tokens(record.legal_name)
    record_tokens |= _identity_name_tokens(record.short_name)
    overlap = target_tokens & record_tokens
    if overlap:
        score += 3 + min(3, len(overlap))

    target_compacts = _identity_name_compacts(getattr(anchors, "legal_name", None))
    target_compacts |= _identity_name_compacts(company_hint)
    record_compacts = _identity_name_compacts(record.legal_name)
    record_compacts |= _identity_name_compacts(record.short_name)
    if target_compacts & record_compacts:
        score += 6
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
    merged_candidates: dict[tuple[str, str], bool] = {}
    for scheme, value, target_scoped in candidates:
        if not value:
            continue
        key = (scheme, value)
        merged_candidates[key] = merged_candidates.get(key, False) or target_scoped
    unique = [
        (scheme, value, target_scoped)
        for (scheme, value), target_scoped in merged_candidates.items()
    ]

    if not unique:
        return anchors, None, [], [
            f"{DADATA_NOTE_PREFIX}: not_attempted — checksum-valid INN/OGRN candidates отсутствуют."
        ], 0

    provider = get_dadata_registry_mirror_provider()
    notes: list[str] = []
    resolved: list[tuple[int, str, DaDataLookupResult, DaDataPartyRecord]] = []
    observed_results: list[DaDataLookupResult] = []
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
        observed_results.append(result)
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
        if not observed_results:
            return anchors, None, [], notes, checked
        states = {item.state for item in observed_results}
        if RegistryMirrorState.CONFLICTING in states:
            state = RegistryMirrorState.CONFLICTING
        elif states == {RegistryMirrorState.UNAVAILABLE}:
            state = RegistryMirrorState.UNAVAILABLE
        else:
            state = RegistryMirrorState.UNRESOLVED
        aggregate = DaDataLookupResult(
            state=state,
            query="multi_identifier_candidates",
            records=[record for item in observed_results for record in item.records],
            conflicts=(
                ["multi_identifier_candidates_unresolved"]
                if state is RegistryMirrorState.CONFLICTING
                else []
            ),
            authority_verified=False,
        )
        return anchors, aggregate, [], notes, checked

    # INN and OGRN lookups for the same legal entity must reinforce each other,
    # not compete as two different candidates.
    by_entity: dict[tuple[str, str, str], tuple[int, str, DaDataLookupResult, DaDataPartyRecord]] = {}
    for item in resolved:
        score, query, result, record = item
        entity_key = (
            str(record.inn or ""),
            str(record.ogrn or ""),
            str(record.legal_name or "").casefold(),
        )
        current = by_entity.get(entity_key)
        if current is None or score > current[0]:
            by_entity[entity_key] = item

    resolved_entities = sorted(by_entity.values(), key=lambda item: (-item[0], item[1]))
    best_score, _, best_result, best_record = resolved_entities[0]
    runner_up = resolved_entities[1][0] if len(resolved_entities) > 1 else -1
    if best_score < 4 or best_score == runner_up:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: checked={checked}; candidate ownership ambiguous "
            f"(best_score={best_score}, runner_up={runner_up}); identity не повышена."
        )
        ambiguous_state = (
            RegistryMirrorState.CONFLICTING
            if best_score == runner_up
            else RegistryMirrorState.UNRESOLVED
        )
        aggregate = DaDataLookupResult(
            state=ambiguous_state,
            query="multi_identifier_candidates",
            records=[item[3] for item in resolved_entities],
            conflicts=(
                ["ambiguous_target_ownership"]
                if ambiguous_state is RegistryMirrorState.CONFLICTING
                else []
            ),
            authority_verified=False,
        )
        return anchors, aggregate, [], notes, checked

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
