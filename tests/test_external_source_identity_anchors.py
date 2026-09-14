from app.external_sources import IdentityAnchors, extract_identity_anchors, query_plan


def test_extract_identity_anchors_from_official_evidence() -> None:
    evidence = """
    SOURCE: https://stomadv.ru/contacts
    ООО «Стоматология для Вас»
    660073, г. Красноярск, ул. Тельмана, 32 А
    Телефон +7 (391) 291-51-55
    ИНН 2465085561 КПП 246501001 ОГРН 1042402655910
    SOURCE: https://stomadv.ru/about
    Клиника также работает в г. Железногорск.
    """

    anchors = extract_identity_anchors(evidence, "https://stomadv.ru/")

    assert anchors.domain == "stomadv.ru"
    assert anchors.inn == "2465085561"
    assert anchors.ogrn == "1042402655910"
    assert anchors.cities[:2] == ("Красноярск", "Железногорск")
    assert anchors.phones[0] == "+73912915155"


def test_query_plan_uses_domain_region_and_registry_ids() -> None:
    anchors = IdentityAnchors(
        domain="stomadv.ru",
        inn="2465085561",
        ogrn="1042402655910",
        cities=("Красноярск",),
        phones=("+73912915155",),
    )

    queries = query_plan("Стоматология для вас", anchors=anchors)
    plan = dict(queries)

    assert queries[0][1] == 'site:stomadv.ru "Стоматология для вас"'
    assert '"2465085561"' in plan["registry"]
    assert '"1042402655910"' in plan["registry"]
    assert '"Красноярск"' in plan["review"]
    assert '"+73912915155"' in plan["contact"]


def test_query_plan_falls_back_to_name_when_no_anchors() -> None:
    plan = dict(query_plan("Example Company"))
    assert plan["official"].startswith('"Example Company"')
