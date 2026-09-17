from __future__ import annotations

import asyncio
import json
import os
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.research_control import record_llm_start, record_llm_usage
from app.research_execution import operation_timeout, research_timed
from app.search_observer_models import observer_profile


TModel = TypeVar("TModel", bound=BaseModel)
DEFAULT_FAST_RESEARCH_PROFILE = "routerai-qwen35-9b"
DEFAULT_FAST_RESEARCH_TIMEOUT_SECONDS = 15.0


class FastResearchModelUnavailable(RuntimeError):
    pass


def _schema_name(phase: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", phase).strip("_")
    return (safe or "aimeton_fast_research")[:64]


def resolve_fast_research_model():
    profile_name = os.getenv(
        "AIMETON_FAST_RESEARCH_MODEL_PROFILE", DEFAULT_FAST_RESEARCH_PROFILE
    ).strip() or DEFAULT_FAST_RESEARCH_PROFILE
    try:
        model = observer_profile(profile_name).resolve()
    except KeyError as exc:
        raise FastResearchModelUnavailable(
            f"unknown_fast_research_profile:{profile_name}"
        ) from exc
    if not model.configured or not model.base_url or not model.api_key or not model.model:
        raise FastResearchModelUnavailable(
            f"fast_research_model_not_configured:{profile_name}"
        )
    return model


@research_timed("llm")
async def request_fast_json(
    phase: str,
    model_type: type[TModel],
    *,
    system: str,
    prompt: str,
    max_tokens: int = 1200,
    timeout_seconds: float = DEFAULT_FAST_RESEARCH_TIMEOUT_SECONDS,
    **_: object,
) -> TModel:
    """Run a small, non-reasoning structured call for research triage/classification.

    This path intentionally does not inherit deep-research 120s reasoning timeouts.
    Deep research may widen evidence depth, but triage remains a bounded O1 control-plane
    operation. Callers must implement a deterministic conservative fallback.
    """
    model = resolve_fast_research_model()
    timeout_seconds = min(30.0, max(2.0, float(timeout_seconds)))
    payload = {
        "model": model.model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "structured_outputs": True,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name(phase),
                "strict": True,
                "schema": model_type.model_json_schema(),
            },
        },
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }

    record_llm_start()
    try:
        async with httpx.AsyncClient(
            timeout=operation_timeout("llm", timeout_seconds)
        ) as client:
            response = await client.post(
                f"{model.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {model.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        record_llm_usage(body)
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise FastResearchModelUnavailable(f"{phase}:output_truncated")
        return model_type.model_validate(json.loads(choice["message"]["content"]))
    except FastResearchModelUnavailable:
        raise
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise FastResearchModelUnavailable(f"{phase}:timeout") from exc
    except Exception as exc:
        raise FastResearchModelUnavailable(
            f"{phase}:{type(exc).__name__}"
        ) from exc
