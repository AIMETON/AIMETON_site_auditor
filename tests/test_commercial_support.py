from app.commercial_support import assess_commercial_support, enforce_commercial_support
from app.models import CommercialOpportunity, CompanyFact, EconomicSignal, EvidenceSource


def _opportunity(*, score: int = 85, source_ids: list[str] | None = None) -> CommercialOpportunity:
    return CommercialOpportunity(
        opportunity_type="AI automation",
        problem_hypothesis="Ручная обработка заявок клиентов",
        recommended_solution="Автоматизировать обработку заявок",
        expected_value="Сократить ручной труд",
        score=score,
        qualification="Приоритетная" if score >= 80 else "Перспективная",
        source_ids=list(source_ids or []),
    )


def _source(identifier: str, quote: str) -> EvidenceSource:
    return EvidenceSource(
        id=identifier,
        title="Официальный документ",
        url=f"https://example.test/{identifier}",
        accessed_at="2026-09-19T00:00:00Z",
        evidence_quote=quote,
        source_type="official_page",
        evidence_level="confirmed_fact",
    )


def test_direct_evidence_support_preserves_priority_score():
    opportunity = _opportunity(source_ids=["S1"])
    assessment = assess_commercial_support(
        opportunity,
        sources=[_source("S1", "Ручная обработка заявок клиентов выполняется сотрудниками.")],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.state == "supported"
    assert assessment.direct_support_source_ids == ("S1",)
    assert enforced.score == 85
    assert enforced.qualification == "Приоритетная"


def test_unrelated_real_source_cannot_authorize_80_plus_score():
    opportunity = _opportunity(source_ids=["S1"])
    assessment = assess_commercial_support(
        opportunity,
        sources=[_source("S1", "Лицензия выдана медицинской организации на стоматологическую деятельность.")],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.state == "unsupported"
    assert enforced.score == 79
    assert enforced.qualification == "Перспективная"


def test_model_derived_signal_can_only_make_support_weak_without_direct_quote_overlap():
    opportunity = _opportunity(source_ids=["S1"])
    signal = EconomicSignal(
        signal="Ручная обработка заявок клиентов",
        evidence="Интерпретация процесса",
        business_effect="Потенциал автоматизации",
        source_ids=["S1"],
    )
    assessment = assess_commercial_support(
        opportunity,
        signals=[signal],
        sources=[_source("S1", "Контактная информация и режим работы.")],
    )

    assert assessment.state == "weak"
    assert assessment.direct_support_count == 0
    assert enforce_commercial_support(opportunity, assessment).score == 79


def test_missing_or_invented_source_ids_are_unsupported():
    opportunity = _opportunity(source_ids=["MISSING"])
    assessment = assess_commercial_support(
        opportunity,
        sources=[_source("S1", "Ручная обработка заявок клиентов.")],
    )

    assert assessment.state == "unsupported"
    assert assessment.cited_source_ids == ()


def test_lower_score_hypothesis_is_retained_but_marked_unsupported():
    opportunity = _opportunity(score=70, source_ids=["S1"])
    assessment = assess_commercial_support(
        opportunity,
        facts=[
            CompanyFact(field="products", value="Стоматология", source_ids=["S1"]),
        ],
        sources=[_source("S1", "Стоматологические услуги и цены.")],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.state == "unsupported"
    assert enforced.score == 70
    assert enforced.qualification == "Перспективная"


def test_child_source_id_matches_parent_document_support():
    opportunity = _opportunity(source_ids=["H1-b2-0"])
    assessment = assess_commercial_support(
        opportunity,
        sources=[_source("H1", "Ручная обработка заявок клиентов подтверждена документом.")],
    )

    assert assessment.state == "supported"
    assert assessment.cited_source_ids == ("H1",)
