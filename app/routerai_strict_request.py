from __future__ import annotations

from app.research_execution import research_timed, operation_timeout

import asyncio
import json
import os
import re
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel

from app.llm_runtime_settings import LlmReasoningMode, LlmRole, resolve_llm_runtime
from app.research_control import deep_research_enabled, record_llm_start, record_llm_usage
from app.routerai_split_synthesis import (
    SplitSynthesisPhaseError,
    SplitSynthesisPhaseTimeout,
)


TModel = TypeVar("TModel", bound=BaseModel)
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


def _schema_name(phase: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", phase).strip("_")
    return (safe or "aimeton_structured_output")[:64]


@research_timed("llm")
async def request_json_strict(
    phase: str,
    model_type: type[TModel],
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
    reasoning_enabled: bool | None = None,
    reasoning_effort: ReasoningEffort | None = None,
) -> TModel:
    """Request provider-enforced JSON Schema output for a bounded split phase."""
    role = LlmRole.EXTRACTION if phase.startswith("profile_") else LlmRole.REASONING
    runtime = resolve_llm_runtime(role)
    if not runtime.configured:
        raise RuntimeError(f"llm_runtime_not_configured:{role.value}:{runtime.profile_name}")

    inherited_timeout = max(float(timeout_seconds), 120.0) if deep_research_enabled() else float(timeout_seconds)
    timeout_seconds = float(runtime.timeout_seconds or inherited_timeout)
    effective_max_tokens = min(int(max_tokens), int(runtime.max_tokens or max_tokens))
    output_mode = "strict_schema" if runtime.output_mode.value == "inherit" else runtime.output_mode.value
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

    record_llm_start(
        phase=phase,
        provider=runtime.provider,
        profile=runtime.profile_name,
        model=runtime.model,
    )
    payload = {
        "model": runtime.model,
        "temperature": 0.1 if runtime.temperature is None else runtime.temperature,
        "max_tokens": effective_max_tokens,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    if output_mode == "strict_schema":
        payload["structured_outputs"] = True

    explicit_off = reasoning_enabled is False
    if explicit_off or runtime.reasoning_mode is LlmReasoningMode.OFF:
        payload["reasoning"] = {"enabled": False}
    elif runtime.reasoning_mode is LlmReasoningMode.ON:
        reasoning: dict[str, bool | str] = {"enabled": True}
        effort = (
            runtime.reasoning_effort.value
            if runtime.reasoning_effort is not None
            else reasoning_effort
        )
        if effort is not None:
            reasoning["effort"] = effort
        payload["reasoning"] = reasoning
    else:
        reasoning: dict[str, bool | str] = {}
        if reasoning_enabled is not None:
            reasoning["enabled"] = reasoning_enabled
        effort = (
            runtime.reasoning_effort.value
            if runtime.reasoning_effort is not None
            else reasoning_effort
        )
        if effort is not None:
            reasoning["effort"] = effort
        if reasoning:
            payload["reasoning"] = reasoning

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{runtime.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {runtime.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        record_llm_usage(body)
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        content = choice["message"]["content"]
        result = model_type.model_validate(json.loads(content))
        record_llm_success(phase=phase)
        return result
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        record_llm_failure(phase=phase, error_type="timeout")
        raise SplitSynthesisPhaseTimeout(phase) from exc
    except RuntimeError as exc:
        record_llm_failure(phase=phase, error_type=type(exc).__name__)
        raise
    except Exception as exc:
        record_llm_failure(phase=phase, error_type=type(exc).__name__)
        raise SplitSynthesisPhaseError(phase, type(exc).__name__) from exc
