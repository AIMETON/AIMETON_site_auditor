import asyncio

from app.hunter_query_intelligence import (
    HunterQueryPlan,
    _dedupe_queries,
    _query_preserves_semantic_anchors,
    _validated_queries,
    generate_hunter_query_plan,
)


def test_hunter_query_plan_deduplicates_case_and_whitespace() -> None:
    values = [
        "стоматология Красноярск официальный сайт",
        "  стоматология   Красноярск официальный сайт ",
        "Стоматология Красноярск официальный сайт",
        "зубная клиника Красноярск",
    ]

    assert _dedupe_queries(values, 10) == [
        "стоматология Красноярск официальный сайт",
        "зубная клиника Красноярск",
    ]


def test_hunter_query_plan_schema_accepts_corrected_typo_example() -> None:
    plan = HunterQueryPlan(
        normalized_region="Красноярск",
        normalized_industries=["стоматология"],
        corrected_input_summary="Исправлена очевидная опечатка: «стамотология» → «стоматология».",
        query_variants=[
            "стоматология Красноярск официальный сайт",
            "стоматологическая клиника Красноярск",
            "частная стоматология Красноярск",
        ],
    )

    assert plan.normalized_industries == ["стоматология"]
    assert len(plan.query_variants) == 3


def test_query_semantic_guard_requires_region_and_industry() -> None:
    assert _query_preserves_semantic_anchors(
        "стоматологические клиники Красноярска официальный сайт",
        region="Красноярск",
        industries=["стоматология"],
    )
    assert not _query_preserves_semantic_anchors(
        "каталог товаров",
        region="Красноярск",
        industries=["стоматология"],
    )
    assert not _query_preserves_semantic_anchors(
        "детская Красноярск",
        region="Красноярск",
        industries=["стоматология"],
    )
    assert not _query_preserves_semantic_anchors(
        "стоматология список клиник",
        region="Красноярск",
        industries=["стоматология"],
    )


def test_validated_queries_drop_generic_noise_and_preserve_order() -> None:
    valid, rejected = _validated_queries(
        [
            "стоматология Красноярск официальный сайт",
            "каталог товаров",
            "детская Красноярск",
            "стоматологические клиники Красноярска",
            "Стоматология Красноярск официальный сайт",
        ],
        region="Красноярск",
        industries=["стоматология"],
        limit=10,
    )

    assert valid == [
        "стоматология Красноярск официальный сайт",
        "стоматологические клиники Красноярска",
    ]
    assert rejected == 2


def test_hunter_query_intelligence_falls_back_without_routerai_key(monkeypatch) -> None:
    monkeypatch.delenv("ROUTERAI_API_KEY", raising=False)

    result = asyncio.run(
        generate_hunter_query_plan(
            region="Красноярск",
            industries=["стамотология"],
            focus=[],
            max_queries=12,
        )
    )

    assert result is None
