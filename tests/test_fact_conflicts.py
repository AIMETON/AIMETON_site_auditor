from app.fact_conflicts import assess_financial_conflicts, normalize_financial_value
from app.models import CompanyFact, EvidenceSource


def _source(identifier: str, level: str = "corroborated_signal") -> EvidenceSource:
    return EvidenceSource(
        id=identifier,
        title=identifier,
        url=f"https://registry.test/{identifier}",
        accessed_at="2026-09-19T00:00:00Z",
        evidence_quote="financial evidence",
        source_type="finance",
        evidence_level=level,
        document_url=f"https://registry.test/{identifier}",
        document_digest="sha256:" + identifier.lower()[0] * 64,
        evidence_digest="sha256:" + identifier.lower()[-1] * 64,
    )


def test_financial_amount_normalization_handles_russian_multipliers():
    assert normalize_financial_value("10 млн руб.") == normalize_financial_value("10 000 000 рублей")
    assert normalize_financial_value("1,5 млрд ₽")[0] == 1_500_000_000


def test_same_period_authoritative_values_conflict():
    facts = [
        CompanyFact(field="revenue", value="10 млн руб.", period="2025", source_ids=["A1"]),
        CompanyFact(field="revenue", value="12 000 000 руб.", period="2025", source_ids=["B1"]),
    ]

    assessment = assess_financial_conflicts(
        facts,
        sources=[_source("A1"), _source("B1")],
    )

    assert assessment.unresolved_critical_conflicts == 1
    assert assessment.conflict_fields == ("revenue",)
    assert assessment.conflicts[0].normalized_values == ("10000000", "12000000")


def test_equivalent_formatting_is_not_a_conflict():
    facts = [
        CompanyFact(field="profit", value="10 млн руб.", period="2025", source_ids=["A1"]),
        CompanyFact(field="profit", value="10 000 000 ₽", period="2025", source_ids=["B1"]),
    ]

    assessment = assess_financial_conflicts(
        facts,
        sources=[_source("A1"), _source("B1")],
    )

    assert assessment.unresolved_critical_conflicts == 0


def test_different_periods_do_not_conflict():
    facts = [
        CompanyFact(field="assets", value="10 млн руб.", period="2024", source_ids=["A1"]),
        CompanyFact(field="assets", value="12 млн руб.", period="2025", source_ids=["B1"]),
    ]

    assessment = assess_financial_conflicts(
        facts,
        sources=[_source("A1"), _source("B1")],
    )

    assert assessment.unresolved_critical_conflicts == 0


def test_weak_disagreement_does_not_create_critical_conflict():
    facts = [
        CompanyFact(field="taxes", value="1 млн руб.", period="2025", source_ids=["A1"]),
        CompanyFact(field="taxes", value="2 млн руб.", period="2025", source_ids=["B1"]),
    ]

    assessment = assess_financial_conflicts(
        facts,
        sources=[_source("A1", "weak_signal"), _source("B1", "weak_signal")],
    )

    assert assessment.unresolved_critical_conflicts == 0


def test_missing_period_is_not_assumed_to_be_same_reporting_period():
    facts = [
        CompanyFact(field="revenue", value="10 млн руб.", source_ids=["A1"]),
        CompanyFact(field="revenue", value="12 млн руб.", source_ids=["B1"]),
    ]

    assessment = assess_financial_conflicts(
        facts,
        sources=[_source("A1"), _source("B1")],
    )

    assert assessment.unresolved_critical_conflicts == 0
