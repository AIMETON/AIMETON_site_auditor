from __future__ import annotations

from app.research_execution import research_timed, operation_timeout

import asyncio
import json
import os
import re
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.llm_runtime_settings import (
    LlmReasoningMode,
    LlmRole,
    effective_llm_output_mode,
    resolve_llm_runtime,
)
from app.research_control import (deep_research_enabled, record_llm_failure, record_llm_start, record_llm_success, record_llm_usage)
from app.routerai_split_synthesis import (
    SplitSynthesisPhaseError,
    SplitSynthesisPhaseTimeout,
)


TModel = TypeVar("TModel", bound=BaseModel)
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


def _schema_name(phase: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", phase).strip("_")
    return (safe or "aimeton_structured_output")[:64]


_RATE_LIMIT_MAX_RETRIES = 2
_RATE_LIMIT_BACKOFF_BASE_SECONDS = 2.0
_RATE_LIMIT_BACKOFF_MAX_SECONDS = 30.0


def _rate_limit_retry_delay(response: httpx.Response, retry_index: int) -> float:
    retry_after = (response.headers.get("Retry-After") or "").strip()
    if retry_after:
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            seconds = -1.0
        if seconds >= 0:
            return min(_RATE_LIMIT_BACKOFF_MAX_SECONDS, seconds)
    return min(
        _RATE_LIMIT_BACKOFF_MAX_SECONDS,
        _RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** retry_index),
    )


async def _post_with_rate_limit_retry(
    *,
    phase: str,
    runtime,
    payload: dict,
    timeout_seconds: float,
) -> httpx.Response:
    retries = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{runtime.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {runtime.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or retries >= _RATE_LIMIT_MAX_RETRIES:
                raise
            record_llm_failure(
                phase=phase,
                error_type="HTTPStatusError_429_retrying",
            )
            delay = _rate_limit_retry_delay(exc.response, retries)
            retries += 1
            if delay > 0:
                await asyncio.sleep(delay)
            record_llm_start(
                phase=phase,
                provider=runtime.provider,
                profile=runtime.profile_name,
                model=runtime.model,
            )


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
    """Request schema-validated JSON output using the provider-safe transport."""
    role = LlmRole.EXTRACTION if phase.startswith("profile_") else LlmRole.REASONING
    runtime = resolve_llm_runtime(role)
    if not runtime.configured:
        raise RuntimeError(f"llm_runtime_not_configured:{role.value}:{runtime.profile_name}")

    inherited_timeout = max(float(timeout_seconds), 120.0) if deep_research_enabled() else float(timeout_seconds)
    timeout_seconds = float(runtime.timeout_seconds or inherited_timeout)
    effective_max_tokens = min(int(max_tokens), int(runtime.max_tokens or max_tokens))
    output_mode = effective_llm_output_mode(runtime).value
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
    if output_mode == "strict_schema" and runtime.provider == "routerai":
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
        response = await _post_with_rate_limit_retry(
            phase=phase,
            runtime=runtime,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        body = response.json()
        record_llm_usage(body)
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        content = choice["message"]["content"]
        try:
            result = model_type.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as validation_exc:
            repair_phase = f"{phase}_schema_repair"
            schema = model_type.model_json_schema()
            if isinstance(validation_exc, ValidationError):
                errors = validation_exc.errors(include_url=False, include_context=False)
            else:
                errors = [{
                    "type": "json_decode_error",
                    "loc": [],
                    "msg": str(validation_exc),
                }]
            repair_prompt = (
                "Исправь ТОЛЬКО структуру предыдущего JSON-ответа по указанной JSON Schema. "
                "Не добавляй новые факты, источники, цифры, проблемы, решения или выводы. "
                "Сохрани все корректные значения исходного ответа. "
                "Если обязательного поля нет и его значение нельзя вывести из уже имеющихся полей, "
                "используй консервативное нейтральное значение, допустимое схемой: "
                "для обычной строки — 'Недостаточно данных', для массива — [], "
                "для числового score — 0, для qualification — 'Недостаточно данных', "
                "для source_ids — []. Не удаляй уже существующие доказательные поля. "
                "Верни только исправленный JSON без пояснений.\n\n"
                "VALIDATION ERRORS:\n"
                + json.dumps(errors, ensure_ascii=False, separators=(",", ":"))
                + "\n\nJSON SCHEMA:\n"
                + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                + "\n\nPREVIOUS JSON OUTPUT:\n"
                + str(content)[:24000]
            )
            repair_payload = {
                "model": runtime.model,
                "temperature": 0.0,
                "max_tokens": effective_max_tokens,
                "response_format": response_format,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты schema-repair модуль. Исправляй только форму JSON, "
                            "не выполняй бизнес-анализ повторно и не добавляй новые утверждения."
                        ),
                    },
                    {"role": "user", "content": repair_prompt},
                ],
                "reasoning": {"enabled": False},
            }
            if output_mode == "strict_schema" and runtime.provider == "routerai":
                repair_payload["structured_outputs"] = True
            record_llm_start(
                phase=repair_phase,
                provider=runtime.provider,
                profile=runtime.profile_name,
                model=runtime.model,
            )
            repair_response = await _post_with_rate_limit_retry(
                phase=repair_phase,
                runtime=runtime,
                payload=repair_payload,
                timeout_seconds=timeout_seconds,
            )
            repair_body = repair_response.json()
            record_llm_usage(repair_body)
            repair_choice = repair_body["choices"][0]
            if repair_choice.get("finish_reason") == "length":
                raise SplitSynthesisPhaseError(repair_phase, "OutputTruncated")
            repaired_content = repair_choice["message"]["content"]
            result = model_type.model_validate(json.loads(repaired_content))
            record_llm_success(phase=repair_phase)
        record_llm_success(phase=phase)
        return result
    except httpx.HTTPStatusError as exc:
        safe_error = f"HTTPStatusError_{exc.response.status_code}"
        record_llm_failure(phase=phase, error_type=safe_error)
        raise SplitSynthesisPhaseError(phase, safe_error) from exc
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        record_llm_failure(phase=phase, error_type="timeout")
        raise SplitSynthesisPhaseTimeout(phase) from exc
    except RuntimeError as exc:
        record_llm_failure(phase=phase, error_type=type(exc).__name__)
        raise
    except Exception as exc:
        record_llm_failure(phase=phase, error_type=type(exc).__name__)
        raise SplitSynthesisPhaseError(phase, type(exc).__name__) from exc
