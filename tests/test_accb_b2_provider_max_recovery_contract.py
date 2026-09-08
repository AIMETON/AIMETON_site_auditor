from pathlib import Path
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "accb_b2_provider_max_recovery.py"
WORKFLOW = ROOT / ".github" / "workflows" / "accb-b2-provider-max-selective-recovery.yml"
ROUTER = ROOT / "scripts" / "aimeton_command_router.py"


def test_provider_max_recovery_has_no_aimeton_compute_cap() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "EXPECTED_COMMON_OUTPUT_CEILING" not in text
    assert "MAX_OUTPUT_TOKENS = 8192" not in text
    assert '"effort": "low"' not in text
    assert '"compute_policy": "selected_endpoint_advertised_maximum"' in text
    assert '"common_output_ceiling_tokens": None' in text
    assert 'endpoint_ceiling = int(endpoint_route.get("max_completion_tokens") or 0)' in text
    assert '"max_output_tokens_sent": endpoint_ceiling' in text
    assert '"selected_endpoint_max_completion_tokens": endpoint_ceiling' in text


def test_provider_max_recovery_is_selective_manifest_only() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--manifest" in text
    assert "load_recovery_manifest" in text
    assert "source_run_id" in text
    assert "source_site_auditor_sha" in text
    assert "source_evidence_comment_id" in text
    assert "provider_max_policy_sha" in text
    assert "for selected in recovery_cells" in text
    assert "for model in ALL_MODELS" not in text
    assert "for tier_id in TIER_ORDER" not in text
    assert '"planned_cells": len(recovery_cells)' in text
    assert 'generation_attempts == len(recovery_cells)' in text


def test_provider_max_endpoint_limit_is_model_capability_boundary() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "MODEL_ENDPOINT_COMPUTE_LIMIT_REACHED" in text
    assert 'call["status"] = "MODEL_ENDPOINT_COMPUTE_LIMIT_REACHED"' in text
    assert "OUTPUT_BUDGET_EXHAUSTED" in text
    assert 'row["ACI_B2"] = 0.0' in text


def test_provider_max_recovery_keeps_no_retry_no_fallback() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert '"silent_retries": 0' in text
    assert '"fallbacks": False' in text
    assert '"allow_fallbacks": False' in text
    assert '"forced_low_reasoning_effort": False' in text
    assert "max_budget_rub" not in workflow
    assert "ACCB_B2_MAX_BUDGET_RUB" not in workflow
    assert "owner_spend_authorized" in workflow


def test_provider_max_recovery_workflow_is_dispatch_only_exact_sha() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "push:" not in trigger
    assert "pull_request:" not in trigger
    assert "runs-on: ubuntu-24.04" in text
    assert "environment: stage" in text
    assert "timeout-minutes: 720" in text
    assert "actions/checkout@" not in text
    assert "expected_sha" in text
    assert "manifest_path" in text


def test_provider_max_recovery_command_route_is_owner_exact_sha() -> None:
    router = ROUTER.read_text(encoding="utf-8")
    expected = (
        '"recover-accb-b2-provider-max": (798, "accb-b2-provider-max-selective-recovery.yml", '
        '{"expected_sha": "{sha}", "manifest_path": '
        '"docs/research/ACCB_B2_PROVIDER_MAX_RECOVERY_MANIFEST_v0.1.json", '
        '"evidence_issue": "798", "owner_spend_authorized": "true"})'
    )
    assert expected in router


def test_provider_max_recovery_script_compiles() -> None:
    compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")
