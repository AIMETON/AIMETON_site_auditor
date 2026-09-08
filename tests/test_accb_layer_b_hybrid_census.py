from __future__ import annotations

from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/"scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0,str(SCRIPTS))

import accb_layer_b_hybrid_census as hybrid


WORKFLOW=Path(".github/workflows/accb-layer-b-hybrid-census.yml")


def test_hybrid_census_is_get_only_and_zero_generation() -> None:
    text=WORKFLOW.read_text(encoding="utf-8")
    trigger=text.split("permissions:",1)[0]
    assert "workflow_dispatch:" in trigger
    assert "issue_comment:" not in trigger
    assert "ROUTERAI_API_KEY" not in text
    assert "OPENROUTER_API_KEY" not in text
    assert "provider_generations" in text
    assert "provider_api_secrets" in text
    assert "10000" in text
    assert "OPENROUTER_PROXY_URL" in text
    assert "OPENROUTER_SOCKS_URL" not in text
    assert "runs-on: ubuntu-24.04" in text


def test_openrouter_guard_uses_conservative_maximum_rates() -> None:
    body={
        "data":{
            "endpoints":[
                {
                    "provider_name":"OpenAI Flex",
                    "context_length":1050000,
                    "max_prompt_tokens":922000,
                    "max_completion_tokens":128000,
                    "pricing":{"prompt":"0.000001","completion":"0.000005"},
                },
                {
                    "provider_name":"OpenAI",
                    "context_length":1050000,
                    "max_prompt_tokens":922000,
                    "max_completion_tokens":128000,
                    "pricing":{"prompt":"0.000004","completion":"0.000020"},
                },
                {
                    "provider_name":"Azure",
                    "context_length":1050000,
                    "pricing":{"prompt":"0.000005","completion":"0.000030"},
                },
            ]
        }
    }
    row=hybrid.select_openrouter_sol(body)
    assert row["provider_pin"] == "openai"
    assert row["eligible_openai_family_endpoints"] == 2
    assert row["guard_prompt_usd_per_token"] == 0.000004
    assert row["guard_completion_usd_per_token"] == 0.000020
    assert row["usd_to_rub_budget_guard_rate"] == 500.0
    assert row["estimate"]["model_total_rub_guard"] > 0


def test_openrouter_guard_rejects_insufficient_capacity() -> None:
    body={
        "data":{
            "endpoints":[
                {
                    "provider_name":"OpenAI",
                    "context_length":200000,
                    "pricing":{"prompt":"0.000004","completion":"0.000020"},
                }
            ]
        }
    }
    try:
        hybrid.select_openrouter_sol(body)
    except hybrid.HybridCensusError:
        pass
    else:
        raise AssertionError("insufficient-capacity Sol endpoint was admitted")


def test_hybrid_census_uses_execution_admission_v02_and_no_tokenizer_gate() -> None:
    assert hybrid.EXECUTION_ADMISSION_SHA == "67c8ea3e84405884136119d7252fe7424ccf1631"
    result_keys = Path("scripts/accb_layer_b_hybrid_census.py").read_text(encoding="utf-8")
    assert '"tokenizer_preflight_required": False' in result_keys
    assert '"primary_input_length_measurement": "provider-reported usage after successful scored response"' in result_keys
    assert "payload_sha256" in result_keys
    assert "request_text_bytes" in result_keys
    assert "request_text_characters" in result_keys
