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


def test_unsupported_quantitative_expected_value_is_replaced():
    opportunity = _opportunity(score=72, source_ids=["S1"]).model_copy(update={
        "expected_value": "Снижение нагрузки на 30-40% и ответ за 1-2 минуты",
    })
    assessment = assess_commercial_support(
        opportunity,
        sources=[_source("S1", "Ручная обработка заявок клиентов выполняется администраторами.")],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.unsupported_expected_value_metrics == ("1-2:minute", "30-40:%")
    assert "30-40%" not in enforced.expected_value
    assert "1-2" not in enforced.expected_value
    assert "количественные KPI" in enforced.expected_value


def test_directly_evidenced_quantitative_expected_value_is_preserved():
    opportunity = _opportunity(score=72, source_ids=["S1"]).model_copy(update={
        "expected_value": "Снижение нагрузки на 30-40% и ответ за 1-2 минуты",
    })
    assessment = assess_commercial_support(
        opportunity,
        sources=[_source(
            "S1",
            "Ручная обработка заявок клиентов. Пилот показал снижение нагрузки на 30-40% "
            "и время ответа 1-2 минуты.",
        )],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.unsupported_expected_value_metrics == ()
    assert enforced.expected_value == opportunity.expected_value


def test_empty_commercial_citations_are_recovered_from_supported_sourced_fact():
    opportunity = _opportunity(source_ids=[]).model_copy(update={
        "problem_hypothesis": "Сеть использует онлайн запись заявок клиентов",
    })
    fact = CompanyFact(
        field="other",
        value="Онлайн запись заявок клиентов доступна на сайте",
        source_ids=["S1"],
    )
    assessment = assess_commercial_support(
        opportunity,
        facts=[fact],
        sources=[_source("S1", "Онлайн запись заявок клиентов доступна во всех филиалах сети.")],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.state == "supported"
    assert assessment.cited_source_ids == ("S1",)
    assert enforced.source_ids == ["S1"]


def test_empty_commercial_citations_do_not_recover_unrelated_sourced_fact():
    opportunity = _opportunity(source_ids=[])
    fact = CompanyFact(
        field="products",
        value="Стоматологические услуги",
        source_ids=["S1"],
    )
    assessment = assess_commercial_support(
        opportunity,
        facts=[fact],
        sources=[_source("S1", "Стоматологические услуги и лицензия клиники.")],
    )

    enforced = enforce_commercial_support(opportunity, assessment)

    assert assessment.state == "unsupported"
    assert assessment.cited_source_ids == ()
    assert enforced.source_ids == []
