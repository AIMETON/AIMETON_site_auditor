#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import accb_b2_endpoint_capability_census as census
import openrouter_proxy_client as openrouter

ROUTERAI_BASE_URL = "https://routerai.ru/api/v1"
ROUTERAI_MODELS = tuple(census.ROUTERAI_MODELS)
SOL_MODEL = census.SOL_MODEL
ALL_MODELS = ROUTERAI_MODELS + (SOL_MODEL,)

B2_ARCHITECTURE_SHA = "d390b2f56c0b2dae4be0cc4810dcb86403f2dc26"
OUTPUT_POLICY_SHA = "4c2ee2afbc85c6ae5fcabffac86bd33304273338"
REASONING_POLICY_SHA = "b44127abe77315b6ee50c6167e07d88f0116f726"
PROVIDER_MAX_POLICY_SHA = "f66e0aac76848879c212365b9b040f5ad5b7d605"
SNAPSHOT_ROOT = Path("docs/research/accb_b2_snapshot") / B2_ARCHITECTURE_SHA
GENERATOR_PATH = SNAPSHOT_ROOT / "generate_accb_b2_information_load.py"
SCORER_PATH = SNAPSHOT_ROOT / "score_accb_b2_trace.py"

EXPECTED_PAYLOADS = {
    "b2-32k": (32768, "e3cc52bfba4611e16cc66dc55a2d8439728b60cf238e53a920e47a4700e59a74"),
    "b2-64k": (65536, "1dd95208d3b82cb0258007394e4401fe01c5382a8c3c5801491b3eba64adb0f8"),
    "b2-140k": (143934, "6c3b07e447aa448205379256a2dc156c4b3c5d7efc2e5eccf92bdd3cc92ccd94"),
    "b2-562k": (575367, "1a37254ab02153759d26081b70f1b9c687ae00f1e075960a1c089e035d6df15d"),
    "b2-2191k": (2297725, "8f3ac74b87b2ed362045063a8f834492b835a09c55dd5853fca7e51c58d165a6"),
}
TIER_ORDER = tuple(EXPECTED_PAYLOADS)

GLM_MODEL = "z-ai/glm-5.2"
GLM_REASONING_EFFORT = "high"
GLM_THINKING_BUDGET = 32768

EXPERIMENT_ID = "ACCB-B2-GLM-REASONING-NORMALIZED-v0.1"


class ExecutionError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ExecutionError(f"missing required environment variable: {name}")
    return value


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _dynamic_import(path: Path, module_name: str):
    if not path.is_file():
        raise ExecutionError(f"missing frozen module: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ExecutionError(f"cannot load frozen module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_frozen_modules():
    return (
        _dynamic_import(GENERATOR_PATH, "accb_b2_frozen_generator"),
        _dynamic_import(SCORER_PATH, "accb_b2_frozen_scorer"),
    )


def load_recovery_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ExecutionError(f"recovery manifest missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionError("recovery manifest must be a JSON object")
    if value.get("schema_version") != "0.1":
        raise ExecutionError("recovery manifest schema_version drift")
    source_run_id = value.get("source_run_id")
    if not isinstance(source_run_id, int) or isinstance(source_run_id, bool) or source_run_id <= 0:
        raise ExecutionError("recovery manifest source_run_id invalid")
    source_sha = str(value.get("source_site_auditor_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ExecutionError("recovery manifest source_site_auditor_sha invalid")
    source_evidence_comment_id = value.get("source_evidence_comment_id")
    if (
        not isinstance(source_evidence_comment_id, int)
        or isinstance(source_evidence_comment_id, bool)
        or source_evidence_comment_id <= 0
    ):
        raise ExecutionError("recovery manifest source_evidence_comment_id invalid")
    if value.get("provider_max_policy_sha") != PROVIDER_MAX_POLICY_SHA:
        raise ExecutionError("recovery manifest provider_max_policy_sha drift")
    cells = value.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ExecutionError("recovery manifest cells missing")
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for row in cells:
        if not isinstance(row, dict):
            raise ExecutionError("recovery manifest cell must be object")
        model = str(row.get("model") or "")
        tier_id = str(row.get("tier_id") or "")
        reason = str(row.get("reason") or "")
        if model not in ALL_MODELS or tier_id not in TIER_ORDER:
            raise ExecutionError(f"invalid recovery cell: {model}/{tier_id}")
        key = (model, tier_id)
        if key in seen:
            raise ExecutionError(f"duplicate recovery cell: {model}/{tier_id}")
        if not reason:
            raise ExecutionError(f"recovery reason missing: {model}/{tier_id}")
        seen.add(key)
        result.append({"model": model, "tier_id": tier_id, "reason": reason})
    return result


def extract_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    clean = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*\x60\x60\x60$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            raise ExecutionError("model output does not contain a JSON object")
        try:
            value = json.loads(clean[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ExecutionError("model output JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ExecutionError("model output JSON is not an object")
    return value


def build_tiers(generator) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tier_id, (expected_bytes, expected_sha) in EXPECTED_PAYLOADS.items():
        tier = generator.build_tier(tier_id, expected_bytes)
        request_text = str(tier["request_text"])
        observed_bytes = len(request_text.encode("utf-8"))
        observed_sha = sha256_text(request_text)
        if observed_bytes != expected_bytes:
            raise ExecutionError(f"B2 exact byte drift at {tier_id}")
        if observed_sha != expected_sha:
            raise ExecutionError(f"B2 payload hash drift at {tier_id}")
        proof = generator.validate_semantic_consequence(tier)
        if proof.get("all_authoritative_events_consequential") is not True:
            raise ExecutionError(f"B2 semantic consequence drift at {tier_id}")
        result[tier_id] = tier
    if tuple(result) != TIER_ORDER:
        raise ExecutionError("B2 tier order drift")
    return result


def split_system_user(generator, request_text: str) -> tuple[str, str]:
    prefix = generator.SYSTEM_TEXT + "\n\n"
    if not request_text.startswith(prefix):
        raise ExecutionError("B2 request no longer begins with frozen system text")
    return generator.SYSTEM_TEXT, request_text[len(prefix):]


def post_routerai(api_key: str, body: dict[str, Any], timeout: int = 2400) -> tuple[dict[str, Any], float]:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        ROUTERAI_BASE_URL + "/chat/completions",
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "aimeton-accb-b2-live-v01",
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
            "_body_sha256": sha256_bytes(raw),
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
            "_body_sha256": sha256_bytes(raw),
        }, time.monotonic() - started
    if not isinstance(value, dict):
        return {
            "_integration_status": "INTEGRATION_FAILURE_NON_OBJECT",
            "_http_status": status,
            "_body_bytes": len(raw),
            "_body_sha256": sha256_bytes(raw),
        }, time.monotonic() - started
    return value, time.monotonic() - started


def _usage_int(usage: Any, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _nested_usage_int(usage: Any, parents: tuple[str, ...], keys: tuple[str, ...]) -> int | None:
    cur = usage
    for parent in parents:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(parent)
    if not isinstance(cur, dict):
        return None
    for key in keys:
        value = cur.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def routerai_safe_response(response: dict[str, Any], common_ceiling: int) -> dict[str, Any]:
    usage = response.get("usage")
    prompt_tokens = _usage_int(usage, "prompt_tokens")
    completion_tokens = _usage_int(usage, "completion_tokens")
    reasoning_tokens = _nested_usage_int(
        usage, ("completion_tokens_details",), ("reasoning_tokens",)
    )
    if reasoning_tokens is None:
        reasoning_tokens = _nested_usage_int(
            usage, ("output_tokens_details",), ("reasoning_tokens",)
        )
    final_answer_tokens = None
    if completion_tokens is not None and reasoning_tokens is not None:
        final_answer_tokens = max(0, completion_tokens - reasoning_tokens)

    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    finish_reason = str(choice.get("finish_reason") or "")[:80] or None
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        content = "\n".join(parts)
    output_text = content.strip() if isinstance(content, str) else ""

    cap_hit = (
        finish_reason in {"length", "max_tokens"}
        or (completion_tokens is not None and completion_tokens >= common_ceiling)
        or (reasoning_tokens is not None and reasoning_tokens >= common_ceiling)
    )

    if not output_text:
        status = "OUTPUT_BUDGET_EXHAUSTED" if cap_hit else "INTEGRATION_FAILURE_EMPTY_FINAL_CONTENT"
    else:
        status = "PROVIDER_SUCCESS"

    return {
        "status": status,
        "provider_input_tokens": prompt_tokens,
        "provider_output_tokens": completion_tokens,
        "provider_reasoning_tokens": reasoning_tokens,
        "provider_final_answer_tokens": final_answer_tokens,
        "finish_reason": finish_reason,
        "output_budget_hit": cap_hit,
        "output_text": output_text,
    }


def routerai_call(
    api_key: str,
    model: str,
    route: dict[str, Any],
    *,
    system_text: str,
    user_text: str,
    common_ceiling: int,
) -> dict[str, Any]:
    if route.get("transport") != "chat":
        raise ExecutionError(f"selected RouterAI route is not chat for {model}")
    tag = str(route.get("tag") or "").strip()
    if not tag:
        raise ExecutionError(f"selected RouterAI route has no tag for {model}")
    params = set(str(x) for x in (route.get("supported_parameters") or []))
    if model != GLM_MODEL:
        raise ExecutionError("GLM reasoning rerun received non-GLM model")
    if "reasoning" not in params:
        raise ExecutionError("selected GLM route does not advertise reasoning support")
    if common_ceiling <= GLM_THINKING_BUDGET:
        raise ExecutionError("selected GLM endpoint leaves no final-answer reserve")
    max_tokens_sent = common_ceiling - GLM_THINKING_BUDGET
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": max_tokens_sent,
        "reasoning": {"effort": GLM_REASONING_EFFORT},
        "thinking_budget": GLM_THINKING_BUDGET,
        "provider": {"only": [tag], "allow_fallbacks": False},
    }
    if "temperature" in params:
        body["temperature"] = 0.0

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
            "reasoning_effort_sent": GLM_REASONING_EFFORT,
            "reasoning_budget_tokens_sent": GLM_THINKING_BUDGET,
            "max_output_tokens_sent_actual": max_tokens_sent,
            "provider_seed_sent": False,
        }
    safe = routerai_safe_response(response, common_ceiling)
    safe.update({
        "transport": "routerai-chat",
        "provider_tag": tag,
        "elapsed_seconds": round(elapsed, 6),
        "reasoning_effort_sent": GLM_REASONING_EFFORT,
        "reasoning_budget_tokens_sent": GLM_THINKING_BUDGET,
        "max_output_tokens_sent_actual": max_tokens_sent,
        "provider_seed_sent": False,
    })
    return safe


def _find_int(value: Any, names: set[str]) -> int | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in names and isinstance(child, int) and not isinstance(child, bool) and child >= 0:
                return child
        for child in value.values():
            found = _find_int(child, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_int(child, names)
            if found is not None:
                return found
    return None


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
    raw_cost = data.get("total_cost") if data.get("total_cost") is not None else data.get("cost")
    return {
        "status": "READBACK_COMPLETE",
        "provider_name": data.get("provider_name") or data.get("provider"),
        "native_tokens_prompt": data.get("native_tokens_prompt")
        if isinstance(data.get("native_tokens_prompt"), int)
        else None,
        "native_tokens_completion": data.get("native_tokens_completion")
        if isinstance(data.get("native_tokens_completion"), int)
        else None,
        "total_cost_usd": float(raw_cost)
        if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool)
        else None,
    }


def openrouter_sol_call(api_key: str, request_text: str, common_ceiling: int) -> dict[str, Any]:
    payload = {
        "model": SOL_MODEL,
        "input": request_text,
        "max_output_tokens": common_ceiling,
        "store": False,
        "provider": {
            "only": ["openai"],
            "order": ["openai"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
        },
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    started = time.monotonic()
    try:
        result = openrouter.post_response(
            proxy="",
            api_key=api_key,
            body=body,
            total_timeout_seconds=2400,
        )
    except openrouter.CurlTransportError as exc:
        return {
            "status": "TRANSPORT_FAILURE",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "failure_class": exc.failure_class,
            "transport_metrics": exc.metrics,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "reasoning_effort_sent": None,
            "provider_seed_sent": False,
        }
    elapsed = time.monotonic() - started
    if not 200 <= result.http_status < 300:
        return {
            "status": "INTEGRATION_FAILURE_HTTP",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "http_status": result.http_status,
            "response_body_bytes": len(result.body),
            "response_body_sha256": sha256_bytes(result.body),
            "transport_metrics": result.metrics,
            "elapsed_seconds": round(elapsed, 6),
            "reasoning_effort_sent": None,
            "provider_seed_sent": False,
        }
    try:
        response = json.loads(result.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {
            "status": "INTEGRATION_FAILURE_INVALID_JSON",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "elapsed_seconds": round(elapsed, 6),
            "reasoning_effort_sent": None,
            "provider_seed_sent": False,
        }
    if not isinstance(response, dict):
        return {
            "status": "INTEGRATION_FAILURE_NON_OBJECT",
            "transport": "openrouter-responses-direct",
            "provider_tag": "openai",
            "elapsed_seconds": round(elapsed, 6),
            "reasoning_effort_sent": None,
            "provider_seed_sent": False,
        }

    usage = response.get("usage")
    prompt_tokens = _find_int(usage, {"input_tokens", "prompt_tokens"})
    completion_tokens = _find_int(usage, {"output_tokens", "completion_tokens"})
    reasoning_tokens = _find_int(usage, {"reasoning_tokens"})
    final_answer_tokens = None
    if completion_tokens is not None and reasoning_tokens is not None:
        final_answer_tokens = max(0, completion_tokens - reasoning_tokens)

    generation_id = openrouter.find_generation_id(response)
    readback = generation_readback(api_key, generation_id) if generation_id else None
    if isinstance(readback, dict) and readback.get("status") == "READBACK_COMPLETE":
        if readback.get("native_tokens_prompt") is not None:
            prompt_tokens = readback["native_tokens_prompt"]
        if readback.get("native_tokens_completion") is not None:
            completion_tokens = readback["native_tokens_completion"]
            if reasoning_tokens is not None:
                final_answer_tokens = max(0, completion_tokens - reasoning_tokens)

    output_text = openrouter.extract_output_text(response)
    response_status = str(response.get("status") or "")[:80] or None
    incomplete_details = response.get("incomplete_details")
    incomplete_reason = None
    if isinstance(incomplete_details, dict):
        incomplete_reason = str(incomplete_details.get("reason") or "")[:120] or None
    cap_hit = (
        response_status == "incomplete"
        or incomplete_reason in {"max_output_tokens", "max_tokens"}
        or (completion_tokens is not None and completion_tokens >= common_ceiling)
        or (reasoning_tokens is not None and reasoning_tokens >= common_ceiling)
    )
    if not output_text:
        status = "OUTPUT_BUDGET_EXHAUSTED" if cap_hit else "INTEGRATION_FAILURE_EMPTY_FINAL_CONTENT"
    else:
        status = "PROVIDER_SUCCESS"

    return {
        "status": status,
        "transport": "openrouter-responses-direct",
        "provider_tag": "openai",
        "provider_input_tokens": prompt_tokens,
        "provider_output_tokens": completion_tokens,
        "provider_reasoning_tokens": reasoning_tokens,
        "provider_final_answer_tokens": final_answer_tokens,
        "response_status": response_status,
        "incomplete_reason": incomplete_reason,
        "output_budget_hit": cap_hit,
        "generation_id_present": bool(generation_id),
        "generation_readback": readback,
        "transport_metrics": result.metrics,
        "elapsed_seconds": round(elapsed, 6),
        "output_text": output_text,
        "reasoning_effort_sent": None,
        "provider_seed_sent": False,
    }


def route_map(fresh: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = fresh.get("routerai_routes")
    if not isinstance(rows, list):
        raise ExecutionError("fresh B2 census RouterAI routes missing")
    result = {row["model"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("model"), str)}
    if set(result) != set(ROUTERAI_MODELS):
        raise ExecutionError("fresh B2 census RouterAI model mismatch")
    return result


def routerai_cost_rub(route: dict[str, Any], prompt_tokens: int, completion_tokens: int) -> float:
    prompt_rate, completion_rate = census.routerai_rates(route, prompt_tokens)
    return prompt_tokens * prompt_rate + completion_tokens * completion_rate


def provider_max_cell_guard(fresh: dict[str, Any], model: str, request_bytes: int) -> float:
    route = next(
        (r for r in list(fresh.get("routerai_routes") or []) + [fresh.get("openrouter_sol")]
         if isinstance(r, dict) and r.get("model") == model),
        None,
    )
    if not isinstance(route, dict):
        raise ExecutionError(f"missing fresh route for guard: {model}")
    endpoint_max = int(route.get("max_completion_tokens") or 0)
    if endpoint_max <= 0:
        raise ExecutionError(f"missing endpoint maximum for guard: {model}")
    estimate = census.estimate_route(route, endpoint_max, usd=(model == SOL_MODEL))
    rows = estimate.get("tiers") if isinstance(estimate, dict) else None
    if not isinstance(rows, list):
        raise ExecutionError(f"missing provider-max estimate rows: {model}")
    row = next((r for r in rows if int(r.get("request_text_bytes", -1)) == request_bytes), None)
    if not isinstance(row, dict):
        raise ExecutionError(f"missing provider-max cell guard: {model}/{request_bytes}")
    value = float(row["estimated_cost_rub_guard"])
    if value <= 0:
        raise ExecutionError("non-positive provider-max cell guard")
    return value


def score_candidate(scorer, gold: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = scorer.score(gold, candidate)
    if not isinstance(result, dict):
        raise ExecutionError("B2 scorer returned non-object")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    site_sha = required_env("ACCB_B2_EXPECTED_SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", site_sha):
        raise ExecutionError("ACCB_B2_EXPECTED_SHA must be exact 40-char SHA")
    routerai_key = required_env("ROUTERAI_API_KEY")
    openrouter_key = required_env("OPENROUTER_API_KEY")
    generator, scorer = load_frozen_modules()
    tiers = build_tiers(generator)
    recovery_cells = load_recovery_manifest(args.manifest)

    fresh = census.run()
    if fresh.get("status") != "ACCB_B2_ENDPOINT_CAPABILITY_CENSUS_COMPLETE":
        raise ExecutionError("fresh B2 endpoint census failed")
    fresh_common_guard = float(fresh.get("whole_25_cell_cost_rub_guard") or 0)
    routes = route_map(fresh)
    sol_route = fresh.get("openrouter_sol")
    if not isinstance(sol_route, dict):
        raise ExecutionError("fresh Sol route missing")

    result: dict[str, Any] = {
        "schema_version": "0.1",
        "experiment_id": EXPERIMENT_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site_auditor_sha": site_sha,
        "b2_architecture_sha": B2_ARCHITECTURE_SHA,
        "output_policy_sha": OUTPUT_POLICY_SHA,
        "reasoning_policy_sha": REASONING_POLICY_SHA,
        "provider_max_policy_sha": PROVIDER_MAX_POLICY_SHA,
        "scenario_id": generator.SCENARIO_ID,
        "scenario_version": generator.SCENARIO_VERSION,
        "primary_cross_model_input_axis": "request_text_bytes",
        "payload_identity_field": "payload_sha256",
        "provider_tokens_role": "secondary model/provider diagnostic telemetry only",
        "planned_models": sorted({row["model"] for row in recovery_cells}),
        "planned_tiers": sorted({row["tier_id"] for row in recovery_cells}, key=TIER_ORDER.index),
        "planned_cells": len(recovery_cells),
        "one_provider_generation_per_cell": True,
        "silent_retries": 0,
        "fallbacks": False,
        "compute_policy": "selected_endpoint_advertised_maximum_with_glm_reasoning_reserve",
        "common_output_ceiling_tokens": None,
        "legacy_global_8192_output_cap_allowed": False,
        "forced_low_reasoning_effort": False,
        "reasoning_policy": {
            "model": GLM_MODEL,
            "effort": GLM_REASONING_EFFORT,
            "thinking_budget_tokens": GLM_THINKING_BUDGET,
            "final_answer_reserve_policy": "max_tokens = endpoint_max - thinking_budget",
        },
        "raw_prompt_retained": False,
        "raw_completion_retained": False,
        "raw_provider_reasoning_retained": False,
        "fresh_census": {
            "status": fresh.get("status"),
            "whole_25_cell_common_128k_guard_rub_diagnostic_only": fresh_common_guard,
            "selection_policy": fresh.get("selection_policy"),
        },
        "cells": [],
    }

    accounted_spend = 0.0
    generation_attempts = 0

    with tempfile.TemporaryDirectory(prefix="aimeton-accb-b2-live-") as _:
        for selected in recovery_cells:
                model = selected["model"]
                tier_id = selected["tier_id"]
                tier = tiers[tier_id]
                request_text = str(tier["request_text"])
                request_bytes = int(tier["request_text_bytes"])
                cell_guard = provider_max_cell_guard(fresh, model, request_bytes)
                endpoint_route = routes[model] if model in ROUTERAI_MODELS else sol_route
                endpoint_ceiling = int(endpoint_route.get("max_completion_tokens") or 0)
                if endpoint_ceiling <= 0:
                    raise ExecutionError(f"selected endpoint maximum missing for {model}")
                max_tokens_sent = endpoint_ceiling - GLM_THINKING_BUDGET
                if max_tokens_sent <= 0:
                    raise ExecutionError("GLM endpoint maximum is not larger than frozen thinking budget")
                row: dict[str, Any] = {
                    "model": model,
                    "tier_id": tier_id,
                    "request_text_bytes": request_bytes,
                    "payload_sha256": tier["payload_sha256"],
                    "event_count": tier["event_count"],
                    "authoritative_transition_count": tier["authoritative_transition_count"],
                    "rejected_record_count": tier["rejected_record_count"],
                    "tier_entity_count": tier["tier_entity_count"],
                    "semantic_evidence_ratio": tier["semantic_evidence_ratio"],
                    "recovery_reason": selected["reason"],
                    "provider_generation_attempts": 1,
                    "silent_retries": 0,
                    "fallbacks": False,
                    "max_output_tokens_sent": max_tokens_sent,
                    "selected_endpoint_max_completion_tokens": endpoint_ceiling,
                    "reasoning_effort_sent": GLM_REASONING_EFFORT,
                    "reasoning_budget_tokens_sent": GLM_THINKING_BUDGET,
                    "provider_seed_sent": False,
                    "conservative_cell_guard_rub": round(cell_guard, 6),
                    "ACI_B2": None,
                    "ACI_B2_min": None,
                    "critical_failure_count": None,
                }
                generation_attempts += 1

                try:
                    if model in ROUTERAI_MODELS:
                        system_text, user_text = split_system_user(generator, request_text)
                        call = routerai_call(
                            routerai_key,
                            model,
                            routes[model],
                            system_text=system_text,
                            user_text=user_text,
                            common_ceiling=endpoint_ceiling,
                        )
                    else:
                        call = openrouter_sol_call(openrouter_key, request_text, endpoint_ceiling)

                    if call.get("status") == "OUTPUT_BUDGET_EXHAUSTED":
                        call["status"] = "MODEL_ENDPOINT_COMPUTE_LIMIT_REACHED"
                    row.update({
                        key: value for key, value in call.items()
                        if key != "output_text"
                    })
                    output_text = str(call.get("output_text") or "")

                    prompt_tokens = call.get("provider_input_tokens")
                    completion_tokens = call.get("provider_output_tokens")
                    actual_cost = None
                    if call.get("status") == "PROVIDER_SUCCESS":
                        if model in ROUTERAI_MODELS and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                            actual_cost = routerai_cost_rub(routes[model], prompt_tokens, completion_tokens)
                        elif model == SOL_MODEL:
                            rb = call.get("generation_readback")
                            if isinstance(rb, dict) and isinstance(rb.get("total_cost_usd"), (int, float)):
                                actual_cost = float(rb["total_cost_usd"]) * census.USD_TO_RUB_GUARD

                    if actual_cost is not None and actual_cost >= 0:
                        row["accounted_cost_rub"] = round(actual_cost, 6)
                        accounted_spend += actual_cost
                    else:
                        row["accounted_cost_rub"] = None
                        # A completed provider attempt without authoritative cost
                        # telemetry is guarded conservatively rather than converted
                        # to synthetic zero.
                        if generation_attempts:
                            accounted_spend += cell_guard
                            row["cost_accounting_mode"] = "conservative_cell_guard_due_usage_or_cost_degraded"

                    if call.get("status") != "PROVIDER_SUCCESS":
                        row["candidate_trace_sha256"] = None
                        result["cells"].append(row)
                        continue

                    try:
                        candidate = extract_json_object(output_text)
                    except BaseException as exc:
                        row["status"] = "MODEL_OUTPUT_CONTRACT_FAILURE"
                        row["ACI_B2"] = 0.0
                        row["ACI_B2_min"] = 0.0
                        row["critical_failure_count"] = 1
                        row["critical_failures"] = ["delivered final answer violated required JSON object contract"]
                        row["candidate_trace_sha256"] = sha256_text(output_text)
                        row["error_type"] = type(exc).__name__
                        row["error_message_sha256"] = sha256_text(str(exc))
                        result["cells"].append(row)
                        continue

                    canonical_candidate = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    row["candidate_trace_sha256"] = sha256_text(canonical_candidate)
                    score = score_candidate(scorer, tier["gold"], candidate)
                    row["status"] = "SCORED"
                    row["ACI_B2"] = score.get("ACI_B2")
                    row["ACI_B2_min"] = score.get("ACI_B2_min")
                    row["critical_failure_count"] = score.get("critical_failure_count")
                    row["critical_failures"] = score.get("critical_failures") or []
                    row["metrics"] = score.get("metrics") or {}
                    result["cells"].append(row)

                except BaseException as exc:
                    row["status"] = "HARNESS_FAILURE"
                    row["error_type"] = type(exc).__name__
                    row["error_message_sha256"] = sha256_text(str(exc))
                    row["candidate_trace_sha256"] = None
                    row["failure_stage"] = "cell_execution_or_scoring"
                    accounted_spend += cell_guard
                    row["accounted_cost_rub"] = None
                    row["cost_accounting_mode"] = "conservative_cell_guard_after_harness_failure"
                    result["cells"].append(row)

    cells = result["cells"]
    result["provider_generation_attempts_total"] = generation_attempts
    result["completed_cells"] = len(cells)
    result["scored_cells"] = sum(row.get("status") == "SCORED" for row in cells)
    result["model_output_contract_failure_cells"] = sum(
        row.get("status") == "MODEL_OUTPUT_CONTRACT_FAILURE" for row in cells
    )
    result["model_endpoint_compute_limit_cells"] = sum(
        row.get("status") == "MODEL_ENDPOINT_COMPUTE_LIMIT_REACHED" for row in cells
    )
    result["terminal_integration_cells"] = sum(
        str(row.get("status") or "").startswith("INTEGRATION_FAILURE")
        or row.get("status") == "TRANSPORT_FAILURE"
        or row.get("status") == "MODEL_ENDPOINT_COMPUTE_LIMIT_REACHED"
        for row in cells
    )
    result["harness_failure_cells"] = sum(row.get("status") == "HARNESS_FAILURE" for row in cells)
    result["accounted_spend_rub"] = round(accounted_spend, 6)
    result["completion_criterion_met"] = (
        len(cells) == len(recovery_cells)
        and generation_attempts == len(recovery_cells)
        and all(
            row.get("status") in {
                "SCORED",
                "MODEL_OUTPUT_CONTRACT_FAILURE",
                "MODEL_ENDPOINT_COMPUTE_LIMIT_REACHED",
                "INTEGRATION_FAILURE_HTTP",
                "INTEGRATION_FAILURE_INVALID_JSON",
                "INTEGRATION_FAILURE_NON_OBJECT",
                "INTEGRATION_FAILURE_EMPTY_FINAL_CONTENT",
                "TRANSPORT_FAILURE",
            }
            for row in cells
        )
    )
    result["status"] = (
        "ACCB_B2_GLM_REASONING_NORMALIZED_COMPLETE"
        if result["completion_criterion_met"]
        else "ACCB_B2_GLM_REASONING_NORMALIZED_INCOMPLETE"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": result["status"],
        "completed_cells": result["completed_cells"],
        "scored_cells": result["scored_cells"],
        "terminal_integration_cells": result["terminal_integration_cells"],
        "model_output_contract_failure_cells": result["model_output_contract_failure_cells"],
        "model_endpoint_compute_limit_cells": result["model_endpoint_compute_limit_cells"],
        "accounted_spend_rub": result["accounted_spend_rub"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["completion_criterion_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
