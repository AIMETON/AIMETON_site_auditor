#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import accb_layer_b_common_payload_preflight as common
import accb_layer_b_hybrid_census as hybrid
import accb_layer_b_payload as payload
import openrouter_proxy_client as openrouter

ROUTERAI_BASE_URL = "https://routerai.ru/api/v1"
ROUTERAI_MODELS = tuple(hybrid.ROUTERAI_MODELS)
SOL_MODEL = hybrid.SOL_MODEL
ALL_MODELS = ROUTERAI_MODELS + (SOL_MODEL,)
ANCHORS = tuple(payload.FROZEN_ANCHORS)
MAX_OUTPUT_TOKENS = 8192
OWNER_CEILING_RUB = 10_000.0
EXECUTION_ADMISSION_SHA = "363f69971ed82ce3e4fc5ea9716652e57e83118e"
FROZEN_ARCHITECTURE_SHA = hybrid.FROZEN_ARCHITECTURE_SHA
EXPERIMENT_ID = "ACCB-LAYER-B-DIAGNOSTIC-v0.3"


class ExecutionError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ExecutionError(f"missing required environment variable: {name}")
    return value


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            raise ExecutionError("model output does not contain a JSON object")
        value = json.loads(clean[start:end + 1])
    if not isinstance(value, dict):
        raise ExecutionError("model output JSON is not an object")
    return value


def materialize_common_payloads(architecture_root: Path) -> dict[int, dict[str, Any]]:
    common_receipt = common.build_report(architecture_root)
    if common_receipt.get("status") != "ACCB_LAYER_B_COMMON_PAYLOAD_READY":
        raise ExecutionError("common payload preflight is not ready")
    if common_receipt.get("execution_admission_sha") != EXECUTION_ADMISSION_SHA:
        raise ExecutionError("common payload admission SHA mismatch")
    frozen = payload.load_frozen_artifacts(architecture_root)
    schedule = json.loads(common.SCHEDULE_PATH.read_text(encoding="utf-8"))
    result: dict[int, dict[str, Any]] = {}
    for spec in schedule["anchors"]:
        anchor = int(spec["nominal_anchor"])
        logical = int(spec["logical_context_tokens"])
        system_text, user_text, manifest, seed, digest = payload.build_payload(
            frozen.scenario,
            frozen.trace_schema,
            nominal_anchor=anchor,
            logical_context_tokens=logical,
        )
        request_text = payload.local_count_text(system_text, user_text)
        request_bytes = len(request_text.encode("utf-8"))
        request_chars = len(request_text)
        request_sha = sha256_text(request_text)
        if request_bytes != int(spec["expected_request_text_bytes"]):
            raise ExecutionError(f"common request byte drift at anchor {anchor}")
        if request_sha != spec["expected_payload_sha256"]:
            raise ExecutionError(f"common payload hash drift at anchor {anchor}")
        if manifest.get("context_sha256") != spec["expected_context_sha256"]:
            raise ExecutionError(f"common context hash drift at anchor {anchor}")
        result[anchor] = {
            "system_text": system_text,
            "user_text": user_text,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            "request_text": request_text,
            "logical_context_tokens": logical,
            "payload_sha256": request_sha,
            "request_text_bytes": request_bytes,
            "request_text_characters": request_chars,
            "context_sha256": manifest.get("context_sha256"),
            "target_critical_fact_positions": manifest.get("target_critical_fact_positions"),
            "assembly_seed_u32": seed,
            "assembly_seed_derivation_sha256": digest,
        }
    if tuple(result) != ANCHORS:
        raise ExecutionError("common payload anchor schedule mismatch")
    return result


def post_routerai(api_key: str, body: dict[str, Any], timeout: int = 900) -> tuple[dict[str, Any], float]:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        ROUTERAI_BASE_URL + "/chat/completions",
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "aimeton-accb-layer-b-execution-v02",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "_integration_status": "INTEGRATION_FAILURE_HTTP",
            "_http_status": exc.code,
            "_body_bytes": len(raw),
            "_body_sha256": hashlib.sha256(raw).hexdigest(),
        }, time.monotonic() - started
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        return {
            "_integration_status": "TRANSPORT_FAILURE",
            "_reason_type": type(reason).__name__,
        }, time.monotonic() - started
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {
            "_integration_status": "INTEGRATION_FAILURE_INVALID_JSON",
            "_http_status": status,
            "_body_bytes": len(raw),
            "_body_sha256": hashlib.sha256(raw).hexdigest(),
        }, time.monotonic() - started
    if not isinstance(value, dict):
        raise ExecutionError("RouterAI response is not an object")
    return value, time.monotonic() - started


def usage_pair(body: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    p = usage.get("prompt_tokens")
    c = usage.get("completion_tokens")
    prompt = p if isinstance(p, int) and not isinstance(p, bool) and p >= 0 else None
    completion = c if isinstance(c, int) and not isinstance(c, bool) and c >= 0 else None
    return prompt, completion, "usage.prompt_tokens" if prompt is not None else None


def routerai_output_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ExecutionError("RouterAI response has no choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ExecutionError("RouterAI response has no message")
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise ExecutionError("RouterAI response content is empty")
    return text.strip()


def routerai_call(api_key: str, model: str, route: dict[str, Any], materialized: dict[str, Any]) -> dict[str, Any]:
    if route.get("transport") != "chat":
        raise ExecutionError(f"selected RouterAI transport is not chat for {model}")
    tag = str(route.get("tag") or "").strip()
    if not tag:
        raise ExecutionError(f"selected RouterAI route has no tag for {model}")
    body = {
        "model": model,
        "messages": materialized["messages"],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "provider": {"only": [tag], "allow_fallbacks": False},
    }
    response, elapsed = post_routerai(api_key, body)
    if response.get("_integration_status"):
        return {
            "status": response["_integration_status"],
            "transport": "routerai-chat",
            "provider_tag": tag,
            "elapsed_seconds": round(elapsed, 6),
            "http_status": response.get("_http_status"),
            "response_body_bytes": response.get("_body_bytes"),
            "response_body_sha256": response.get("_body_sha256"),
            "failure_reason_type": response.get("_reason_type"),
        }
    prompt_tokens, completion_tokens, usage_source = usage_pair(response)
    return {
        "status": "PROVIDER_SUCCESS",
        "transport": "routerai-chat",
        "provider_tag": tag,
        "elapsed_seconds": round(elapsed, 6),
        "provider_input_tokens": prompt_tokens,
        "provider_output_tokens": completion_tokens,
        "provider_input_usage_source": usage_source,
        "measurement_status": (
            "MEASURED_PROVIDER_INPUT_COUNT"
            if prompt_tokens is not None
            else "MEASUREMENT_DEGRADED_NO_PROVIDER_INPUT_COUNT"
        ),
        "output_text": routerai_output_text(response),
    }


def find_int(value: Any, names: set[str]) -> tuple[int | None, str | None]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in names and isinstance(child, int) and not isinstance(child, bool) and child >= 0:
                return child, key
        for child in value.values():
            found, source = find_int(child, names)
            if found is not None:
                return found, source
    elif isinstance(value, list):
        for child in value:
            found, source = find_int(child, names)
            if found is not None:
                return found, source
    return None, None


def generation_readback(api_key: str, generation_id: str) -> dict[str, Any]:
    url = "https://openrouter.ai/api/v1/generation?id=" + urllib.parse.quote(generation_id, safe="")
    try:
        result = openrouter.authenticated_get(url=url, proxy="", api_key=api_key, total_timeout_seconds=90)
    except openrouter.CurlTransportError as exc:
        return {"status": "READBACK_TRANSPORT_FAILURE", "failure_class": exc.failure_class}
    if result.http_status != 200:
        return {"status": "READBACK_HTTP_FAILURE", "http_status": result.http_status}
    try:
        value = json.loads(result.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {"status": "READBACK_INVALID_JSON"}
    if not isinstance(value, dict):
        return {"status": "READBACK_NON_OBJECT"}
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    p = data.get("native_tokens_prompt")
    c = data.get("native_tokens_completion")
    raw_cost = data.get("total_cost") if data.get("total_cost") is not None else data.get("cost")
    return {
        "status": "READBACK_COMPLETE",
        "provider_name": data.get("provider_name") or data.get("provider"),
        "native_tokens_prompt": p if isinstance(p, int) and not isinstance(p, bool) and p >= 0 else None,
        "native_tokens_completion": c if isinstance(c, int) and not isinstance(c, bool) and c >= 0 else None,
        "total_cost_usd": float(raw_cost)
        if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool)
        else None,
    }


def openrouter_call(api_key: str, materialized: dict[str, Any]) -> dict[str, Any]:
    request_body = openrouter.build_response_body(
        materialized["request_text"],
        max_output_tokens=MAX_OUTPUT_TOKENS,
        stream=False,
    )
    started = time.monotonic()
    try:
        result = openrouter.post_response(
            proxy="",
            api_key=api_key,
            body=request_body,
            total_timeout_seconds=1200,
        )
    except openrouter.CurlTransportError as exc:
        return {
            "status": "TRANSPORT_FAILURE",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "failure_class": exc.failure_class,
            "transport_metrics": exc.metrics,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
    elapsed = time.monotonic() - started
    if not 200 <= result.http_status < 300:
        return {
            "status": "INTEGRATION_FAILURE_HTTP",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "http_status": result.http_status,
            "response_body_bytes": len(result.body),
            "response_body_sha256": hashlib.sha256(result.body).hexdigest(),
            "transport_metrics": result.metrics,
            "elapsed_seconds": round(elapsed, 6),
        }
    try:
        response = json.loads(result.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {
            "status": "INTEGRATION_FAILURE_INVALID_JSON",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "transport_metrics": result.metrics,
            "elapsed_seconds": round(elapsed, 6),
        }
    if not isinstance(response, dict):
        raise ExecutionError("OpenRouter response is not an object")
    if response.get("error") not in (None, {}):
        return {
            "status": "INTEGRATION_FAILURE_PROVIDER_ERROR",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "transport_metrics": result.metrics,
            "elapsed_seconds": round(elapsed, 6),
        }
    prompt_tokens, prompt_source = find_int(response.get("usage"), {"input_tokens", "prompt_tokens"})
    completion_tokens, _ = find_int(response.get("usage"), {"output_tokens", "completion_tokens"})
    generation_id = openrouter.find_generation_id(response)
    readback = None
    if generation_id:
        readback = generation_readback(api_key, generation_id)
        if readback.get("status") == "READBACK_COMPLETE":
            if readback.get("native_tokens_prompt") is not None:
                prompt_tokens = readback["native_tokens_prompt"]
                prompt_source = "generation.native_tokens_prompt"
            if readback.get("native_tokens_completion") is not None:
                completion_tokens = readback["native_tokens_completion"]
    text = openrouter.extract_output_text(response)
    if not text:
        raise ExecutionError("OpenRouter response output text is empty")
    return {
        "status": "PROVIDER_SUCCESS",
        "transport": "openrouter-responses-direct",
        "provider_tag": "openai",
        "provider_input_tokens": prompt_tokens,
        "provider_output_tokens": completion_tokens,
        "provider_input_usage_source": prompt_source,
        "measurement_status": (
            "MEASURED_PROVIDER_INPUT_COUNT"
            if prompt_tokens is not None
            else "MEASUREMENT_DEGRADED_NO_PROVIDER_INPUT_COUNT"
        ),
        "generation_id_present": bool(generation_id),
        "generation_readback": readback,
        "transport_metrics": result.metrics,
        "elapsed_seconds": round(elapsed, 6),
        "output_text": text,
    }


def score_candidate(frozen: payload.FrozenArtifacts, candidate: dict[str, Any], temp_root: Path, stem: str) -> dict[str, Any]:
    trace_path = temp_root / f"{stem}.trace.json"
    score_path = temp_root / f"{stem}.score.json"
    trace_path.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(frozen.scorer_path), "--scenario", str(frozen.scenario_path), "--trace", str(trace_path), "--output", str(score_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if not score_path.is_file():
        raise ExecutionError("scorer produced no output; stderr_sha256=" + hashlib.sha256(proc.stderr.encode("utf-8")).hexdigest())
    value = json.loads(score_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionError("scorer output is not an object")
    return value


def route_map(census: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = census.get("routerai_models")
    if not isinstance(rows, list):
        raise ExecutionError("hybrid census RouterAI rows missing")
    result = {row["model"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("model"), str)}
    if set(result) != set(ROUTERAI_MODELS):
        raise ExecutionError("hybrid census RouterAI model mismatch")
    return result


def cell_guard(route: dict[str, Any], anchor: int) -> float:
    estimate = route.get("estimate")
    anchors = estimate.get("anchors") if isinstance(estimate, dict) else None
    row = anchors.get(str(anchor)) if isinstance(anchors, dict) else None
    if not isinstance(row, dict):
        raise ExecutionError(f"missing cell guard for anchor {anchor}")
    raw = row.get("estimated_cost_rub")
    if raw is None:
        raw = row.get("estimated_cost_rub_guard")
    value = float(raw)
    if value <= 0:
        raise ExecutionError("non-positive cell guard")
    return value


def routerai_cost(route: dict[str, Any], prompt_tokens: int, completion_tokens: int) -> float:
    pr, cr, _ = hybrid.router.conservative_rates(route, prompt_tokens)
    return prompt_tokens * pr + completion_tokens * cr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    site_sha = required_env("ACCB_EXPECTED_SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", site_sha):
        raise ExecutionError("ACCB_EXPECTED_SHA must be exact 40-char SHA")
    routerai_key = required_env("ROUTERAI_API_KEY")
    openrouter_key = required_env("OPENROUTER_API_KEY")
    max_budget_rub = float(required_env("ACCB_MAX_BUDGET_RUB"))
    if max_budget_rub != OWNER_CEILING_RUB:
        raise ExecutionError("ACCB_MAX_BUDGET_RUB must equal frozen owner ceiling 10000")

    common_payloads = materialize_common_payloads(args.architecture_root)
    frozen = payload.load_frozen_artifacts(args.architecture_root)
    census = hybrid.run("", "direct")
    guard = float(census.get("whole_tranche_conservative_guard_rub") or 0)
    if census.get("status") != "ACCB_LAYER_B_HYBRID_CENSUS_COMPLETE":
        raise ExecutionError("fresh hybrid census did not complete")
    if census.get("execution_admission_sha") != EXECUTION_ADMISSION_SHA:
        raise ExecutionError("fresh census admission SHA mismatch")
    if census.get("budget_admitted") is not True or guard <= 0 or guard > max_budget_rub:
        raise ExecutionError(f"fresh whole-tranche guard not admitted: {guard}")

    routes = route_map(census)
    sol_route = census.get("openrouter_sol")
    if not isinstance(sol_route, dict):
        raise ExecutionError("fresh Sol route missing")

    result: dict[str, Any] = {
        "schema_version": "0.2",
        "experiment_id": EXPERIMENT_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site_auditor_sha": site_sha,
        "frozen_architecture_sha": FROZEN_ARCHITECTURE_SHA,
        "execution_admission_sha": EXECUTION_ADMISSION_SHA,
        "scenario_id": "ACCB-DEV-004",
        "scenario_version": "0.1",
        "nominal_anchors": list(ANCHORS),
        "planned_cells": 15,
        "one_scored_call_per_cell": True,
        "silent_retries": 0,
        "fallbacks": False,
        "tokenizer_required_at_execution": False,
        "primary_cross_model_input_axis": "request_text_bytes",
        "payload_identity_field": "payload_sha256",
        "provider_input_tokens_role": "secondary model-specific tokenizer/provider-framing telemetry",
        "primary_input_length_measurement": "request_text_bytes for cross-model comparison",
        "raw_prompt_retained": False,
        "raw_completion_retained": False,
        "raw_provider_reasoning_retained": False,
        "fresh_census": {
            "timestamp": census.get("timestamp"),
            "whole_tranche_conservative_guard_rub": guard,
            "owner_first_stage_ceiling_rub": census.get("owner_first_stage_ceiling_rub"),
            "budget_admitted": census.get("budget_admitted"),
            "routerai_four_model_total_rub": census.get("routerai_four_model_total_rub"),
            "openrouter_sol_total_rub_guard": census.get("openrouter_sol_total_rub_guard"),
            "sol_provider_pin": sol_route.get("provider_pin"),
            "sol_transport": sol_route.get("transport"),
        },
        "cells": [],
    }

    reserved_guard = 0.0
    accounted_guard = 0.0
    scored = 0
    measured = 0
    degraded = 0
    integration = 0
    unscorable = 0
    harness_failures = 0

    with tempfile.TemporaryDirectory(prefix="accb-layer-b-live-") as root:
        temp_root = Path(root)
        for model in ALL_MODELS:
            route = sol_route if model == SOL_MODEL else routes[model]
            for anchor in ANCHORS:
                materialized = common_payloads[anchor]
                guard_cell = cell_guard(route, anchor)
                if reserved_guard + guard_cell > max_budget_rub:
                    raise ExecutionError(f"cumulative guard exceeds owner ceiling before {model}@{anchor}")
                reserved_guard += guard_cell
                row: dict[str, Any] = {
                    "model": model,
                    "nominal_anchor": anchor,
                    "logical_context_tokens": materialized["logical_context_tokens"],
                    "payload_sha256": materialized["payload_sha256"],
                    "request_text_bytes": materialized["request_text_bytes"],
                    "request_text_characters": materialized["request_text_characters"],
                    "L_payload_local_estimate": None,
                    "tokenizer_identity_optional": None,
                    "context_sha256": materialized["context_sha256"],
                    "target_critical_fact_positions": materialized["target_critical_fact_positions"],
                    "assembly_seed_u32": materialized["assembly_seed_u32"],
                    "assembly_seed_derivation_sha256": materialized["assembly_seed_derivation_sha256"],
                    "conservative_cell_guard_rub": round(guard_cell, 6),
                    "provider_generation_attempts": 1,
                    "provider_seed_sent": False,
                    "route": {
                        "provider_name": route.get("provider_name"),
                        "provider_tag": "openai" if model == SOL_MODEL else route.get("tag"),
                        "transport": route.get("transport"),
                    },
                }
                try:
                    provider = openrouter_call(openrouter_key, materialized) if model == SOL_MODEL else routerai_call(routerai_key, model, route, materialized)
                    row["provider"] = {k: v for k, v in provider.items() if k != "output_text"}
                    if provider.get("status") != "PROVIDER_SUCCESS":
                        row["status"] = str(provider.get("status") or "INTEGRATION_FAILURE_UNKNOWN")
                        row["measurement_status"] = "NO_VALID_PROVIDER_RESPONSE"
                        row["ACI"] = None
                        row["ACI_min"] = None
                        row["critical_failures"] = []
                        integration += 1
                    else:
                        prompt_tokens = provider.get("provider_input_tokens")
                        completion_tokens = provider.get("provider_output_tokens")
                        row["L_model_input_provider"] = prompt_tokens
                        row["provider_input_usage_source"] = provider.get("provider_input_usage_source")
                        row["provider_output_tokens"] = completion_tokens
                        row["measurement_status"] = provider["measurement_status"]
                        if prompt_tokens is not None:
                            measured += 1
                        else:
                            degraded += 1

                        if model == SOL_MODEL:
                            readback = provider.get("generation_readback")
                            usd = readback.get("total_cost_usd") if isinstance(readback, dict) else None
                            if isinstance(usd, (int, float)) and usd >= 0:
                                cost = float(usd) * hybrid.OPENROUTER_USD_TO_RUB_GUARD_RATE
                                source = "generation_readback_usd_x_guard_fx"
                            elif isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                                pr = float(sol_route["guard_prompt_usd_per_token"])
                                cr = float(sol_route["guard_completion_usd_per_token"])
                                cost = (prompt_tokens * pr + completion_tokens * cr) * hybrid.OPENROUTER_USD_TO_RUB_GUARD_RATE
                                source = "fresh_openrouter_guard_rates_x_provider_usage"
                            else:
                                cost = guard_cell
                                source = "conservative_cell_guard_due_usage_degraded"
                        elif isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                            cost = routerai_cost(route, prompt_tokens, completion_tokens)
                            source = "fresh_routerai_pricing_x_provider_usage"
                        else:
                            cost = guard_cell
                            source = "conservative_cell_guard_due_usage_degraded"
                        accounted_guard += cost
                        row["accounted_cost_rub_guard"] = round(cost, 6)
                        row["accounted_cost_source"] = source

                        text = str(provider["output_text"])
                        try:
                            candidate = extract_json_object(text)
                        except Exception as exc:
                            row["status"] = "MODEL_OUTPUT_UNSCORABLE_JSON"
                            row["output_sha256"] = sha256_text(text)
                            row["error_type"] = type(exc).__name__
                            row["ACI"] = None
                            row["ACI_min"] = None
                            row["critical_failures"] = []
                            unscorable += 1
                        else:
                            score = score_candidate(frozen, candidate, temp_root, f"{model.replace('/', '__')}__{anchor}")
                            row["status"] = "SCORED" if prompt_tokens is not None else "SCORED_MEASUREMENT_DEGRADED_NO_PROVIDER_INPUT_COUNT"
                            row["candidate_trace_sha256"] = sha256_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                            row["ACI"] = score.get("ACI")
                            row["ACI_min"] = score.get("ACI_min")
                            row["critical_failures"] = score.get("critical_failures") or []
                            row["metrics"] = score.get("metrics") or {}
                            scored += 1
                except Exception as exc:
                    row["status"] = "HARNESS_FAILURE"
                    row["measurement_status"] = "HARNESS_FAILURE"
                    row["error_type"] = type(exc).__name__
                    row["error_message"] = str(exc)[:500]
                    row["ACI"] = None
                    row["ACI_min"] = None
                    row["critical_failures"] = []
                    harness_failures += 1
                result["cells"].append(row)

    result["scored_cells"] = scored
    result["measured_provider_input_cells"] = measured
    result["measurement_degraded_cells"] = degraded
    result["terminal_integration_cells"] = integration
    result["unscorable_model_output_cells"] = unscorable
    result["harness_failure_cells"] = harness_failures
    result["completed_cells"] = len(result["cells"])
    result["accounted_spend_rub_guard"] = round(accounted_guard, 6)
    result["reserved_full_tranche_guard_rub"] = round(reserved_guard, 6)
    result["completion_criterion_met"] = (
        len(result["cells"]) == 15
        and scored + integration == 15
        and unscorable == 0
        and harness_failures == 0
    )
    result["status"] = "ACCB_LAYER_B_EXECUTION_COMPLETE" if result["completion_criterion_met"] else "ACCB_LAYER_B_EXECUTION_INCOMPLETE"
    result["scientific_boundary"] = "Diagnostic calibration only; no universal minimum-context threshold, universal ranking, or confirmatory reproducibility claim."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "completed_cells": result["completed_cells"],
        "scored_cells": scored,
        "measured_provider_input_cells": measured,
        "measurement_degraded_cells": degraded,
        "terminal_integration_cells": integration,
        "fresh_guard_rub": guard,
        "accounted_spend_rub_guard": round(accounted_guard, 6),
    }, sort_keys=True))
    return 0 if result["completion_criterion_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
