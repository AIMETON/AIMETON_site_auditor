from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "scripts" / "aimeton_command_router.py"
WORKFLOW = ROOT / ".github" / "workflows" / "accept-immers-full-site-audit-stage.yml"
DRIVER = ROOT / "scripts" / "accept_immers_full_stage.py"


def test_immers_full_acceptance_is_owner_routed_and_exact_sha_gated():
    router = ROUTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert (
        '"accept-immers-full-stage": '
        '(1003, "accept-immers-full-site-audit-stage.yml", '
        '{"expected_sha": "{sha}", "allow_paid_calls": "true", '
        '"owner_spend_authorized": "true"})'
    ) in router
    assert "workflow_dispatch:" in workflow
    assert "inputs.expected_sha" in workflow
    assert "allow_paid_calls" in workflow
    assert "owner_spend_authorized" in workflow
    assert "test \"$deployed\" = \"$expected\"" in workflow


def test_immers_full_acceptance_restores_settings_and_keeps_evidence_sanitized():
    driver = DRIVER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'finally:' in driver
    assert '"restore settings after Immers full Site Audit acceptance #1003"' in driver
    assert 'restored_response["record"]["settings"] == original' in driver
    assert "completion text" in driver
    assert "IMMERS_API_KEY" not in driver
    assert "Authorization" not in driver
    assert "AIMETON_BOOTSTRAP_ADMIN_PASSWORD" in workflow
    assert "secrets.AIMETON_BOOTSTRAP_ADMIN_PASSWORD" in workflow
    assert "cat $AIMETON_BOOTSTRAP_ADMIN_PASSWORD" not in workflow


def test_immers_acceptance_targets_current_aldenta_regression_and_provider_evidence():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    driver = DRIVER.read_text(encoding="utf-8")
    assert "TARGET_URL: https://aldenta.ru/" in workflow
    assert "llm_last_provider" in driver
    assert "llm_reasoning_provider" in driver
    assert "commercial_reasoning_failure" in driver


def test_immers_acceptance_uses_production_strict_schema_policy_and_requires_reasoning_success():
    driver = DRIVER.read_text(encoding="utf-8")
    assert 'item["output_mode"] = "inherit"' in driver
    assert 'item["output_mode"] = "json_object"' not in driver
    assert 'commercial_reasoning_state") != "succeeded"' in driver
    assert "immers_extraction_returned_no_company_facts" in driver
