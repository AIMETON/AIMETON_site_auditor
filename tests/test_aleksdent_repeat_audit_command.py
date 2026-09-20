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


DEEP_WORKFLOW = Path(".github/workflows/audit-aleksdent-deep-research-stage.yml")


def test_aleksdent_deep_audit_is_fixed_target_authenticated_and_bounded() -> None:
    router = ROUTER.read_text(encoding="utf-8")
    workflow = DEEP_WORKFLOW.read_text(encoding="utf-8")

    assert (
        '"audit-aleksdent-deep-stage": '
        '(293, "audit-aleksdent-deep-research-stage.yml", {"expected_sha": "{sha}"})'
    ) in router
    assert "TARGET_URL: https://aleksdent24.ru/" in workflow
    assert '"deep_research": True' in workflow
    assert '"unlimited_llm_budget": True' in workflow
    assert "/api/auth/login" in workflow
    assert "/api/admin/llm-settings" in workflow
    assert '~deepseek/deepseek-v4-flash-latest' in workflow
    assert "timeout-minutes: 45" in workflow
    assert "for _ in $(seq 1 800)" in workflow
    assert '[[ "$terminal" == completed || "$terminal" == failed ]]' in workflow
    assert "/stop" not in workflow
    assert "raw prompts, raw provider payloads or chain-of-thought are published" in workflow
