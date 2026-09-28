from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "scripts" / "aimeton_command_router.py"
WORKFLOW = ROOT / ".github" / "workflows" / "configure-immers-llm-stage.yml"
DRIVER = ROOT / "scripts" / "configure_immers_llm_stage.py"


def test_immers_stage_configuration_is_owner_routed_and_exact_sha_gated():
    router = ROUTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert (
        '"configure-immers-llm-stage": '
        '(1026, "configure-immers-llm-stage.yml", '
        '{"expected_sha": "{sha}", "allow_provider_calls": "true", '
        '"owner_spend_authorized": "true"})'
    ) in router
    assert "workflow_dispatch:" in workflow
    assert "inputs.expected_sha" in workflow
    assert 'test "$deployed" = "$expected"' in workflow
    assert "allow_provider_calls" in workflow
    assert "owner_spend_authorized" in workflow


def test_immers_stage_configuration_persists_role_specific_models():
    driver = DRIVER.read_text(encoding="utf-8")
    assert '"fast_research": "qwen3.6-35b-a3b"' in driver
    assert '"extraction": "deepseek-v4-flash-0731"' in driver
    assert '"reasoning": "deepseek-v4-flash-0731"' in driver
    assert '"profile_name"] = "immers-primary"' in driver
    assert '"PUT",' in driver
    assert '"/api/admin/llm-settings"' in driver
    assert "immers_readback_failed" in driver


def test_immers_stage_configuration_probes_fast_and_heavy_models_before_save():
    driver = DRIVER.read_text(encoding="utf-8")
    assert 'for role in ("fast_research", "extraction"):' in driver
    assert '"/api/admin/llm-settings/test"' in driver
    assert "immers_probe_failed" in driver
    assert "resolved_model" in driver
