from app.models import CompanyFact
from app.verified_analysis import _merge_late_enrichment_facts


def test_late_identity_enrichment_merges_equivalent_registry_facts() -> None:
    facts = [
        CompanyFact(
            field="legal_name",
            value='ООО «Алекс Дент»',
            confidence="Высокая",
            source_ids=["S1"],
            note="official",
        ),
        CompanyFact(field="inn", value="2462215501", source_ids=["R-H25"]),
        CompanyFact(field="ogrn", value="1112468013030", source_ids=["R-H25"]),
        CompanyFact(field="registration_status", value="Действующая", source_ids=["R-H25"]),
    ]
    additions = [
        CompanyFact(
            field="legal_name",
            value='Общество с ограниченной ответственностью "АЛЕКС ДЕНТ"',
            confidence="Средняя",
            note="DaData registry mirror; authority_verified=false",
        ),
        CompanyFact(field="inn", value="24 6221 5501", note="DaData"),
        CompanyFact(field="ogrn", value="1 112 468 013 030", note="DaData"),
        CompanyFact(field="registration_status", value="Действующая организация", note="DaData"),
    ]

    merged = _merge_late_enrichment_facts(facts, additions)

    assert merged == 4
    assert len(facts) == 4
    assert [fact.field for fact in facts].count("legal_name") == 1
    assert [fact.field for fact in facts].count("inn") == 1
    assert [fact.field for fact in facts].count("ogrn") == 1
    assert [fact.field for fact in facts].count("registration_status") == 1
    legal = next(fact for fact in facts if fact.field == "legal_name")
    assert legal.value == 'ООО «Алекс Дент»'
    assert legal.confidence == "Высокая"
    assert legal.source_ids == ["S1"]
    assert "official" in legal.note
    assert "DaData registry mirror" in legal.note


def test_late_identity_enrichment_keeps_genuine_conflicts() -> None:
    facts = [
        CompanyFact(field="inn", value="2462215501", source_ids=["S1"]),
        CompanyFact(field="registration_status", value="Действующая", source_ids=["S1"]),
    ]
    additions = [
        CompanyFact(field="inn", value="7707083893", note="DaData"),
        CompanyFact(field="registration_status", value="LIQUIDATED", note="DaData"),
    ]

    merged = _merge_late_enrichment_facts(facts, additions)

    assert merged == 0
    assert [fact.value for fact in facts if fact.field == "inn"] == [
        "2462215501",
        "7707083893",
    ]
    assert [fact.value for fact in facts if fact.field == "registration_status"] == [
        "Действующая",
        "LIQUIDATED",
    ]


def test_late_enrichment_does_not_semantically_merge_non_identity_facts() -> None:
    facts = [
        CompanyFact(field="products", value="Имплантация — от 69 000 ₽", source_ids=["S1"]),
    ]
    additions = [
        CompanyFact(field="products", value="Имплантация — 72 000 руб.", source_ids=["R1"]),
    ]

    merged = _merge_late_enrichment_facts(facts, additions)

    assert merged == 0
    assert len(facts) == 2
