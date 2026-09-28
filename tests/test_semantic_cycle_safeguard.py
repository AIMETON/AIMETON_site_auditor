from app.research_coverage_controller import assess_coverage
from app.verified_analysis import _semantic_loop_signature


def test_semantic_cycle_signature_collapses_cosmetic_repetition() -> None:
    coverage = assess_coverage([], {"registry", "finance"})

    first = _semantic_loop_signature(
        coverage,
        ("ИНН организации установлен", "  Финансы   не найдены "),
        [("registry", '"123"   реквизиты')],
    )
    repeated = _semantic_loop_signature(
        coverage,
        ("инн организации установлен", "ФИНАНСЫ не найдены"),
        [("registry", '"123" реквизиты')],
    )

    assert first == repeated


def test_semantic_cycle_signature_changes_with_real_progress() -> None:
    coverage = assess_coverage([], {"registry", "finance"})
    plan = [("registry", '"123" реквизиты')]

    before = _semantic_loop_signature(
        coverage,
        ("ИНН организации установлен",),
        plan,
    )
    after = _semantic_loop_signature(
        coverage,
        (
            "ИНН организации установлен",
            "Получена новая подтвержденная финансовая отчетность",
        ),
        plan,
    )
    different_direction = _semantic_loop_signature(
        coverage,
        ("ИНН организации установлен",),
        [("court", '"123" арбитраж')],
    )

    assert before != after
    assert before != different_direction
