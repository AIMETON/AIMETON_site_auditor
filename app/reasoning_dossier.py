from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field

from app.models import CompanyFact, EconomicSignal


DEFAULT_FACT_LIMIT = 20
FIELD_LIMITS: dict[str, int] = {
    "legal_name": 8, "brand_name": 8, "inn": 8, "ogrn": 8,
    "registration_status": 8, "address": 20, "phones": 20, "emails": 20,
    "website": 12, "social_accounts": 16, "headcount": 16,
    "revenue": 24, "profit": 24, "assets": 24, "taxes": 24,
    "founders": 24, "executives": 32, "beneficial_owners": 24,
    "affiliates": 24, "geography": 24, "products": 40,
    "customers": 24, "suppliers": 24, "other": 20,
}
MAX_SIGNALS = 24
MAX_EVIDENCE_HIGHLIGHTS = 12
MAX_RISKS = 16
_CONFIDENCE = {"Высокая": 2, "Средняя": 1, "Низкая": 0}
_AUTHORITY = {
    "confirmed_fact": 3,
    "corroborated_signal": 2,
    "weak_signal": 1,
    "unverified_mention": 0,
}


class ReasoningDossier(BaseModel):
    """Bounded projection for expensive KM/commercial reasoning.

    The full extraction ledger and consolidated profile remain authoritative. Counts
    expose what was omitted from this reasoning projection, so bounded context is never
    confused with complete evidence coverage.
    """

    company_name: str
    business_summary: str
    facts_by_field: dict[str, list[CompanyFact]] = Field(default_factory=dict)
    economic_signals: list[EconomicSignal] = Field(default_factory=list)
    evidence_highlights: list[str] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)
    coverage: dict[str, int | bool] = Field(default_factory=dict)
    source_authority_by_id: dict[str, str] = Field(default_factory=dict)
    fact_counts_by_field: dict[str, int] = Field(default_factory=dict)
    omitted_fact_counts_by_field: dict[str, int] = Field(default_factory=dict)
    total_facts: int = 0
    selected_facts: int = 0
    total_signals: int = 0
    selected_signals: int = 0

    def safe_metrics(self) -> dict[str, int]:
        return {
            "total_facts": self.total_facts,
            "selected_facts": self.selected_facts,
            "omitted_facts": max(0, self.total_facts - self.selected_facts),
            "total_signals": self.total_signals,
            "selected_signals": self.selected_signals,
            "omitted_signals": max(0, self.total_signals - self.selected_signals),
        }


def _authority_score(source_ids: list[str], authority_by_id: dict[str, str]) -> tuple[int, int]:
    levels = [
        _AUTHORITY.get(authority_by_id.get(source_id, "unverified_mention"), 0)
        for source_id in source_ids
    ]
    return (max(levels, default=0), sum(level > 0 for level in levels))


def _ranked_facts(
    items: list[tuple[int, CompanyFact]],
    authority_by_id: dict[str, str],
) -> list[CompanyFact]:
    ranked = sorted(
        items,
        key=lambda pair: (
            -_authority_score(pair[1].source_ids, authority_by_id, group_by_id)[0],
            -_authority_score(pair[1].source_ids, authority_by_id, group_by_id)[1],
            -_CONFIDENCE.get(pair[1].confidence, 0),
            -int(bool(pair[1].period)),
            pair[0],
        ),
    )
    return [fact for _, fact in ranked]


def build_reasoning_dossier(
    profile: Any,
    *,
    source_authority_by_id: dict[str, str] | None = None,
) -> ReasoningDossier:
    source_authority_by_id = dict(source_authority_by_id or {})
    grouped: dict[str, list[tuple[int, CompanyFact]]] = defaultdict(list)
    counts = Counter()
    for index, fact in enumerate(profile.company_facts):
        grouped[fact.field].append((index, fact))
        counts[fact.field] += 1

    selected: dict[str, list[CompanyFact]] = {}
    omitted: dict[str, int] = {}
    for field in sorted(grouped):
        limit = FIELD_LIMITS.get(field, DEFAULT_FACT_LIMIT)
        ranked = _ranked_facts(grouped[field], source_authority_by_id, source_group_by_id)
        selected[field] = ranked[:limit]
        if len(ranked) > limit:
            omitted[field] = len(ranked) - limit

    signals = sorted(
        enumerate(profile.economic_signals),
        key=lambda pair: (
            -_authority_score(pair[1].source_ids, source_authority_by_id)[0],
            -_authority_score(pair[1].source_ids, source_authority_by_id)[1],
            -_CONFIDENCE.get(pair[1].confidence, 0),
            pair[0],
        ),
    )
    selected_signals = [signal for _, signal in signals[:MAX_SIGNALS]]

    evidence: list[str] = []
    seen_evidence: set[str] = set()
    for item in profile.evidence:
        normalized = " ".join(str(item).split()).strip()
        if not normalized or normalized.casefold() in seen_evidence:
            continue
        seen_evidence.add(normalized.casefold())
        evidence.append(normalized)
        if len(evidence) >= MAX_EVIDENCE_HIGHLIGHTS:
            break

    risks: list[str] = []
    seen_risks: set[str] = set()
    for item in profile.risks_and_assumptions:
        normalized = " ".join(str(item).split()).strip()
        if not normalized or normalized.casefold() in seen_risks:
            continue
        seen_risks.add(normalized.casefold())
        risks.append(normalized)
        if len(risks) >= MAX_RISKS:
            break

    return ReasoningDossier(
        company_name=profile.company_name,
        business_summary=profile.business_summary,
        facts_by_field=selected,
        economic_signals=selected_signals,
        evidence_highlights=evidence,
        risks_and_assumptions=risks,
        coverage=profile.coverage,
        source_authority_by_id={
            source_id: source_authority_by_id[source_id]
            for source_id in sorted({
                source_id
                for facts in selected.values()
                for fact in facts
                for source_id in fact.source_ids
            } | {
                source_id
                for signal in selected_signals
                for source_id in signal.source_ids
            })
            if source_id in source_authority_by_id
        },
        fact_counts_by_field=dict(counts),
        omitted_fact_counts_by_field=omitted,
        total_facts=len(profile.company_facts),
        selected_facts=sum(len(items) for items in selected.values()),
        total_signals=len(profile.economic_signals),
        selected_signals=len(selected_signals),
    )
