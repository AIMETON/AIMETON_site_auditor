from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.models import CommercialOpportunity, CompanyFact, EconomicSignal, EvidenceSource


_TOKEN = re.compile(r"[0-9]+|[A-Za-zА-Яа-яЁё]{4,}")
_CHILD_ID = re.compile(r"^(?P<parent>.+)-b\d+-\d+$")
_METRIC_CLAIM = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)(?:\s*[-–—]\s*(\d+(?:[.,]\d+)?))?\s*"
    r"(%|процент\w*|мин(?:ут\w*)?|час\w*|дн(?:я|ей|и)?|руб(?:\.|лей)?|₽)",
    re.IGNORECASE,
)
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
    unsupported_expected_value_metrics: tuple[str, ...] = ()

    @property
    def direct_support_count(self) -> int:
        return len(self.direct_support_source_ids)

    def safe_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "cited_source_ids": list(self.cited_source_ids),
            "direct_support_source_ids": list(self.direct_support_source_ids),
            "matched_terms": list(self.matched_terms),
            "unsupported_expected_value_metrics": list(self.unsupported_expected_value_metrics),
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


def _inferred_cited_source_ids(
    opportunity: CommercialOpportunity,
    *,
    facts: Iterable[CompanyFact],
    signals: Iterable[EconomicSignal],
    source_by_id: dict[str, EvidenceSource],
) -> tuple[str, ...]:
    """Recover omitted citations only from already-sourced records supporting the claim.

    This is deliberately narrower than semantic search: a fact/signal must share at
    least two meaningful claim terms (or a numeric term), and every recovered id must
    already resolve to an evidence source. Explicit model citations are never replaced.
    """
    claim_terms = _terms(opportunity.problem_hypothesis)
    if not claim_terms:
        return ()

    recovered: list[str] = []

    def consider(text: str, source_ids: Iterable[str]) -> None:
        overlap = claim_terms & _terms(text)
        numeric_overlap = {item for item in overlap if item.isdigit()}
        if len(overlap) < 2 and not numeric_overlap and not (len(claim_terms) <= 2 and overlap):
            return
        for raw_source_id in source_ids:
            source_id = _parent_id(str(raw_source_id))
            if source_id in source_by_id and source_id not in recovered:
                recovered.append(source_id)

    for fact in facts:
        consider(f"{fact.field} {fact.value} {fact.note}", fact.source_ids)
    for signal in signals:
        consider(
            f"{signal.signal} {signal.evidence} {signal.business_effect}",
            signal.source_ids,
        )
    return tuple(recovered)


def _metric_claim_keys(value: str) -> set[str]:
    result: set[str] = set()
    for match in _METRIC_CLAIM.finditer(str(value or "")):
        first, second, raw_unit = match.groups()
        numbers = [first.replace(",", ".")]
        if second:
            numbers.append(second.replace(",", "."))
        unit = raw_unit.casefold()
        if unit == "%" or unit.startswith("процент"):
            normalized_unit = "%"
        elif unit.startswith("мин"):
            normalized_unit = "minute"
        elif unit.startswith("час"):
            normalized_unit = "hour"
        elif unit.startswith("дн"):
            normalized_unit = "day"
        else:
            normalized_unit = "rub"
        result.add(f"{'-'.join(numbers)}:{normalized_unit}")
    return result


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
    if not opportunity.source_ids:
        cited = _inferred_cited_source_ids(
            opportunity,
            facts=facts,
            signals=signals,
            source_by_id=source_by_id,
        )
    expected_value_metrics = _metric_claim_keys(opportunity.expected_value)
    direct_metric_keys: set[str] = set()
    for source_id in cited:
        direct_metric_keys.update(_metric_claim_keys(_source_text(source_by_id[source_id])))
    unsupported_expected_value_metrics = tuple(sorted(expected_value_metrics - direct_metric_keys))
    if not cited or not claim_terms:
        return CommercialSupportAssessment(
            state="unsupported",
            cited_source_ids=cited,
            direct_support_source_ids=(),
            matched_terms=(),
            unsupported_expected_value_metrics=unsupported_expected_value_metrics,
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
        unsupported_expected_value_metrics=unsupported_expected_value_metrics,
    )


def enforce_commercial_support(
    opportunity: CommercialOpportunity,
    assessment: CommercialSupportAssessment,
) -> CommercialOpportunity:
    """Enforce direct support for priority and reject invented quantitative KPI claims."""
    updates: dict[str, object] = {}
    if not opportunity.source_ids and assessment.cited_source_ids:
        updates["source_ids"] = list(assessment.cited_source_ids)
    if opportunity.score >= 80 and assessment.state != "supported":
        qualification = opportunity.qualification
        if qualification == "Приоритетная":
            qualification = "Перспективная"
        updates.update({"score": 79, "qualification": qualification})
    if assessment.unsupported_expected_value_metrics:
        updates["expected_value"] = (
            "Потенциальный эффект требует проверки на пилоте; "
            "количественные KPI без прямого подтверждения не заявляются."
        )
    return opportunity.model_copy(update=updates) if updates else opportunity
