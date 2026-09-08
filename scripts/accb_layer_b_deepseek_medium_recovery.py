#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import accb_layer_b_live_execution as live
import accb_layer_b_payload as payload

MODEL = "deepseek/deepseek-v4-pro-0813"
ANCHOR = 131072
EXPECTED_BYTES = 575367
EXPECTED_PAYLOAD_SHA256 = "eb3bf2c81c12320ce992fd8aba0028e891673f7054d3bd2286ad58a486f811cc"
SOURCE_FAILED_RUN_ID = 34182582503
MAX_RECOVERY_BUDGET_RUB = 200.0


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise live.ExecutionError(f"missing required environment variable: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    site_sha = required_env("ACCB_EXPECTED_SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", site_sha):
        raise live.ExecutionError("ACCB_EXPECTED_SHA must be exact 40-char SHA")
    api_key = required_env("ROUTERAI_API_KEY")
    max_budget = float(required_env("ACCB_RECOVERY_MAX_BUDGET_RUB"))
    if max_budget != MAX_RECOVERY_BUDGET_RUB:
        raise live.ExecutionError("recovery budget must equal 200 RUB")

    common_payloads = live.materialize_common_payloads(args.architecture_root)
    materialized = common_payloads[ANCHOR]
    if materialized["request_text_bytes"] != EXPECTED_BYTES:
        raise live.ExecutionError("recovery payload byte mismatch")
    if materialized["payload_sha256"] != EXPECTED_PAYLOAD_SHA256:
        raise live.ExecutionError("recovery payload hash mismatch")

    census = live.hybrid.run("", "direct")
    if census.get("status") != "ACCB_LAYER_B_HYBRID_CENSUS_COMPLETE":
        raise live.ExecutionError("fresh hybrid census did not complete")
    if census.get("execution_admission_sha") != live.EXECUTION_ADMISSION_SHA:
        raise live.ExecutionError("fresh census admission SHA mismatch")
    routes = live.route_map(census)
    route = routes[MODEL]
    guard = live.cell_guard(route, ANCHOR)
    if guard <= 0 or guard > max_budget:
        raise live.ExecutionError(f"single-cell guard not admitted: {guard}")

    frozen = payload.load_frozen_artifacts(args.architecture_root)
    row = {
        "model": MODEL,
        "nominal_anchor": ANCHOR,
        "request_text_bytes": materialized["request_text_bytes"],
        "request_text_characters": materialized["request_text_characters"],
        "payload_sha256": materialized["payload_sha256"],
        "context_sha256": materialized["context_sha256"],
        "target_critical_fact_positions": materialized["target_critical_fact_positions"],
        "provider_generation_attempts": 1,
        "silent_retries": 0,
        "fallbacks": False,
        "route": {
            "provider_name": route.get("provider_name"),
            "provider_tag": route.get("tag"),
            "transport": route.get("transport"),
        },
        "conservative_cell_guard_rub": round(guard, 6),
    }
    status = "RECOVERY_INCOMPLETE"
    with tempfile.TemporaryDirectory(prefix="accb-layer-b-recovery-") as root:
        try:
            provider = live.routerai_call(api_key, MODEL, route, materialized)
            row["provider"] = {k: v for k, v in provider.items() if k != "output_text"}
            if provider.get("status") != "PROVIDER_SUCCESS":
                row["status"] = str(provider.get("status") or "INTEGRATION_FAILURE_UNKNOWN")
                row["measurement_status"] = "NO_VALID_PROVIDER_RESPONSE"
                row["ACI"] = None
                row["ACI_min"] = None
                row["critical_failures"] = []
                status = "RECOVERY_TERMINAL_INTEGRATION"
            else:
                prompt_tokens = provider.get("provider_input_tokens")
                completion_tokens = provider.get("provider_output_tokens")
                row["L_model_input_provider"] = prompt_tokens
                row["provider_input_usage_source"] = provider.get("provider_input_usage_source")
                row["provider_output_tokens"] = completion_tokens
                row["measurement_status"] = provider.get("measurement_status")
                if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                    cost = live.routerai_cost(route, prompt_tokens, completion_tokens)
                    source = "fresh_routerai_pricing_x_provider_usage"
                else:
                    cost = guard
                    source = "conservative_cell_guard_due_usage_degraded"
                row["accounted_cost_rub_guard"] = round(cost, 6)
                row["accounted_cost_source"] = source

                text = str(provider["output_text"])
                candidate = live.extract_json_object(text)
                score = live.score_candidate(
                    frozen,
                    candidate,
                    Path(root),
                    "deepseek-medium-recovery",
                )
                row["candidate_trace_sha256"] = live.sha256_text(
                    json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
                row["status"] = "RECOVERED_SINGLE_CELL"
                row["ACI"] = score.get("ACI")
                row["ACI_min"] = score.get("ACI_min")
                row["critical_failures"] = score.get("critical_failures") or []
                row["metrics"] = score.get("metrics") or {}
                status = "RECOVERY_COMPLETE"
        except Exception as exc:
            row["status"] = "HARNESS_FAILURE"
            row["measurement_status"] = "HARNESS_FAILURE"
            row["error_type"] = type(exc).__name__
            row["error_message_sha256"] = live.sha256_text(str(exc))
            row["failure_stage"] = "single_cell_provider_or_scoring_harness"
            row["ACI"] = None
            row["ACI_min"] = None
            row["critical_failures"] = []

    result = {
        "schema_version": "0.1-single-cell-recovery",
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site_auditor_sha": site_sha,
        "frozen_architecture_sha": live.FROZEN_ARCHITECTURE_SHA,
        "execution_admission_sha": live.EXECUTION_ADMISSION_SHA,
        "experiment_id": "ACCB-LAYER-B-DIAGNOSTIC-v0.3",
        "recovery_id": "ACCB-LAYER-B-RECOVERY-DEEPSEEK-MEDIUM-v0.1",
        "source_failed_run_id": SOURCE_FAILED_RUN_ID,
        "recovery_reason": "replace exactly one missing DeepSeek medium cell after harness failure; preserve the other 14 scored cells",
        "primary_cross_model_input_axis": "request_text_bytes",
        "provider_input_tokens_role": "secondary model-specific tokenizer/provider-framing telemetry",
        "planned_recovery_cells": 1,
        "provider_generation_attempts_total": 1,
        "silent_retries": 0,
        "fallbacks": False,
        "fresh_single_cell_guard_rub": round(guard, 6),
        "max_recovery_budget_rub": max_budget,
        "cell": row,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "model": MODEL,
        "request_text_bytes": EXPECTED_BYTES,
        "payload_sha256": EXPECTED_PAYLOAD_SHA256,
        "ACI": row.get("ACI"),
        "ACI_min": row.get("ACI_min"),
        "guard_rub": round(guard, 6),
        "accounted_cost_rub_guard": row.get("accounted_cost_rub_guard"),
    }, sort_keys=True))
    return 0 if status in {"RECOVERY_COMPLETE", "RECOVERY_TERMINAL_INTEGRATION"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
