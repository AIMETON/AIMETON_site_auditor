import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "accb_b2_glm_reasoning_rerun.py"
WORKFLOW = ROOT / ".github" / "workflows" / "accb-b2-glm-reasoning-normalized-rerun.yml"
MANIFEST = ROOT / "docs" / "research" / "ACCB_B2_GLM_REASONING_RERUN_MANIFEST_v0.1.json"
ROUTER = ROOT / "scripts" / "aimeton_command_router.py"


def test_glm_reasoning_rerun_script_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    assert 'GLM_MODEL = "z-ai/glm-5.2"' in text
    assert 'GLM_REASONING_EFFORT = "high"' in text
    assert "GLM_THINKING_BUDGET = 32768" in text
    assert '"reasoning": {"effort": GLM_REASONING_EFFORT}' in text
    assert '"thinking_budget": GLM_THINKING_BUDGET' in text
    assert "max_tokens_sent = common_ceiling - GLM_THINKING_BUDGET" in text
    assert '"reasoning_budget_tokens_sent": GLM_THINKING_BUDGET' in text
    assert '"compute_policy": "selected_endpoint_advertised_maximum_with_glm_reasoning_reserve"' in text
    assert 'EXPERIMENT_ID = "ACCB-B2-GLM-REASONING-NORMALIZED-v0.1"' in text
    assert '"allow_fallbacks": False' in text
    assert '"silent_retries": 0' in text


def test_glm_reasoning_rerun_manifest_is_all_five_frozen_tiers() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_run_id"] == 34216143208
    assert data["source_site_auditor_sha"] == "390e279549f2c9ec4a6fd8700e7dce9274252c88"
    assert data["source_evidence_comment_id"] == 5586594799
    assert data["reasoning_policy"] == {
        "effort": "high",
        "thinking_budget_tokens": 32768,
        "final_answer_reserve_policy": "max_tokens = selected_endpoint_max_completion_tokens - thinking_budget_tokens",
    }
    assert [(r["model"], r["tier_id"]) for r in data["cells"]] == [
        ("z-ai/glm-5.2", "b2-32k"),
        ("z-ai/glm-5.2", "b2-64k"),
        ("z-ai/glm-5.2", "b2-140k"),
        ("z-ai/glm-5.2", "b2-562k"),
        ("z-ai/glm-5.2", "b2-2191k"),
    ]
    assert data["retries"] == 0
    assert data["fallbacks"] is False


def test_glm_reasoning_rerun_workflow_and_command_are_exact_sha_owner_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    router = ROUTER.read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "pull_request:" not in trigger
    assert "push:" not in trigger
    assert "runs-on: ubuntu-24.04" in workflow
    assert "environment: stage" in workflow
    assert "owner_spend_authorized" in workflow
    assert "scripts/accb_b2_glm_reasoning_rerun.py" in workflow
    assert "ACCB-B2-GLM-REASONING-NORMALIZED-v0.1" in workflow
    assert "p[\"planned_cells\"] == 5" in workflow
    assert 'r["reasoning_effort_sent"] == "high"' in workflow
    assert 'r["reasoning_budget_tokens_sent"] == 32768' in workflow
    assert '"rerun-accb-b2-glm-reasoning": (798, "accb-b2-glm-reasoning-normalized-rerun.yml"' in router
