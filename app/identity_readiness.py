from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.models import CompanyFact, EvidenceSource


_AUTHORITY_RANK = {
    "unverified_mention": 0,
    "weak_signal": 1,
    "corroborated_signal": 2,
    "confirmed_fact": 3,
}
_STRONG_IDENTITY_FIELDS = ("inn", "ogrn")
_NAME_FIELDS = ("legal_name", "brand_name")


@dataclass(frozen=True)
class IdentityReadinessAssessment:
    state: str
    authoritative_identifiers: dict[str, tuple[str, ...]]
    sourced_names: tuple[str, ...]
    conflict_fields: tuple[str, ...]

    @property
    def unresolved_critical_conflicts(self) -> int:
        return len(self.conflict_fields)

    def safe_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "authoritative_identifiers": {
                field: list(values)
                for field, values in self.authoritative_identifiers.items()
            },
            "sourced_names": list(self.sourced_names),
            "conflict_fields": list(self.conflict_fields),
            "unresolved_critical_conflicts": self.unresolved_critical_conflicts,
        }


def _parent_source_id(source_id: str) -> str:
    match = re.match(r"^(?P<parent>.+)-b\d+-\d+$", source_id)
    return match.group("parent") if match else source_id


def _normalize_identity_value(field: str, value: str) -> str:
    compact = " ".join(str(value or "").split()).strip()
    if field in _STRONG_IDENTITY_FIELDS:
        return re.sub(r"\D", "", compact)
    return compact.casefold()


def source_authority_map(sources: Iterable[EvidenceSource]) -> dict[str, str]:
    authority: dict[str, str] = {"S1": "confirmed_fact"}
    for source in sources:
        source_id = _parent_source_id(str(source.id))
        level = str(source.evidence_level or "unverified_mention")
        if level not in _AUTHORITY_RANK:
            level = "unverified_mention"
        current = authority.get(source_id, "unverified_mention")
        if _AUTHORITY_RANK[level] > _AUTHORITY_RANK[current]:
            authority[source_id] = level
    return authority


def assess_identity_readiness(
    facts: Iterable[CompanyFact],
    *,
    sources: Iterable[EvidenceSource] = (),
    source_authority_by_id: dict[str, str] | None = None,
) -> IdentityReadinessAssessment:
    """Resolve identity from sourced facts without asking an LLM to arbitrate it.

    Strong registration identifiers only become authoritative when at least one
    supporting source is confirmed/corroborated. Weak sourced identity remains
    provisional. Multiple authoritative INN/OGRN values are a critical conflict.
    """
    authority = source_authority_map(sources)
    for source_id, level in (source_authority_by_id or {}).items():
        parent_id = _parent_source_id(str(source_id))
        if level not in _AUTHORITY_RANK:
            continue
        current = authority.get(parent_id, "unverified_mention")
        if _AUTHORITY_RANK[level] > _AUTHORITY_RANK[current]:
            authority[parent_id] = level

    sourced_names: list[str] = []
    all_sourced_strong: dict[str, set[str]] = {
        field: set() for field in _STRONG_IDENTITY_FIELDS
    }
    authoritative: dict[str, set[str]] = {
        field: set() for field in _STRONG_IDENTITY_FIELDS
    }

    for fact in facts:
        normalized = _normalize_identity_value(fact.field, fact.value)
        if not normalized or not fact.source_ids:
            continue
        source_levels = [
            _AUTHORITY_RANK.get(
                authority.get(_parent_source_id(str(source_id)), "unverified_mention"),
                0,
            )
            for source_id in fact.source_ids
        ]
        max_authority = max(source_levels, default=0)
        if fact.field in _NAME_FIELDS and max_authority >= 1:
            if normalized not in sourced_names:
                sourced_names.append(normalized)
        if fact.field in _STRONG_IDENTITY_FIELDS:
            all_sourced_strong[fact.field].add(normalized)
            if max_authority >= _AUTHORITY_RANK["corroborated_signal"]:
                authoritative[fact.field].add(normalized)

    conflicts = tuple(
        field for field in _STRONG_IDENTITY_FIELDS
        if len(authoritative[field]) > 1
    )
    if conflicts:
        state = "conflicting"
    elif sourced_names and any(len(authoritative[field]) == 1 for field in _STRONG_IDENTITY_FIELDS):
        state = "resolved"
    elif sourced_names or any(all_sourced_strong[field] for field in _STRONG_IDENTITY_FIELDS):
        state = "provisional"
    else:
        state = "unresolved"

    return IdentityReadinessAssessment(
        state=state,
        authoritative_identifiers={
            field: tuple(sorted(authoritative[field]))
            for field in _STRONG_IDENTITY_FIELDS
        },
        sourced_names=tuple(sourced_names),
        conflict_fields=conflicts,
    )


def identity_release_blocker(state: str) -> str | None:
    if state == "resolved":
        return None
    if state == "conflicting":
        return "identity_conflicting"
    if state == "provisional":
        return "identity_provisional"
    return "identity_unresolved"
