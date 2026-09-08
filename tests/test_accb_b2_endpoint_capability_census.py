from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import accb_b2_endpoint_capability_census as census


def _router_body():
    return {
        "data": {
            "endpoints": [
                {
                    "provider_name": "small",
                    "tag": "small",
                    "status": 0,
                    "context_length": 1000000,
                    "max_completion_tokens": 8192,
                    "supported_apis": ["chat"],
                    "supported_parameters": ["max_tokens"],
                    "pricing": {"prompt": 0.0001, "completion": 0.0002},
                },
                {
                    "provider_name": "large",
                    "tag": "large",
                    "status": 0,
                    "context_length": 1048576,
                    "max_completion_tokens": 131072,
                    "supported_apis": ["chat"],
                    "supported_parameters": ["max_tokens", "reasoning"],
                    "pricing": {"prompt": 0.0002, "completion": 0.0003},
                },
            ]
        }
    }


def test_routerai_selection_rejects_legacy_8192_and_prefers_larger_output_capacity():
    route = census.routerai_candidate("vendor/model", _router_body())
    assert route["tag"] == "large"
    assert route["max_completion_tokens"] == 131072
    assert route["max_completion_tokens"] > 8192


def test_cost_guard_uses_selected_endpoint_maximum_output_capacity():
    route = census.routerai_candidate("vendor/model", _router_body())
    estimate = census.estimate_route(route)
    assert len(estimate["tiers"]) == 5
    assert all(
        row["max_output_tokens_admitted"] == route["max_completion_tokens"]
        for row in estimate["tiers"]
    )
    assert estimate["model_total_rub_guard"] > 0


def test_census_source_has_no_provider_secret_dependency_or_generation_post():
    text = (SCRIPTS / "accb_b2_endpoint_capability_census.py").read_text(encoding="utf-8")
    assert "ROUTERAI_API_KEY" not in text
    assert "OPENROUTER_API_KEY" not in text
    assert 'method="POST"' not in text
    assert "MAX_OUTPUT_TOKENS = 8192" not in text
