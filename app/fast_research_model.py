from __future__ import annotations

import asyncio
import json
import os
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.research_control import record_llm_failure, record_llm_start, record_llm_success, record_llm_usage
from app.research_execution import operation_timeout, research_timed
from app.llm_runtime_settings import LlmReasoningMode, LlmRole, resolve_llm_runtime


TModel = TypeVar("TModel", bound=BaseModel)
DEFAULT_FAST_RESEARCH_PROFILE = "routerai-qwen35-9b"
DEFAULT_FAST_RESEARCH_TIMEOUT_SECONDS = 15.0


class FastResearchModelUnavailable(RuntimeError):
    pass


def _schema_name(phase: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", phase).strip("_")
    return (safe or "aimeton_fast_research")[:64]


def resolve_fast_research_model():
    try:
        model = resolve_llm_runtime(LlmRole.FAST_RESEARCH)
    except Exception as exc:
        raise FastResearchModelUnavailable(type(exc).__name__) from exc
    if not model.configured or not model.base_url or not model.api_key or not model.model:
        raise FastResearchModelUnavailable(
            f"fast_research_model_not_configured:{model.profile_name}"
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
    timeout_seconds = min(30.0, max(2.0, float(model.timeout_seconds or timeout_seconds)))
    effective_max_tokens = min(int(max_tokens), int(model.max_tokens or max_tokens))
    output_mode = "strict_schema" if model.output_mode.value == "inherit" else model.output_mode.value
    if output_mode == "strict_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name(phase),
                "strict": True,
                "schema": model_type.model_json_schema(),
            },
        }
    else:
        response_format = {"type": "json_object"}
    payload = {
        "model": model.model,
        "temperature": 0.0 if model.temperature is None else model.temperature,
        "max_tokens": effective_max_tokens,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    if output_mode == "strict_schema":
        payload["structured_outputs"] = True
    if model.reasoning_mode is LlmReasoningMode.ON:
        reasoning = {"enabled": True}
        if model.reasoning_effort is not None:
            reasoning["effort"] = model.reasoning_effort.value
        payload["reasoning"] = reasoning
    else:
        payload["reasoning"] = {"enabled": False}

    record_llm_start(
        phase=phase, provider=model.provider, profile=model.profile_name, model=model.model
    )
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
        result = model_type.model_validate(json.loads(choice["message"]["content"]))
        record_llm_success(phase=phase)
        return result
    except FastResearchModelUnavailable as exc:
        record_llm_failure(phase=phase, error_type=type(exc).__name__)
        raise
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        record_llm_failure(phase=phase, error_type="timeout")
        raise FastResearchModelUnavailable(f"{phase}:timeout") from exc
    except Exception as exc:
        record_llm_failure(phase=phase, error_type=type(exc).__name__)
        raise FastResearchModelUnavailable(
            f"{phase}:{type(exc).__name__}"
        ) from exc
