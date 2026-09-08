from pathlib import Path


WORKFLOW = Path(".github/workflows/accb-layer-b-live-execution.yml")
SCRIPT = Path("scripts/accb_layer_b_live_execution.py")
ROUTER = Path("scripts/aimeton_command_router.py")


def test_layer_b_live_execution_is_dispatch_only_hosted_and_owner_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "pull_request:" not in trigger
    assert "push:" not in trigger
    assert "runs-on: ubuntu-24.04" in text
    assert "environment: stage" in text
    assert "owner_spend_authorized" in text
    assert "max_budget_rub" in text
    assert 'default: "10000"' in text
    assert 'test "$OWNER_SPEND_AUTHORIZED" = "true"' in text
    assert 'float(os.environ["ACCB_MAX_BUDGET_RUB"]) == 10000.0' in text
    assert "ROUTERAI_API_KEY: ${{ secrets.ROUTERAI_API_KEY }}" in text
    assert "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in text
    assert "actions/checkout@" not in text
    assert "actions/setup-python@" not in text
    assert "actions/upload-artifact@" not in text


def test_layer_b_live_execution_is_tokenizer_free_and_uses_common_payload() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "accb_layer_b_common_payload_preflight as common" in script
    assert "materialize_common_payloads" in script
    assert "fit_payload_to_local_anchor" not in script
    assert "accb_layer_b_tokenizer_preflight" not in script
    assert "AutoTokenizer" not in script
    assert "transformers" not in workflow
    assert "huggingface-hub" not in workflow
    assert "tiktoken" not in workflow
    assert '"tokenizer_required_at_execution": False' in script
    assert '"L_payload_local_estimate": None' in script


def test_layer_b_live_harness_repeats_fresh_admission_before_paid_calls() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'hybrid.run("", "direct")' in text
    assert 'ACCB_LAYER_B_HYBRID_CENSUS_COMPLETE' in text
    assert 'budget_admitted' in text
    assert 'OWNER_CEILING_RUB = 10_000.0' in text
    assert 'max_budget_rub != OWNER_CEILING_RUB' in text
    assert '"planned_cells": 15' in text
    assert '"provider_generation_attempts": 1' in text
    assert 'EXECUTION_ADMISSION_SHA = "363f69971ed82ce3e4fc5ea9716652e57e83118e"' in text


def test_cross_model_axis_is_identical_payload_bytes_and_provider_tokens_are_secondary() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert '"primary_cross_model_input_axis": "request_text_bytes"' in text
    assert '"payload_identity_field": "payload_sha256"' in text
    assert "secondary model-specific tokenizer/provider-framing telemetry" in text
    assert "payload bytes" in workflow
    assert "payload SHA256" in workflow
    assert "provider tokens (diagnostic)" in workflow


def test_missing_provider_input_usage_is_measurement_degraded_not_cognition_failure() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "MEASUREMENT_DEGRADED_NO_PROVIDER_INPUT_COUNT" in text
    assert "SCORED_MEASUREMENT_DEGRADED_NO_PROVIDER_INPUT_COUNT" in text
    assert "INTEGRATION_FAILURE_NO_AUTHORITATIVE_INPUT_COUNT" not in text
    assert 'row["ACI"] = score.get("ACI")' in text
    assert 'row["L_model_input_provider"] = prompt_tokens' in text


def test_layer_b_live_harness_preserves_scientific_execution_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"allow_fallbacks": False' in text
    assert '"silent_retries": 0' in text
    assert '"fallbacks": False' in text
    assert '"raw_prompt_retained": False' in text
    assert '"raw_completion_retained": False' in text
    assert '"raw_provider_reasoning_retained": False' in text
    assert '"provider_seed_sent": False' in text
    assert "MODEL_OUTPUT_UNSCORABLE_JSON" in text
    assert "HARNESS_FAILURE" in text
    assert "conservative_cell_guard_due_usage_degraded" in text


def test_layer_b_live_workflow_enforces_complete_15_cell_receipt() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'assert p["planned_cells"] == 15' in text
    assert 'assert p["completed_cells"] == 15' in text
    assert 'assert all(row["provider_generation_attempts"] == 1 for row in p["cells"])' in text
    assert 'assert p["status"] == "ACCB_LAYER_B_EXECUTION_COMPLETE"' in text
    assert 'assert p["completion_criterion_met"] is True' in text
    assert "payload bytes" in text
    assert "measurement" in text


def test_owner_command_router_has_exact_layer_b_live_route() -> None:
    text = ROUTER.read_text(encoding="utf-8")
    expected = (
        '"execute-accb-layer-b": (798, "accb-layer-b-live-execution.yml", '
        '{"expected_sha": "{sha}", "evidence_issue": "798", '
        '"owner_spend_authorized": "true", "max_budget_rub": "10000"})'
    )
    assert expected in text
