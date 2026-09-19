from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.identity_readiness import source_authority_map
from app.models import CompanyFact, EvidenceSource


_FINANCIAL_FIELDS = frozenset({"revenue", "profit", "assets", "taxes"})
_AUTHORITY_RANK = {
    "unverified_mention": 0,
    "weak_signal": 1,
    "corroborated_signal": 2,
    "confirmed_fact": 3,
}
_CHILD_ID = re.compile(r"^(?P<parent>.+)-b\d+-\d+$")
_NUMBER = re.compile(r"[-+]?\d[\d\s\u00a0]*(?:[.,]\d+)?")
_MULTIPLIERS = (
    (("млрд", "billion", "bn"), Decimal("1000000000")),
    (("млн", "million", "mln"), Decimal("1000000")),
    (("тыс", "thousand", "тысяч"), Decimal("1000")),
)


@dataclass(frozen=True)
class FinancialConflict:
    field: str
    period: str
    normalized_values: tuple[str, ...]
    source_ids: tuple[str, ...]

    def safe_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "period": self.period,
            "normalized_values": list(self.normalized_values),
            "source_ids": list(self.source_ids),
        }


@dataclass(frozen=True)
class FinancialConflictAssessment:
    conflicts: tuple[FinancialConflict, ...]

    @property
    def unresolved_critical_conflicts(self) -> int:
        return len(self.conflicts)

    @property
    def conflict_fields(self) -> tuple[str, ...]:
        return tuple(sorted({item.field for item in self.conflicts}))

    def safe_dict(self) -> dict[str, object]:
        return {
            "unresolved_critical_conflicts": self.unresolved_critical_conflicts,
            "conflict_fields": list(self.conflict_fields),
            "conflicts": [item.safe_dict() for item in self.conflicts],
        }


def _parent_id(source_id: str) -> str:
    match = _CHILD_ID.match(str(source_id))
    return match.group("parent") if match else str(source_id)


def _normalized_period(period: str | None) -> str:
    return " ".join(str(period or "").casefold().split()).strip()


def _currency(value: str) -> str:
    text = value.casefold()
    if "₽" in value or re.search(r"\b(?:руб(?:\.|лей|ля|ль)?|rub)\b", text):
        return "rub"
    if "$" in value or re.search(r"\b(?:usd|доллар)", text):
        return "usd"
    if "€" in value or re.search(r"\b(?:eur|евро)\b", text):
        return "eur"
    return "unknown"


def normalize_financial_value(value: str) -> tuple[Decimal, str] | None:
    text = " ".join(str(value or "").replace("\u00a0", " ").split()).strip()
    match = _NUMBER.search(text)
    if not match:
        return None
    raw = match.group(0).replace(" ", "")
    if raw.count(",") > 1 and "." not in raw:
        raw = raw.replace(",", "")
    elif raw.count(".") > 1 and "," not in raw:
        raw = raw.replace(".", "")
    else:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    folded = text.casefold()
    multiplier = Decimal("1")
    for markers, factor in _MULTIPLIERS:
        if any(marker in folded for marker in markers):
            multiplier = factor
            break
    return amount * multiplier, _currency(text)


def assess_financial_conflicts(
    facts: Iterable[CompanyFact],
    *,
    sources: Iterable[EvidenceSource] = (),
    source_authority_by_id: dict[str, str] | None = None,
) -> FinancialConflictAssessment:
    """Detect incompatible authoritative financial scalars for the same period.

    Only explicit-period, parseable financial values participate. This intentionally
    avoids declaring a conflict between values that may belong to different years or
    use incomparable currencies. At least corroborated provenance is required.
    """
    authority = source_authority_map(sources)
    for source_id, level in (source_authority_by_id or {}).items():
        if level not in _AUTHORITY_RANK:
            continue
        parent = _parent_id(source_id)
        if _AUTHORITY_RANK[level] > _AUTHORITY_RANK.get(authority.get(parent, "unverified_mention"), 0):
            authority[parent] = level

    grouped: dict[tuple[str, str], list[tuple[Decimal, str, tuple[str, ...]]]] = {}
    for fact in facts:
        if fact.field not in _FINANCIAL_FIELDS:
            continue
        period = _normalized_period(fact.period)
        parsed = normalize_financial_value(fact.value)
        if not period or parsed is None or not fact.source_ids:
            continue
        source_ids = tuple(dict.fromkeys(_parent_id(item) for item in fact.source_ids))
        max_authority = max(
            (_AUTHORITY_RANK.get(authority.get(item, "unverified_mention"), 0) for item in source_ids),
            default=0,
        )
        if max_authority < _AUTHORITY_RANK["corroborated_signal"]:
            continue
        grouped.setdefault((fact.field, period), []).append((*parsed, source_ids))

    conflicts: list[FinancialConflict] = []
    for (field, period), items in sorted(grouped.items()):
        known_currencies = {currency for _, currency, _ in items if currency != "unknown"}
        if len(known_currencies) > 1:
            # Cross-currency amounts are not directly comparable without an explicit
            # conversion contract; leave them outside deterministic conflict handling.
            continue
        values = {amount.normalize() for amount, _, _ in items}
        if len(values) < 2:
            continue
        source_ids = tuple(sorted({source_id for _, _, ids in items for source_id in ids}))
        conflicts.append(
            FinancialConflict(
                field=field,
                period=period,
                normalized_values=tuple(sorted(format(value, "f") for value in values)),
                source_ids=source_ids,
            )
        )
    return FinancialConflictAssessment(conflicts=tuple(conflicts))
