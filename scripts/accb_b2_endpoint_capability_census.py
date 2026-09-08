#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROUTERAI_BASE = "https://routerai.ru/api/v1"
OPENROUTER_SOL_ENDPOINTS = "https://openrouter.ai/api/v1/models/openai/gpt-5.6-sol/endpoints"
ROUTERAI_MODELS = (
    "z-ai/glm-5.2",
    "deepseek/deepseek-v4-pro-0813",
    "qwen/qwen3.7-plus",
    "moonshotai/kimi-k3",
)
SOL_MODEL = "openai/gpt-5.6-sol"
B2_TARGET_BYTES = (32768, 65536, 143934, 575367, 2297725)
MIN_CONTEXT_TOKENS = 1_000_000
# Budget planning only, not a scientific input axis and not a tokenizer claim.
# The historical B1 maximum observed density was ~0.31 provider tokens/byte.
# 0.50 is a deliberately conservative planning envelope for B2 cost estimation.
INPUT_TOKENS_PER_BYTE_GUARD = 0.50
USD_TO_RUB_GUARD = 500.0


class CensusError(RuntimeError):
    pass


def get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "aimeton-accb-b2-capability-census"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise CensusError(
            f"GET failed HTTP {exc.code}; bytes={len(body)}; sha256={hashlib.sha256(body).hexdigest()}"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        raise CensusError(f"GET transport failure: {type(reason).__name__}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CensusError(
            f"GET returned invalid JSON; bytes={len(raw)}; sha256={hashlib.sha256(raw).hexdigest()}"
        ) from exc
    if not isinstance(value, dict):
        raise CensusError("GET returned non-object")
    return value


def endpoints(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data")
    rows = data.get("endpoints") if isinstance(data, dict) else body.get("endpoints")
    if not isinstance(rows, list):
        raise CensusError("endpoint list missing")
    return [row for row in rows if isinstance(row, dict)]


def positive_float(raw: Any, label: str) -> float:
    if isinstance(raw, bool) or raw is None:
        raise CensusError(f"missing price {label}")
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise CensusError(f"invalid price {label}")
    return value


def safe_observation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_name": str(row.get("provider_name") or row.get("provider") or "")[:120],
        "tag": str(row.get("tag") or "")[:120],
        "status": row.get("status") if isinstance(row.get("status"), (int, str)) else None,
        "context_length": row.get("context_length") if isinstance(row.get("context_length"), int) else None,
        "max_prompt_tokens": row.get("max_prompt_tokens") if isinstance(row.get("max_prompt_tokens"), int) else None,
        "max_completion_tokens": row.get("max_completion_tokens") if isinstance(row.get("max_completion_tokens"), int) else None,
        "supported_apis": [str(x) for x in (row.get("supported_apis") or []) if isinstance(x, str)][:16],
        "supported_parameters": [str(x) for x in (row.get("supported_parameters") or []) if isinstance(x, str)][:64],
        "pricing_present": isinstance(row.get("pricing"), dict),
    }


def routerai_candidate(model: str, body: dict[str, Any]) -> dict[str, Any]:
    candidates: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
    observations: list[dict[str, Any]] = []
    for row in endpoints(body):
        obs = safe_observation(row)
        observations.append(obs)
        tag = str(row.get("tag") or "").strip()
        status = row.get("status")
        context = row.get("context_length")
        max_out = row.get("max_completion_tokens")
        apis = [str(x) for x in (row.get("supported_apis") or [])]
        pricing = row.get("pricing")
        if not tag or (isinstance(status, int) and status < 0):
            continue
        if "chat" not in apis and "responses" not in apis:
            continue
        if not isinstance(context, int) or context < MIN_CONTEXT_TOKENS:
            continue
        if not isinstance(max_out, int) or max_out <= 8192:
            continue
        if not isinstance(pricing, dict):
            continue
        prompt = positive_float(pricing.get("prompt"), f"{model}.{tag}.prompt")
        completion = positive_float(pricing.get("completion"), f"{model}.{tag}.completion")
        # Scientific preference: maximize non-binding output capacity first,
        # then context length; among equal-capacity routes prefer lower price.
        score = (max_out, context, -(prompt + completion))
        candidates.append((score, {
            "model": model,
            "provider_name": str(row.get("provider_name") or ""),
            "tag": tag,
            "transport": "chat" if "chat" in apis else "responses",
            "context_length": context,
            "max_prompt_tokens": row.get("max_prompt_tokens") if isinstance(row.get("max_prompt_tokens"), int) else None,
            "max_completion_tokens": max_out,
            "supported_parameters": sorted(str(x) for x in (row.get("supported_parameters") or [])),
            "pricing": {"prompt": prompt, "completion": completion},
            "variable_pricings": row.get("variable_pricings") if isinstance(row.get("variable_pricings"), list) else [],
        }))
    if not candidates:
        raise CensusError(f"no RouterAI endpoint with >=1M context and explicit max_completion_tokens >8192 for {model}")
    selected = max(candidates, key=lambda item: item[0])[1]
    selected["observed_endpoints"] = observations[:32]
    return selected


def is_openai_provider(row: dict[str, Any]) -> bool:
    label = " ".join(str(row.get(k) or "") for k in ("provider_name","provider","name","tag")).lower()
    return "openai" in label


def openrouter_sol_candidate(body: dict[str, Any]) -> dict[str, Any]:
    candidates: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
    observations: list[dict[str, Any]] = []
    for row in endpoints(body):
        obs = safe_observation(row)
        observations.append(obs)
        if not is_openai_provider(row):
            continue
        context = row.get("context_length")
        max_out = row.get("max_completion_tokens")
        pricing = row.get("pricing")
        if not isinstance(context, int) or context < MIN_CONTEXT_TOKENS:
            continue
        if not isinstance(max_out, int) or max_out <= 8192:
            continue
        if not isinstance(pricing, dict):
            continue
        prompt = positive_float(pricing.get("prompt"), "sol.prompt")
        completion = positive_float(pricing.get("completion"), "sol.completion")
        score = (max_out, context, -(prompt + completion))
        candidates.append((score, {
            "model": SOL_MODEL,
            "provider_name": str(row.get("provider_name") or row.get("provider") or ""),
            "tag": "openai",
            "transport": "responses",
            "context_length": context,
            "max_prompt_tokens": row.get("max_prompt_tokens") if isinstance(row.get("max_prompt_tokens"), int) else None,
            "max_completion_tokens": max_out,
            "supported_parameters": sorted(str(x) for x in (row.get("supported_parameters") or [])),
            "pricing_usd": {"prompt": prompt, "completion": completion},
        }))
    if not candidates:
        raise CensusError("no OpenAI-family Sol endpoint with >=1M context and explicit max_completion_tokens >8192")
    selected = max(candidates, key=lambda item: item[0])[1]
    selected["observed_endpoints"] = observations[:32]
    return selected


def routerai_rates(route: dict[str, Any], prompt_tokens: int) -> tuple[float, float]:
    prompt = float(route["pricing"]["prompt"])
    completion = float(route["pricing"]["completion"])
    for row in route.get("variable_pricings") or []:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        eligible = kind == "time-of-day"
        if kind == "prompt-threshold":
            raw = row.get("threshold")
            try:
                threshold = int(float(raw))
            except (TypeError, ValueError):
                continue
            eligible = prompt_tokens > threshold
        if not eligible:
            continue
        if row.get("prompt") is not None:
            prompt = max(prompt, positive_float(row.get("prompt"), "variable.prompt"))
        if row.get("completion") is not None:
            completion = max(completion, positive_float(row.get("completion"), "variable.completion"))
    return prompt, completion


def estimate_route(route: dict[str, Any], common_output_ceiling: int, *, usd: bool = False) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total = 0.0
    max_out = int(common_output_ceiling)
    if max_out <= 8192 or max_out > int(route["max_completion_tokens"]):
        raise CensusError("common output ceiling is invalid for selected route")
    for request_bytes in B2_TARGET_BYTES:
        prompt_guard = math.ceil(request_bytes * INPUT_TOKENS_PER_BYTE_GUARD)
        if usd:
            prompt_rate = float(route["pricing_usd"]["prompt"])
            completion_rate = float(route["pricing_usd"]["completion"])
            raw = prompt_guard * prompt_rate + max_out * completion_rate
            rub = raw * USD_TO_RUB_GUARD
        else:
            prompt_rate, completion_rate = routerai_rates(route, prompt_guard)
            raw = prompt_guard * prompt_rate + max_out * completion_rate
            rub = raw
        total += rub
        rows.append({
            "request_text_bytes": request_bytes,
            "planning_prompt_tokens_guard": prompt_guard,
            "max_output_tokens_admitted": max_out,
            "estimated_cost_rub_guard": round(rub, 6),
        })
    return {"tiers": rows, "model_total_rub_guard": round(total, 6)}


def run() -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    for model in ROUTERAI_MODELS:
        author, slug = model.split("/",1)
        body = get_json(f"{ROUTERAI_BASE}/models/{author}/{slug}/endpoints")
        route = routerai_candidate(model, body)
        routes.append(route)

    sol = openrouter_sol_candidate(get_json(OPENROUTER_SOL_ENDPOINTS))
    all_routes = routes + [sol]
    common_output_ceiling = min(int(x["max_completion_tokens"]) for x in all_routes)
    if common_output_ceiling <= 8192:
        raise CensusError("common output ceiling is not materially above legacy 8192")

    for route in routes:
        route["estimate"] = estimate_route(route, common_output_ceiling)
    sol["estimate"] = estimate_route(sol, common_output_ceiling, usd=True)

    whole = sum(float(x["estimate"]["model_total_rub_guard"]) for x in routes)
    whole += float(sol["estimate"]["model_total_rub_guard"])
    return {
        "schema_version": "0.1",
        "status": "ACCB_B2_ENDPOINT_CAPABILITY_CENSUS_COMPLETE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "http_methods": ["GET"],
        "provider_api_secrets_used": False,
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
        "legacy_global_8192_output_cap_allowed": False,
        "selection_policy": "select high-capacity endpoints, then use one common high ceiling across the five-model matrix",
        "common_output_ceiling_tokens": common_output_ceiling,
        "common_output_ceiling_rule": "minimum advertised max_completion_tokens across selected endpoints",
        "minimum_context_tokens": MIN_CONTEXT_TOKENS,
        "planning_input_tokens_per_byte_guard": INPUT_TOKENS_PER_BYTE_GUARD,
        "planning_input_guard_role": "budget estimate only; not tokenizer evidence or scientific x-axis",
        "routerai_routes": routes,
        "openrouter_sol": sol,
        "whole_25_cell_cost_rub_guard": round(whole, 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
