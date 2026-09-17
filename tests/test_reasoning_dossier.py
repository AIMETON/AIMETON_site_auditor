import json
from types import SimpleNamespace as NS

from app.models import CompanyFact, EconomicSignal
from app.reasoning_dossier import build_reasoning_dossier


def test_large_profile_is_bounded_for_reasoning_without_mutating_source_profile():
    products = [
        CompanyFact(field="products", value=f"Услуга {i}", confidence="Средняя", source_ids=["S1"])
        for i in range(180)
    ]
    other = [
        CompanyFact(field="other", value=f"Прочий факт {i}", confidence="Низкая", source_ids=["R1"])
        for i in range(120)
    ]
    identity = [
        CompanyFact(field="legal_name", value="ООО Алекс Дент", confidence="Высокая", source_ids=["S1"]),
        CompanyFact(field="inn", value="2462215501", confidence="Высокая", source_ids=["R1"]),
    ]
    signals = [
        EconomicSignal(
            signal=f"Сигнал {i}", evidence=f"Основание {i}", business_effect=f"Эффект {i}",
            confidence="Средняя", source_ids=["R1"],
        )
        for i in range(60)
    ]
    profile = NS(
        company_name="Алекс Дент",
        business_summary="Стоматологическая клиника",
        company_facts=[*identity, *products, *other],
        economic_signals=signals,
        evidence=[f"Evidence {i}" for i in range(50)],
        risks_and_assumptions=[f"Risk {i}" for i in range(50)],
        coverage={"complete": True},
    )

    dossier = build_reasoning_dossier(profile)

    assert len(profile.company_facts) == 302  # source profile is untouched
    assert len(dossier.facts_by_field["products"]) == 40
    assert len(dossier.facts_by_field["other"]) == 20
    assert len(dossier.facts_by_field["legal_name"]) == 1
    assert len(dossier.facts_by_field["inn"]) == 1
    assert dossier.omitted_fact_counts_by_field == {"other": 100, "products": 140}
    assert dossier.total_facts == 302
    assert dossier.selected_facts == 62
    assert len(dossier.economic_signals) == 24
    assert dossier.total_signals == 60
    assert dossier.selected_signals == 24
    assert len(dossier.evidence_highlights) == 12
    assert len(dossier.risks_and_assumptions) == 16
    assert dossier.safe_metrics()["omitted_facts"] == 240
    assert dossier.safe_metrics()["omitted_signals"] == 36

    serialized = json.dumps(dossier.model_dump(mode="json"), ensure_ascii=False)
    full = json.dumps({
        "facts": [fact.model_dump(mode="json") for fact in profile.company_facts],
        "signals": [signal.model_dump(mode="json") for signal in profile.economic_signals],
        "evidence": profile.evidence,
        "risks": profile.risks_and_assumptions,
    }, ensure_ascii=False)
    assert len(serialized) < len(full) * 0.4


def test_reasoning_dossier_prioritizes_high_confidence_and_sourced_facts():
    facts = [
        CompanyFact(field="products", value=f"Low {i}", confidence="Низкая")
        for i in range(45)
    ]
    facts.extend([
        CompanyFact(field="products", value="High verified", confidence="Высокая", source_ids=["S1"]),
        CompanyFact(field="products", value="Medium verified", confidence="Средняя", source_ids=["R1"]),
    ])
    profile = NS(
        company_name="Company", business_summary="Summary", company_facts=facts,
        economic_signals=[], evidence=[], risks_and_assumptions=[], coverage={},
    )

    dossier = build_reasoning_dossier(profile)
    values = [fact.value for fact in dossier.facts_by_field["products"]]

    assert "High verified" in values
    assert "Medium verified" in values
    assert dossier.omitted_fact_counts_by_field["products"] == 7
