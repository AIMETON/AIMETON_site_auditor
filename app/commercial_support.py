from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.models import CommercialOpportunity, CompanyFact, EconomicSignal, EvidenceSource


_TOKEN = re.compile(r"[0-9]+|[A-Za-zА-Яа-яЁё]{4,}")
_CHILD_ID = re.compile(r"^(?P<parent>.+)-b\d+-\d+$")
_STOPWORDS = {
    "aimeton", "анализ", "аудит", "бизнес", "возможность", "возможности",
    "гипотеза", "данные", "компания", "компании", "может", "нужно", "проблема",
    "процесс", "процесса", "решение", "система", "системы", "работа", "работы",
    "использование", "позволяет", "повысить", "снизить", "улучшить",
    "analysis", "audit", "business", "company", "data", "opportunity", "problem",
    "process", "solution", "system", "work", "could", "would", "using",
}


@dataclass(frozen=True)
class CommercialSupportAssessment:
    state: str
    cited_source_ids: tuple[str, ...]
    direct_support_source_ids: tuple[str, ...]
    matched_terms: tuple[str, ...]

    @property
    def direct_support_count(self) -> int:
        return len(self.direct_support_source_ids)

    def safe_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "cited_source_ids": list(self.cited_source_ids),
            "direct_support_source_ids": list(self.direct_support_source_ids),
            "matched_terms": list(self.matched_terms),
            "direct_support_count": self.direct_support_count,
        }


def _parent_id(source_id: str) -> str:
    match = _CHILD_ID.match(str(source_id))
    return match.group("parent") if match else str(source_id)


def _stem(token: str) -> str:
    value = token.casefold().replace("ё", "е")
    if value.isdigit():
        return value
    return value[:6] if len(value) >= 7 else value


def _terms(value: str) -> set[str]:
    result: set[str] = set()
    for raw in _TOKEN.findall(str(value or "")):
        folded = raw.casefold().replace("ё", "е")
        if folded in _STOPWORDS:
            continue
        result.add(_stem(folded))
    return result


def _source_text(source: EvidenceSource) -> str:
    parts = [source.title, source.evidence_quote]
    parts.extend(block.evidence_quote for block in source.evidence_blocks)
    return " ".join(str(item or "") for item in parts)


def assess_commercial_support(
    opportunity: CommercialOpportunity,
    *,
    facts: Iterable[CompanyFact] = (),
    signals: Iterable[EconomicSignal] = (),
    sources: Iterable[EvidenceSource] = (),
) -> CommercialSupportAssessment:
    """Check that cited evidence semantically supports the stated problem hypothesis.

    The gate is deterministic and deliberately conservative. Direct source quotes/titles
    are stronger than model-derived facts/signals. Derived records may make a claim weakly
    supported, but only direct evidence can authorize an 80+ commercial score.
    """
    claim_terms = _terms(opportunity.problem_hypothesis)
    source_by_id = {_parent_id(source.id): source for source in sources}
    cited = tuple(dict.fromkeys(
        _parent_id(source_id)
        for source_id in opportunity.source_ids
        if _parent_id(source_id) in source_by_id
    ))
    if not cited or not claim_terms:
        return CommercialSupportAssessment(
            state="unsupported",
            cited_source_ids=cited,
            direct_support_source_ids=(),
            matched_terms=(),
        )

    direct_sources: list[str] = []
    direct_matches: set[str] = set()
    for source_id in cited:
        overlap = claim_terms & _terms(_source_text(source_by_id[source_id]))
        numeric_overlap = {item for item in overlap if item.isdigit()}
        if len(overlap) >= 2 or numeric_overlap or (len(claim_terms) <= 2 and overlap):
            direct_sources.append(source_id)
            direct_matches.update(overlap)
        elif overlap:
            direct_matches.update(overlap)

    if direct_sources:
        state = "supported"
        matches = direct_matches
    else:
        derived_terms: set[str] = set()
        cited_set = set(cited)
        for fact in facts:
            if cited_set.intersection(_parent_id(item) for item in fact.source_ids):
                derived_terms.update(_terms(f"{fact.field} {fact.value} {fact.note}"))
        for signal in signals:
            if cited_set.intersection(_parent_id(item) for item in signal.source_ids):
                derived_terms.update(
                    _terms(f"{signal.signal} {signal.evidence} {signal.business_effect}")
                )
        derived_overlap = claim_terms & derived_terms
        matches = direct_matches | derived_overlap
        state = "weak" if matches else "unsupported"

    return CommercialSupportAssessment(
        state=state,
        cited_source_ids=cited,
        direct_support_source_ids=tuple(direct_sources),
        matched_terms=tuple(sorted(matches)),
    )


def enforce_commercial_support(
    opportunity: CommercialOpportunity,
    assessment: CommercialSupportAssessment,
) -> CommercialOpportunity:
    """Enforce the existing contract that 80+ requires direct problem evidence."""
    if opportunity.score < 80 or assessment.state == "supported":
        return opportunity
    qualification = opportunity.qualification
    if qualification == "Приоритетная":
        qualification = "Перспективная"
    return opportunity.model_copy(update={"score": 79, "qualification": qualification})
