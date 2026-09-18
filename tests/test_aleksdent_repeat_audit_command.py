from pathlib import Path


ROUTER = Path("scripts/aimeton_command_router.py")


def test_aleksdent_repeat_audit_uses_existing_stage_audit_workflow() -> None:
    text = ROUTER.read_text(encoding="utf-8")
    expected = (
        '"audit-aleksdent-stage": '
        '(293, "audit-competitor-services-realty-stage.yml", '
        '{"expected_sha": "{sha}", "target_url": "https://aleksdent24.ru/", '
        '"audit_label": "Aleks Dent regression repeat"})'
    )
    assert expected in text
