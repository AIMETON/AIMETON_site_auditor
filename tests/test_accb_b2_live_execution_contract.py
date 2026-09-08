from pathlib import Path

WORKFLOW = Path(".github/workflows/accb-b2-live-execution.yml")
SCRIPT = Path("scripts/accb_b2_live_execution.py")
ROUTER = Path("scripts/aimeton_command_router.py")


def test_b2_live_execution_is_dispatch_only_hosted_owner_bounded() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "pull_request:" not in trigger
    assert "push:" not in trigger
    assert "runs-on: ubuntu-24.04" in workflow
    assert "environment: stage" in workflow
    assert 'default: "10000"' in workflow
    assert 'test "$OWNER_SPEND_AUTHORIZED" = "true"' in workflow
    assert "ROUTERAI_API_KEY: ${{ secrets.ROUTERAI_API_KEY }}" in workflow
    assert "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in workflow
    assert "actions/checkout@" not in workflow
    assert "actions/setup-python@" not in workflow
    assert "actions/upload-artifact@" not in workflow


def test_b2_live_harness_is_exact_information_load_matrix() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'EXPERIMENT_ID = "ACCB-B2-INFOLOAD-DIAGNOSTIC-v0.1"' in script
    assert '"b2-32k": (32768,' in script
    assert '"b2-64k": (65536,' in script
    assert '"b2-140k": (143934,' in script
    assert '"b2-562k": (575367,' in script
    assert '"b2-2191k": (2297725,' in script
    assert '"planned_cells": 25' in script
    assert '"one_provider_generation_per_cell": True' in script
    assert '"silent_retries": 0' in script
    assert '"fallbacks": False' in script


def test_b2_live_removes_legacy_compute_confounds() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'EXPECTED_COMMON_OUTPUT_CEILING = 128_000' in script
    assert '"common_output_ceiling_tokens": common_ceiling' in script
    assert '"legacy_global_8192_output_cap_allowed": False' in script
    assert '"forced_low_reasoning_effort": False' in script
    assert "MAX_OUTPUT_TOKENS = 8192" not in script
    assert '"effort": "low"' not in script
    assert '"reasoning_effort_sent": None' in script
    assert "'reasoning': {'effort': 'low'}" not in script
    assert "! grep -F 'MAX_OUTPUT_TOKENS = 8192'" in workflow
    assert "! grep -F '"effort": "low"'" in workflow


def test_b2_live_repeats_fresh_capability_and_budget_admission() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "fresh = census.run()" in script
    assert 'ACCB_B2_ENDPOINT_CAPABILITY_CENSUS_COMPLETE' in script
    assert 'common_ceiling != EXPECTED_COMMON_OUTPUT_CEILING' in script
    assert 'fresh_guard <= max_budget_rub' in script
    assert 'OWNER_CEILING_RUB = 10_000.0' in script
    assert 'max_budget_rub != OWNER_CEILING_RUB' in script


def test_b2_live_preserves_reasoning_and_finish_telemetry_without_raw_reasoning() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "provider_reasoning_tokens" in script
    assert "provider_final_answer_tokens" in script
    assert "finish_reason" in script
    assert "incomplete_reason" in script
    assert "OUTPUT_BUDGET_EXHAUSTED" in script
    assert '"raw_provider_reasoning_retained": False' in script
    assert "output/reasoning/final" in workflow


def test_b2_live_distinguishes_model_contract_failure_from_integration_failure() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert '"MODEL_OUTPUT_CONTRACT_FAILURE"' in script
    assert 'row["ACI_B2"] = 0.0' in script
    assert 'row["ACI_B2_min"] = 0.0' in script
    assert "INTEGRATION_FAILURE_EMPTY_FINAL_CONTENT" in script
    assert "OUTPUT_BUDGET_EXHAUSTED" in script


def test_b2_live_workflow_requires_complete_25_cell_terminal_receipt() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'assert p["planned_cells"] == 25' in workflow
    assert 'assert p["completed_cells"] == 25' in workflow
    assert 'assert p["provider_generation_attempts_total"] == 25' in workflow
    assert 'assert p["harness_failure_cells"] == 0' in workflow
    assert 'assert p["status"] == "ACCB_B2_EXECUTION_COMPLETE"' in workflow
    assert 'assert p["completion_criterion_met"] is True' in workflow


def test_owner_command_router_has_exact_b2_live_route() -> None:
    router = ROUTER.read_text(encoding="utf-8")
    expected = (
        '"execute-accb-b2": (798, "accb-b2-live-execution.yml", '
        '{"expected_sha": "{sha}", "evidence_issue": "798", '
        '"owner_spend_authorized": "true", "max_budget_rub": "10000"})'
    )
    assert expected in router
