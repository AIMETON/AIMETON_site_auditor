from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextvars import ContextVar
from enum import StrEnum

from app.research_control import record_llm_start, record_llm_usage

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm import BASE_URL
from app.search_observer import SearchWaveTelemetry
from app.search_observer_models import ResolvedObserverModel


DEFAULT_SEARCH_OBSERVER_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_SEARCH_OBSERVER_TIMEOUT_SECONDS = 45.0
_LAST_SHADOW_OBSERVER_EVIDENCE: ContextVar[dict[str, object] | None] = ContextVar(
    "last_shadow_observer_evidence", default=None
)
_LAST_SHADOW_OBSERVER_FAILURE_REASON: ContextVar[str | None] = ContextVar(
    "last_shadow_observer_failure_reason", default=None
)


class ObserverAction(StrEnum):
    BOOST = "boost"
    SLOW = "slow"
    STOP = "stop"
    REFINE = "refine"
    CONTINUE = "continue"
    ESCALATE = "escalate"


class ShadowObserverModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DirectionRecommendation(ShadowObserverModel):
    direction_index: int = Field(ge=0)
    action: ObserverAction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=300)
    refined_queries: list[str] = Field(default_factory=list, max_length=4)


class SearchObserverRecommendation(ShadowObserverModel):
    observer_mode: str = Field(default="shadow", pattern=r"^shadow$")
    routing_changed: bool = False
    sufficient_evidence: bool
    recommendations: list[DirectionRecommendation] = Field(default_factory=list, max_length=40)
    summary: str = Field(min_length=1, max_length=600)


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    return json.loads(cleaned)


def _bounded_telemetry_payload(telemetry: SearchWaveTelemetry) -> dict:
    return {
        "query_count": telemetry.query_count,
        "result_count": telemetry.result_count,
        "unique_domain_count": telemetry.unique_domain_count,
        "duplicate_domain_ratio": telemetry.duplicate_domain_ratio,
        "provider_result_counts": telemetry.provider_result_counts,
        "attempt_states": telemetry.attempt_states,
        "latency_ms_total": telemetry.latency_ms_total,
        "degraded_attempts": telemetry.degraded_attempts,
        "total_cost_by_currency": {
            key: str(value) for key, value in telemetry.total_cost_by_currency.items()
        },
        "directions": [item.model_dump(mode="json") for item in telemetry.directions],
    }


def _observer_timeout_seconds() -> float:
    raw = os.getenv("HUNTER_SEARCH_OBSERVER_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_SEARCH_OBSERVER_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SEARCH_OBSERVER_TIMEOUT_SECONDS
    return min(45.0, max(1.0, value))


def _observer_failure_reason(exc: Exception) -> str:
    """Return a bounded, secret-free reason code for Observer failures."""
    if isinstance(exc, httpx.TimeoutException):
        return "transport_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status_error"
    if isinstance(exc, httpx.HTTPError):
        return "http_error"
    if isinstance(exc, ValidationError):
        return "schema_validation_error"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, (KeyError, IndexError, TypeError)):
        return "response_shape_error"
    if isinstance(exc, ValueError):
        return "value_error"
    return "unclassified_error"


def _legacy_model() -> ResolvedObserverModel | None:
    key = os.getenv("ROUTERAI_API_KEY")
    if not key:
        return None
    model = os.getenv("HUNTER_SEARCH_OBSERVER_MODEL", DEFAULT_SEARCH_OBSERVER_MODEL).strip()
    if not model:
        model = DEFAULT_SEARCH_OBSERVER_MODEL
    return ResolvedObserverModel(
        profile_name="routerai-shadow-observer",
        provider="routerai",
        base_url=BASE_URL.rstrip("/"),
        api_key=key,
        model=model,
        tier="O1",
        configured=True,
    )


def shadow_observer_runtime_descriptor() -> dict[str, str | bool | float]:
    """Return secret-free runtime identity for durable shadow evidence."""
    model = _legacy_model()
    if model is None:
        return {
            "profile_name": "routerai-shadow-observer",
            "provider": "routerai",
            "model": os.getenv("HUNTER_SEARCH_OBSERVER_MODEL", DEFAULT_SEARCH_OBSERVER_MODEL).strip()
            or DEFAULT_SEARCH_OBSERVER_MODEL,
            "tier": "O1",
            "configured": False,
            "timeout_seconds": _observer_timeout_seconds(),
        }
    descriptor = model.safe_descriptor()
    return {**descriptor, "timeout_seconds": _observer_timeout_seconds()}


def get_last_shadow_observer_evidence() -> dict[str, object]:
    """Return call-local, secret-free evidence for the immediately completed shadow evaluation."""
    return dict(_LAST_SHADOW_OBSERVER_EVIDENCE.get() or {})


def _bounded_recommendation_evidence(
    recommendation: SearchObserverRecommendation | None,
) -> list[dict[str, object]]:
    if recommendation is None:
        return []
    return [
        {
            "direction_index": item.direction_index,
            "action": str(item.action),
            "confidence": item.confidence,
            "rationale": item.rationale[:300],
            "refined_queries": item.refined_queries[:4],
            "later_observation_state": "no_later_wave",
        }
        for item in recommendation.recommendations[:40]
    ]


def _record_shadow_observer_evidence(
    *,
    descriptor: dict[str, str | bool | float],
    started: float,
    outcome: str,
    recommendation: SearchObserverRecommendation | None,
    failure_reason: str | None = None,
) -> None:
    _LAST_SHADOW_OBSERVER_EVIDENCE.set(
        {
            **descriptor,
            "observer_latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
            "observer_outcome": outcome,
            "observer_failure_reason": failure_reason,
            "schema_valid": recommendation is not None,
            "observer_recommendation_count": (
                0 if recommendation is None else len(recommendation.recommendations)
            ),
            "recommendations": _bounded_recommendation_evidence(recommendation),
            "later_observation_state": "no_later_wave",
        }
    )


async def evaluate_search_wave_shadow_with_model(
    telemetry: SearchWaveTelemetry,
    model: ResolvedObserverModel,
) -> SearchObserverRecommendation | None:
    """Evaluate one completed wave using one explicitly resolved advisory model.

    The model has no routing authority. Missing/incomplete configuration,
    transport failures and schema violations all fail open to deterministic
    Hunter behavior by returning None.
    """
    _LAST_SHADOW_OBSERVER_FAILURE_REASON.set(None)
    if not model.configured or not model.base_url or not model.api_key or not model.model:
        _LAST_SHADOW_OBSERVER_FAILURE_REASON.set("model_not_configured")
        return None

    schema = SearchObserverRecommendation.model_json_schema()
    prompt = f"""Ты — Search Observer AIMETON Hunter. Работаешь строго в SHADOW MODE.

Твоя задача — оценить фактическую отдачу завершённой поисковой волны и предложить рекомендации для следующей волны. Ты НЕ управляешь поиском и не можешь менять runtime-политику.

Разрешённые рекомендации: boost, slow, stop, refine, continue, escalate.

Жёсткие правила:
1. Никогда не утверждай, что routing уже изменён. routing_changed всегда false.
2. Не предлагай обход CAPTCHA, блокировок, cooldown, circuit breaker, quota, budget или provider policy.
3. Не предлагай повышать concurrency или отключать safety limits.
4. Не выдумывай компании, результаты или факты, которых нет в telemetry.
5. Не помечай направление stop при слабом объёме наблюдений без высокой уверенности.
6. refine может содержать максимум 4 новых поисковых формулировки и не должен выдумывать конкретные компании.
7. escalate означает только рекомендацию запросить следующий разрешённый эшелон через policy gate; это не разрешение на платный вызов.
8. Отличай continue от refine по наблюдаемой необходимости менять формулировку, а не по общему желанию получить ещё результаты: если направление даёт разнообразные домены и нет конкретного сигнала, что запрос сформулирован неудачно, предпочитай continue.
9. Используй refine только когда telemetry даёт конкретный диагностический сигнал для изменения запроса — например, заметную концентрацию повторяющихся доменов, слабое разнообразие результата или иной наблюдаемый дефект направления. В rationale явно назови этот сигнал; refined_queries заполняй только тогда, когда они адресуют названный дефект.
10. Не превращай сам факт неполноты поиска или возможность дополнительной волны в основание для refine. Не вводи собственные числовые пороги: опирайся на сравнительные признаки внутри текущей telemetry.
11. Верни только JSON по схеме.

Telemetry:
{json.dumps(_bounded_telemetry_payload(telemetry), ensure_ascii=False)}

JSON schema:
{json.dumps(schema, ensure_ascii=False)}
"""
    payload = {
        "model": model.model,
        "temperature": 0.0,
        "messages": [
            {
                "role": "system",
                "content": "Ты advisory-only Search Observer. Верни только валидный JSON; routing_changed всегда false.",
            },
            {"role": "user", "content": prompt},
        ],
    }

    record_llm_start()
    try:
        async with httpx.AsyncClient(timeout=_observer_timeout_seconds() + 5.0) as client:
            response = await client.post(
                f"{model.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {model.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        record_llm_usage(body)
        content = body["choices"][0]["message"]["content"]
        recommendation = SearchObserverRecommendation.model_validate(_extract_json(content))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        _LAST_SHADOW_OBSERVER_FAILURE_REASON.set(_observer_failure_reason(exc))
        return None

    if recommendation.routing_changed:
        _LAST_SHADOW_OBSERVER_FAILURE_REASON.set("routing_change_rejected")
        return None
    max_index = len(telemetry.directions) - 1
    if any(item.direction_index > max_index for item in recommendation.recommendations):
        _LAST_SHADOW_OBSERVER_FAILURE_REASON.set("direction_index_out_of_range")
        return None
    return recommendation


async def evaluate_search_wave_shadow(
    telemetry: SearchWaveTelemetry,
) -> SearchObserverRecommendation | None:
    """Evaluate the advisory-only shadow Observer with its dedicated model/timeout."""
    descriptor = shadow_observer_runtime_descriptor()
    started = time.perf_counter()
    model = _legacy_model()
    if model is None:
        _record_shadow_observer_evidence(
            descriptor=descriptor,
            started=started,
            outcome="not_configured",
            recommendation=None,
            failure_reason="model_not_configured",
        )
        return None
    try:
        recommendation = await asyncio.wait_for(
            evaluate_search_wave_shadow_with_model(telemetry, model),
            timeout=_observer_timeout_seconds(),
        )
    except TimeoutError:
        _record_shadow_observer_evidence(
            descriptor=descriptor,
            started=started,
            outcome="timeout",
            recommendation=None,
            failure_reason="wall_clock_timeout",
        )
        return None
    _record_shadow_observer_evidence(
        descriptor=descriptor,
        started=started,
        outcome="succeeded" if recommendation is not None else "unavailable",
        recommendation=recommendation,
        failure_reason=(
            None
            if recommendation is not None
            else _LAST_SHADOW_OBSERVER_FAILURE_REASON.get() or "unclassified_unavailable"
        ),
    )
    return recommendation
