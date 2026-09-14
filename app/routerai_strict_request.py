from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel

from app.llm import BASE_URL, MODEL
from app.research_control import deep_research_enabled, record_llm_start, record_llm_usage
from app.routerai_split_synthesis import (
    SplitSynthesisPhaseError,
    SplitSynthesisPhaseTimeout,
)


TModel = TypeVar("TModel", bound=BaseModel)
ReasoningEffort = Literal["high", "xhigh"]


def _schema_name(phase: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", phase).strip("_")
    return (safe or "aimeton_structured_output")[:64]


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
    if deep_research_enabled():
        timeout_seconds = max(timeout_seconds, 120.0)
    key = os.getenv("ROUTERAI_API_KEY")
    if not key:
        raise RuntimeError("ROUTERAI_API_KEY не задан")

    record_llm_start()
    payload = {
        "model": MODEL,
        "temperature": 0.1,
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
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    reasoning: dict[str, bool | str] = {}
    if reasoning_enabled is not None:
        reasoning["enabled"] = reasoning_enabled
    if reasoning_effort is not None:
        reasoning["effort"] = reasoning_effort
    if reasoning:
        payload["reasoning"] = reasoning

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        record_llm_usage(body)
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        content = choice["message"]["content"]
        return model_type.model_validate(json.loads(content))
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise SplitSynthesisPhaseTimeout(phase) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise SplitSynthesisPhaseError(phase, type(exc).__name__) from exc
