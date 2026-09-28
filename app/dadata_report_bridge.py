from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
import re

from pydantic import BaseModel, ConfigDict
from app.fast_research_model import request_fast_json

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
    for record in result.records:
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


class IdentityCandidateChoice(BaseModel):
    """Closed-list semantic identity decision made by the fast research model."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str | None = None
    reason: str = ""


def _identity_region_matches(record: DaDataPartyRecord, region: str | None) -> bool:
    """Require explicit region agreement when a first-party region is known."""
    region_value = str(region or "").strip()
    if not region_value:
        return True
    address = str(record.address or "").strip()
    if not address:
        return False
    normalize = lambda value: re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "", value).casefold()
    region_key = normalize(region_value)
    address_key = normalize(address)
    return bool(region_key and region_key in address_key)


async def _choose_identity_candidate(
    candidates: list[tuple[str, DaDataPartyRecord, bool]],
    *,
    anchors: Any,
    company_hint: str,
    request_json=None,
) -> tuple[DaDataPartyRecord | None, str]:
    """Choose only from observed candidates; ambiguity or model failure is fail-closed."""
    if not candidates:
        return None, "no_candidates"

    rows = [
        {
            "candidate_id": candidate_id,
            "legal_name": record.legal_name,
            "short_name": record.short_name,
            "address": record.address,
            "inn": record.inn,
            "ogrn": record.ogrn,
            "target_scoped_first_party_identifier": target_scoped,
        }
        for candidate_id, record, target_scoped in candidates
    ]
    allowed = {candidate_id: record for candidate_id, record, _ in candidates}
    request = request_json or request_fast_json
    try:
        decision = await request(
            "identity_candidate_selection",
            IdentityCandidateChoice,
            system=(
                "Ты Identity Candidate Resolver. Выбирай юридическое лицо только из "
                "переданного закрытого списка. Сопоставляй смысл названия/бренда, регион, "
                "адрес и first-party контекст. target_scoped_first_party_identifier — "
                "сильный сигнал, но не автоматическое доказательство. Если несколько "
                "кандидатов правдоподобны, имя слишком общее или данных недостаточно — "
                "верни candidate_id=null. Не придумывай новую организацию."
            ),
            prompt=(
                "TARGET CONTEXT:\n"
                + str({
                    "company_hint": company_hint,
                    "first_party_legal_name": getattr(anchors, "legal_name", None),
                    "first_party_region": getattr(anchors, "primary_region", None),
                    "domain": getattr(anchors, "domain", None),
                })
                + "\nCANDIDATES:\n"
                + str(rows)
                + "\nВыбери candidate_id только при однозначном смысловом соответствии."
            ),
            max_tokens=900,
            timeout_seconds=15,
        )
    except Exception:
        return None, "semantic_selector_unavailable"

    selected = str(decision.candidate_id or "").strip()
    if not selected:
        return None, decision.reason or "semantic_selector_ambiguous"
    record = allowed.get(selected)
    if record is None:
        return None, "semantic_selector_invalid_candidate"
    return record, decision.reason or "semantic_selector_selected"


async def discover_identity_candidate_with_dadata(
    anchors: Any,
    *,
    company_hint: str,
    request_json=None,
) -> tuple[Any, DaDataLookupResult | None, list[CompanyFact], list[str], int]:
    """Discover a preliminary legal-entity candidate by name, then re-check its identifier.

    Name search is candidate discovery only. Promotion requires an unambiguous
    semantic closed-list selection and a second exact findById lookup. DaData remains a non-authoritative
    registry mirror; official FNS verification stays mandatory.
    """
    hint = " ".join(str(company_hint or "").split()).strip()
    if not hint:
        return anchors, None, [], [
            f"{DADATA_NOTE_PREFIX}: name_discovery_not_attempted — company name is empty."
        ], 0

    region = str(getattr(anchors, "primary_region", None) or "").strip()
    query = hint
    if region and region.casefold() not in query.casefold():
        query = f"{query} {region}"

    provider = get_dadata_registry_mirror_provider()
    try:
        suggested = await asyncio.to_thread(provider.suggest, query)
    except RuntimeError:
        return anchors, None, [], [
            f"{DADATA_NOTE_PREFIX}: name_discovery_unavailable — suggestion lookup failed."
        ], 0

    checked = len(suggested.records)
    notes = [
        f"{DADATA_NOTE_PREFIX}: name_discovery records={checked}; "
        f"cache_hit={str(suggested.cache_hit).lower()}; authority_verified=false."
    ]
    if suggested.state is RegistryMirrorState.UNAVAILABLE or not suggested.records:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: name_discovery did not yield a usable candidate."
        )
        return anchors, suggested, [], notes, checked

    region_matched_records = [
        record
        for record in suggested.records
        if _identity_region_matches(record, region)
    ]
    if region and not region_matched_records:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: name candidates do not match first-party region; "
            "identity not promoted."
        )
        return anchors, DaDataLookupResult(
            state=RegistryMirrorState.UNRESOLVED,
            query=query,
            records=suggested.records,
            authority_verified=False,
        ), [], notes, checked

    semantic_candidates = [
        (f"C{index}", record, False)
        for index, record in enumerate(region_matched_records or suggested.records)
    ]
    best_record, selection_reason = await _choose_identity_candidate(
        semantic_candidates,
        anchors=anchors,
        company_hint=hint,
        request_json=request_json,
    )
    if best_record is None:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: name candidate ownership unresolved by semantic selector "
            f"({selection_reason}); identity not promoted."
        )
        state = (
            RegistryMirrorState.CONFLICTING
            if len(semantic_candidates) > 1
            else RegistryMirrorState.UNRESOLVED
        )
        return anchors, DaDataLookupResult(
            state=state,
            query=query,
            records=[record for _, record, _ in semantic_candidates],
            conflicts=(
                ["ambiguous_name_candidates"]
                if state is RegistryMirrorState.CONFLICTING
                else []
            ),
            authority_verified=False,
        ), [], notes, checked

    identifier = best_record.inn or best_record.ogrn
    if not identifier:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: selected name candidate has no INN/OGRN; identity not promoted."
        )
        return anchors, suggested, [], notes, checked

    try:
        exact = await asyncio.to_thread(provider.lookup, str(identifier))
    except RuntimeError:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: exact lookup after name discovery failed."
        )
        return anchors, None, [], notes, checked

    if exact.state is not RegistryMirrorState.VERIFIED or len(exact.records) != 1:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: exact lookup after name discovery is not uniquely verified; "
            "identity not promoted."
        )
        return anchors, exact, [], notes, checked

    exact_record = exact.records[0]
    if region and not _identity_region_matches(exact_record, region):
        notes.append(
            f"{DADATA_NOTE_PREFIX}: exact record does not match first-party region; "
            "identity not promoted."
        )
        return anchors, DaDataLookupResult(
            state=RegistryMirrorState.UNRESOLVED,
            query=str(identifier),
            records=exact.records,
            authority_verified=False,
        ), [], notes, checked

    updated = replace(
        anchors,
        legal_name=exact_record.legal_name or getattr(anchors, "legal_name", None),
        inn=exact_record.inn or getattr(anchors, "inn", None),
        ogrn=exact_record.ogrn or getattr(anchors, "ogrn", None),
    )
    fact_note = (
        f"{DADATA_NOTE_PREFIX}; name_candidate_discovery=true; exact_identifier_recheck=true; "
        f"authority_verified=false; response_digest={exact_record.response_digest}"
    )
    facts: list[CompanyFact] = []
    for field, value in (
        ("legal_name", exact_record.legal_name),
        ("inn", exact_record.inn),
        ("ogrn", exact_record.ogrn),
        ("registration_status", exact_record.status),
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
        f"{DADATA_NOTE_PREFIX}: semantic name candidate re-checked by exact identifier; "
        "authority gate ФНС remains open."
    )
    return updated, exact, facts, notes, checked


async def enrich_identifier_candidates_with_dadata(
    anchors: Any,
    candidates: list[tuple[str, str, bool]],
    *,
    company_hint: str,
    request_json=None,
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
    resolved: list[tuple[str, bool, DaDataLookupResult, DaDataPartyRecord]] = []
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
        resolved.append((value, target_scoped, result, record))

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

    # INN and OGRN lookups for the same legal entity reinforce one observed entity.
    by_entity: dict[
        tuple[str, str, str],
        tuple[DaDataLookupResult, DaDataPartyRecord, bool],
    ] = {}
    for query, target_scoped, result, record in resolved:
        entity_key = (
            str(record.inn or ""),
            str(record.ogrn or ""),
            str(record.legal_name or "").casefold(),
        )
        current = by_entity.get(entity_key)
        if current is None:
            by_entity[entity_key] = (result, record, target_scoped)
        elif target_scoped and not current[2]:
            by_entity[entity_key] = (result, record, True)

    resolved_entities = list(by_entity.values())
    semantic_candidates = [
        (f"C{index}", record, target_scoped)
        for index, (_, record, target_scoped) in enumerate(resolved_entities)
    ]
    best_record, selection_reason = await _choose_identity_candidate(
        semantic_candidates,
        anchors=anchors,
        company_hint=company_hint,
        request_json=request_json,
    )
    if best_record is None:
        notes.append(
            f"{DADATA_NOTE_PREFIX}: checked={checked}; candidate ownership unresolved "
            f"by semantic selector ({selection_reason}); identity не повышена."
        )
        state = (
            RegistryMirrorState.CONFLICTING
            if len(semantic_candidates) > 1
            else RegistryMirrorState.UNRESOLVED
        )
        aggregate = DaDataLookupResult(
            state=state,
            query="multi_identifier_candidates",
            records=[record for _, record, _ in semantic_candidates],
            conflicts=(
                ["ambiguous_target_ownership"]
                if state is RegistryMirrorState.CONFLICTING
                else []
            ),
            authority_verified=False,
        )
        return anchors, aggregate, [], notes, checked

    best_result = next(
        result
        for result, record, _ in resolved_entities
        if record is best_record
    )

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
        "by closed-list semantic resolver; authority gate ФНС остаётся открытым."
    )
    return updated, best_result, facts, notes, checked
