#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import accb_routerai_readonly_census as router

ROUTERAI_MODELS = (
    "z-ai/glm-5.2",
    "deepseek/deepseek-v4-pro-0813",
    "qwen/qwen3.7-plus",
    "moonshotai/kimi-k3",
)
SOL_MODEL = "openai/gpt-5.6-sol"
OPENROUTER_ENDPOINTS_URL = (
    "https://openrouter.ai/api/v1/models/openai/gpt-5.6-sol/endpoints"
)
OPENROUTER_PROVIDER_PIN = "openai"
EXECUTION_ADMISSION_SHA = "67c8ea3e84405884136119d7252fe7424ccf1631"
FROZEN_ARCHITECTURE_SHA = "b47b937873ef980601b5c741af9b327fb18365bc"
OWNER_FIRST_STAGE_CEILING_RUB = 10_000.0
OPENROUTER_USD_TO_RUB_GUARD_RATE = 500.0


class HybridCensusError(RuntimeError):
    pass


def _positive(raw: Any, label: str) -> float:
    if isinstance(raw, bool) or raw is None:
        raise HybridCensusError(f"missing price: {label}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HybridCensusError(f"unparseable price: {label}") from exc
    if not math.isfinite(value) or value <= 0:
        raise HybridCensusError(f"non-positive price: {label}")
    return value


def _get_openrouter_json(proxy_url: str, transport_mode: str) -> dict[str, Any]:
    mode = transport_mode.strip().lower()
    if mode == "direct":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    elif mode == "http":
        parsed = urllib.parse.urlsplit(proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HybridCensusError("OpenRouter HTTP proxy URL missing/invalid")
        if parsed.username or parsed.password:
            raise HybridCensusError("credential-bearing proxy URLs are not admitted")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    else:
        raise HybridCensusError(f"unsupported OpenRouter transport mode: {mode!r}")
    request = urllib.request.Request(
        OPENROUTER_ENDPOINTS_URL,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "aimeton-accb-hybrid-census"},
    )
    try:
        with opener.open(request, timeout=90) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise HybridCensusError(
            f"OpenRouter endpoint census HTTP {exc.code}; "
            f"body_bytes={len(body)}; body_sha256={hashlib.sha256(body).hexdigest()}"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        raise HybridCensusError(
            f"OpenRouter endpoint census transport failure: {type(reason).__name__}"
        ) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HybridCensusError(
            f"OpenRouter endpoint census invalid JSON; bytes={len(raw)}; "
            f"sha256={hashlib.sha256(raw).hexdigest()}"
        ) from exc
    if not isinstance(value, dict):
        raise HybridCensusError("OpenRouter endpoint census returned non-object")
    return value


def _openrouter_endpoints(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data")
    if isinstance(data, dict):
        raw = data.get("endpoints")
    elif isinstance(data, list):
        raw = data
    else:
        raw = body.get("endpoints")
    if not isinstance(raw, list) or not raw:
        raise HybridCensusError("OpenRouter endpoint list missing/empty")
    return [x for x in raw if isinstance(x, dict)]


def _provider_label(endpoint: dict[str, Any]) -> str:
    for key in ("provider_name", "provider", "name", "tag"):
        value = endpoint.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_openai_family(endpoint: dict[str, Any]) -> bool:
    label = _provider_label(endpoint).lower()
    return "openai" in label


def _capacity_ok(endpoint: dict[str, Any]) -> bool:
    required_prompt = max(router.ANCHORS)
    required_context = required_prompt + router.MAX_OUTPUT_TOKENS
    context = endpoint.get("context_length")
    if not isinstance(context, int) or context < required_context:
        return False
    max_prompt = endpoint.get("max_prompt_tokens")
    if isinstance(max_prompt, int) and max_prompt < required_prompt:
        return False
    max_completion = endpoint.get("max_completion_tokens")
    if isinstance(max_completion, int) and max_completion < router.MAX_OUTPUT_TOKENS:
        return False
    status = endpoint.get("status")
    if isinstance(status, int) and status < 0:
        return False
    return True


def _price_candidates(endpoint: dict[str, Any]) -> tuple[list[float], list[float]]:
    pricing = endpoint.get("pricing")
    if not isinstance(pricing, dict):
        raise HybridCensusError("OpenRouter eligible endpoint has no pricing object")
    prompts = [_positive(pricing.get("prompt"), "openrouter.prompt")]
    completions = [_positive(pricing.get("completion"), "openrouter.completion")]
    variable = endpoint.get("variable_pricings")
    if variable is None:
        variable = endpoint.get("variable_pricing")
    if variable is not None:
        if not isinstance(variable, list):
            raise HybridCensusError("OpenRouter variable pricing has unknown shape")
        for index, row in enumerate(variable):
            if not isinstance(row, dict):
                raise HybridCensusError("OpenRouter variable pricing row is not object")
            if "prompt" in row:
                prompts.append(_positive(row["prompt"], f"openrouter.variable[{index}].prompt"))
            if "completion" in row:
                completions.append(
                    _positive(row["completion"], f"openrouter.variable[{index}].completion")
                )
    return prompts, completions


def select_openrouter_sol(body: dict[str, Any]) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for endpoint in _openrouter_endpoints(body):
        label = _provider_label(endpoint)
        family = _is_openai_family(endpoint)
        capacity = _capacity_ok(endpoint)
        pricing_present = isinstance(endpoint.get("pricing"), dict)
        observations.append(
            {
                "provider_label": label[:120],
                "openai_family": family,
                "capacity_ok": capacity,
                "context_length": endpoint.get("context_length")
                if isinstance(endpoint.get("context_length"), int)
                else None,
                "max_prompt_tokens": endpoint.get("max_prompt_tokens")
                if isinstance(endpoint.get("max_prompt_tokens"), int)
                else None,
                "max_completion_tokens": endpoint.get("max_completion_tokens")
                if isinstance(endpoint.get("max_completion_tokens"), int)
                else None,
                "pricing_present": pricing_present,
            }
        )
        if family and capacity and pricing_present:
            eligible.append(endpoint)
    if not eligible:
        raise HybridCensusError(
            "no OpenAI-family OpenRouter Sol endpoint admits 524288+8192"
        )

    prompt_rates: list[float] = []
    completion_rates: list[float] = []
    for endpoint in eligible:
        prompts, completions = _price_candidates(endpoint)
        prompt_rates.extend(prompts)
        completion_rates.extend(completions)
    prompt_rate = max(prompt_rates)
    completion_rate = max(completion_rates)

    anchors: dict[str, Any] = {}
    total_usd = 0.0
    for anchor in router.ANCHORS:
        cost_usd = anchor * prompt_rate + router.MAX_OUTPUT_TOKENS * completion_rate
        total_usd += cost_usd
        anchors[str(anchor)] = {
            "prompt_usd_per_token_guard": prompt_rate,
            "completion_usd_per_token_guard": completion_rate,
            "max_output_tokens": router.MAX_OUTPUT_TOKENS,
            "estimated_cost_usd_guard": round(cost_usd, 6),
            "estimated_cost_rub_guard": round(
                cost_usd * OPENROUTER_USD_TO_RUB_GUARD_RATE, 6
            ),
        }
    return {
        "model": SOL_MODEL,
        "provider_pin": OPENROUTER_PROVIDER_PIN,
        "transport": "openrouter-responses-via-http-proxy",
        "eligible_openai_family_endpoints": len(eligible),
        "observations": observations[:24],
        "guard_prompt_usd_per_token": prompt_rate,
        "guard_completion_usd_per_token": completion_rate,
        "usd_to_rub_budget_guard_rate": OPENROUTER_USD_TO_RUB_GUARD_RATE,
        "estimate": {
            "anchors": anchors,
            "model_total_usd_guard": round(total_usd, 6),
            "model_total_rub_guard": round(
                total_usd * OPENROUTER_USD_TO_RUB_GUARD_RATE, 6
            ),
        },
    }


def run(proxy_url: str, transport_mode: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    router_total = 0.0
    for model in ROUTERAI_MODELS:
        author, slug = model.split("/", 1)
        body = router.get_json(f"{router.BASE_URL}/models/{author}/{slug}/endpoints")
        endpoint = router.select_endpoint(model, body)
        estimate = router.price_endpoint(endpoint)
        router_total += float(estimate["model_total_rub"])
        rows.append(
            {
                **endpoint,
                "route": "routerai",
                "seed_advertised_fresh": "seed" in endpoint["supported_parameters"],
                "estimate": estimate,
            }
        )

    openrouter_body = _get_openrouter_json(proxy_url, transport_mode)
    sol = select_openrouter_sol(openrouter_body)
    sol["transport"] = f"openrouter-responses-via-{transport_mode.strip().lower()}"
    sol_rub = float(sol["estimate"]["model_total_rub_guard"])
    whole = router_total + sol_rub
    result = {
        "schema_version": "0.1-hybrid-execution-admission",
        "status": "ACCB_LAYER_B_HYBRID_CENSUS_COMPLETE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "frozen_architecture_sha": FROZEN_ARCHITECTURE_SHA,
        "execution_admission_sha": EXECUTION_ADMISSION_SHA,
        "anchors_tokens_nominal": list(router.ANCHORS),
        "max_output_tokens_per_cell": router.MAX_OUTPUT_TOKENS,
        "planned_cells": 15,
        "routerai_models": rows,
        "openrouter_sol": sol,
        "routerai_four_model_total_rub": round(router_total, 6),
        "openrouter_sol_total_rub_guard": round(sol_rub, 6),
        "whole_tranche_conservative_guard_rub": round(whole, 6),
        "owner_first_stage_ceiling_rub": OWNER_FIRST_STAGE_CEILING_RUB,
        "budget_admitted": whole <= OWNER_FIRST_STAGE_CEILING_RUB,
        "provider_generations_performed": 0,
        "paid_spend_authorized_by_this_census_rub": 0,
        "provider_api_secrets_used": False,
        "http_methods": ["GET"],
        "openrouter_proxy_value_retained": False,
        "tokenizer_preflight_required": False,
        "primary_input_length_measurement": "provider-reported usage after successful scored response",
        "pre_call_length_telemetry": ["nominal_anchor", "payload_sha256", "request_text_bytes", "request_text_characters"],
        "scientific_boundary": "Fresh route/capability/pricing admission only; no cognition score.",
    }
    if not result["budget_admitted"]:
        raise HybridCensusError(
            f"fresh hybrid tranche guard {whole:.6f} RUB exceeds owner ceiling"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openrouter-proxy-url", default="")
    parser.add_argument("--sol-transport-mode", choices=("direct", "http"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.openrouter_proxy_url, args.sol_transport_mode)
        rc = 0
    except BaseException as exc:
        raw = repr(exc).encode("utf-8", errors="replace")
        result = {
            "schema_version": "0.1-hybrid-execution-admission",
            "status": "ACCB_LAYER_B_HYBRID_CENSUS_FAILED",
            "frozen_architecture_sha": FROZEN_ARCHITECTURE_SHA,
            "execution_admission_sha": EXECUTION_ADMISSION_SHA,
            "provider_generations_performed": 0,
            "paid_spend_authorized_by_this_census_rub": 0,
            "provider_api_secrets_used": False,
            "error_type": type(exc).__name__,
            "safe_message": str(exc)[:1000]
            if isinstance(exc, (HybridCensusError, router.CensusError))
            else "unexpected internal census failure",
            "exception_repr_sha256": hashlib.sha256(raw).hexdigest(),
        }
        rc = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
