from __future__ import annotations

from app.research_execution import research_timed, operation_timeout

import json
import os
import re

from app.research_control import record_llm_start, record_llm_usage

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.llm import BASE_URL, MODEL


class HunterQueryPlan(BaseModel):
    normalized_region: str = Field(min_length=2, max_length=160)
    normalized_industries: list[str] = Field(default_factory=list, max_length=12)
    normalized_focus: list[str] = Field(default_factory=list, max_length=12)
    corrected_input_summary: str = Field(default="", max_length=500)
    query_variants: list[str] = Field(min_length=1, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=12)


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    return json.loads(cleaned)


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _anchor_variants(value: str) -> set[str]:
    normalized = _normalize_text(value)
    variants: set[str] = set()
    if not normalized:
        return variants
    variants.add(normalized)
    for token in re.split(r"[^0-9a-zа-я]+", normalized):
        if len(token) < 5:
            continue
        variants.add(token)
        variants.add(token[: max(5, len(token) - 3)])
    return variants


def _query_preserves_semantic_anchors(
    query: str,
    *,
    region: str,
    industries: list[str],
) -> bool:
    """Fail closed when an LLM query drops the hunt's region or industry."""

    haystack = _normalize_text(query)
    region_anchors = _anchor_variants(region)
    industry_anchors: set[str] = set()
    for industry in industries:
        industry_anchors.update(_anchor_variants(industry))

    if not region_anchors or not industry_anchors:
        return False
    return any(anchor in haystack for anchor in region_anchors) and any(
        anchor in haystack for anchor in industry_anchors
    )


def _dedupe_queries(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).split()).strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _validated_queries(
    values: list[str],
    *,
    region: str,
    industries: list[str],
    limit: int,
) -> tuple[list[str], int]:
    valid: list[str] = []
    rejected = 0
    for query in _dedupe_queries(values, max(limit, len(values))):
        if _query_preserves_semantic_anchors(query, region=region, industries=industries):
            valid.append(query)
            if len(valid) >= limit:
                break
        else:
            rejected += 1
    return valid, rejected


@research_timed("llm")
async def generate_hunter_query_plan(
    *,
    region: str,
    industries: list[str],
    focus: list[str],
    max_queries: int,
) -> HunterQueryPlan | None:
    """Normalize Hunter input and generate diverse search variants with bounded RouterAI use.

    Returns None on any provider/config/schema failure so the caller can safely fall back to
    the deterministic Hunter query builder. LLM variants are validated deterministically
    before execution: every query must preserve both the normalized region and industry.
    """
    key = os.getenv("ROUTERAI_API_KEY")
    if not key:
        return None

    max_queries = max(1, min(int(max_queries), 100))
    schema = HunterQueryPlan.model_json_schema()
    prompt = f"""Ты — Query Intelligence модуль AIMETON Hunter.

Задача: подготовить качественный план веб-поиска потенциальных компаний до обращения к поисковым провайдерам.

Правила:
1. Сохрани исходный смысл пользователя, территорию и отрасль.
2. Исправь только очевидные опечатки и орфографические ошибки. Не меняй смысл молча.
3. Нормализуй регион, отрасли и фокус.
4. Сгенерируй разнообразные поисковые варианты, которые реально расширяют покрытие, а не являются косметическими перефразированиями.
5. Используй уместные синонимы и отраслевые варианты только при высокой уверенности.
6. Часть запросов должна искать официальные сайты компаний; часть — локальные организации/сети/клиники/центры соответствующей отрасли; допускаются каталожные формулировки только как вспомогательный путь обнаружения.
7. Каждый query_variant обязан явно сохранять территорию и отрасль из нормализованного задания; не выдавай generic-запросы только со словами `каталог`, `список`, `детская`, `сеть` и т.п.
8. Не выдумывай названия конкретных компаний, юридические лица, адреса или факты.
9. Не генерируй более {max_queries} query_variants.
10. Верни только JSON по схеме без Markdown.

Вход:
region={json.dumps(region, ensure_ascii=False)}
industries={json.dumps(industries, ensure_ascii=False)}
focus={json.dumps(focus, ensure_ascii=False)}

JSON schema:
{json.dumps(schema, ensure_ascii=False)}
"""
    payload = {
        "model": MODEL,
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": "Возвращай только валидный JSON. Не выдумывай компании и факты; твоя задача — исправление и расширение поисковых формулировок.",
            },
            {"role": "user", "content": prompt},
        ],
    }

    record_llm_start()
    try:
        async with httpx.AsyncClient(timeout=operation_timeout("llm", 25)) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        record_llm_usage(body)
        content = body["choices"][0]["message"]["content"]
        plan = HunterQueryPlan.model_validate(_extract_json(content))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
        return None

    effective_region = plan.normalized_region or region
    effective_industries = plan.normalized_industries or industries
    validated, rejected = _validated_queries(
        plan.query_variants,
        region=effective_region,
        industries=effective_industries,
        limit=max_queries,
    )
    if not validated:
        return None

    warnings = list(plan.warnings)
    if rejected:
        warnings.append(f"deterministic_query_guard_rejected={rejected}")
    return plan.model_copy(update={"query_variants": validated, "warnings": warnings[:12]})
