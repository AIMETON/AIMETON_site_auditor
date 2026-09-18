from __future__ import annotations

from app.research_execution import research_timed, operation_timeout

import json
import os
import re
from datetime import datetime, timezone

import httpx

from app.research_control import record_llm_start, record_llm_usage
from app.llm_runtime_settings import LlmReasoningMode, LlmRole, resolve_llm_runtime

from app.models import (
    EvidenceSource,
    PreliminaryResultReadiness,
    PreliminaryVerticalStatus,
    SiteAnalysis,
)

BASE_URL = os.getenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")
MODEL = os.getenv("ROUTERAI_MODEL", "openai/gpt-4o-mini")


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    return json.loads(text)


def _fallback_quote(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] if compact else "Текст страницы не извлечён."


@research_timed("llm")
async def analyze_with_routerai(url: str, title: str, text: str, external_sources: list[dict] | None = None) -> SiteAnalysis:
    runtime = resolve_llm_runtime(LlmRole.REASONING)
    if not runtime.configured:
        raise RuntimeError(f"llm_runtime_not_configured:reasoning:{runtime.profile_name}")

    schema = SiteAnalysis.model_json_schema()
    accessed_at = datetime.now(timezone.utc).isoformat()
    external_sources = external_sources or []
    external_context = json.dumps(external_sources, ensure_ascii=False, indent=2)[:52000]

    prompt = f"""Ты — AI-продажник и аналитический модуль экономической разведки AIMETON Site Auditor.

ГЛАВНАЯ ЦЕЛЬ
Найти наиболее доказанную коммерческую возможность для продажи конкретного AI-решения компании, подготовить убедительный пилот и пакет первого контакта. Восстановление профиля компании и модель КМ служат углублению понимания и повышению точности продажи, но не заменяют коммерческий вывод.

ДОКАЗАТЕЛЬНОСТЬ
1. S1 — официальный сайт. Внешние источники имеют id E1, E2 и далее. Используй только эти id.
2. Не создавай URL, цифры, людей, контакты и факты, отсутствующие во входных данных.
3. Поисковый сниппет — сигнал. Высокая уверенность допустима для официального источника, государственного реестра или двух независимых согласующихся источников.
4. Для каждого факта, сигнала и элемента КМ указывай source_ids.
5. Не найдено — пиши «Нет данных», а не делай вывод об отсутствии.
6. Финансовые показатели всегда сопровождай периодом. Не смешивай выручку, прибыль, активы, налоги и оборот.
7. Учредитель не автоматически является фактическим владельцем. Аффилированность и бенефициарность маркируй как гипотезу до подтверждения.
8. Наличие судебного дела не означает виновность. Указывай роль компании, если она видна.

ПРОФИЛЬ КОМПАНИИ
Извлеки подтверждённые сведения: юридическое и брендовое название, ИНН, ОГРН, статус, адреса, телефоны, email, сайт, соцсети, численность персонала, выручку, прибыль, активы, налоги, учредителей, руководителей, предполагаемых бенефициаров, аффилированные компании, географию, продукты, клиентов и поставщиков. Неизвестные поля не выдумывай.

КАНОНИЧЕСКАЯ БИЗНЕС-МОДЕЛЬ AIMETON / КМ
Используй данные нормативного ядра AIMETON: один корневой оператор бизнеса имеет четыре функции — I коммуникационные системы, II люди, III технологии, IV менеджмент. Каждая раскрывается в отдельный детализирующий оператор КМ с четырьмя вершинами. Сформируй до 16 элементов business_machine_4x4 строго по следующей структуре, без дополнительных измерений и без выдуманных осей.

I — Коммуникационные системы:
 I-I Взаимодействие
 I-II Влияние
 I-III Зависимость
 I-IV Противодействие

II — Люди:
 II-I Учредители и собственники
 II-II Ось люди-управленцы
 II-III Обслуживающий персонал и роботы
 II-IV Виртуозы и специалисты

III — Технологии:
 III-I Знания и наука
 III-II Стандартная процедура
 III-III Рабочая процедура
 III-IV Продукты, товар и услуга

IV — Менеджмент:
 IV-I Управление коммуникационными системами
 IV-II Управление людьми
 IV-III Управление технологиями
 IV-IV Самоуправление

Для каждого элемента укажи finding, status, confidence, source_ids и sales_relevance — как найденное влияет на AI-продажу, выбор пилота или ценностное предложение. Если данных нет, status="Нет данных". Не превращай КМ в отдельную оценочную матрицу и не подменяй ею поиск коммерческой возможности.

ОПЕРАЦИОННЫЕ ПРОЕКЦИИ
Учитывай, что в нормативной архитектуре бизнес имеет две операционные проекции — external/front_office и internal/back_office, то есть 4 функции × 2 направления = 8 проекций. Не называй это 4×4 и не создавай для них отдельные вершины без данных.

AI-ПРОДАЖА
1. Сначала выяви экономические сигналы и подтверждённые разрывы.
2. Затем выбери одну наиболее доказанную и экономически значимую AI-возможность.
3. Предложи 3–10 конкретных AI-инструментов, сохраняя основной фокус на решениях, которые можно продемонстрировать и внедрить.
4. Подготовь ЛПР, основание контакта, демонстрационный сценарий, первое сообщение и следующий шаг.
5. Оценка 80+ допустима только при прямом подтверждении проблемы, масштаба и реалистичного пилота.

Верни только JSON по схеме.

OFFICIAL URL: {url}
TITLE: {title}
ACCESSED AT: {accessed_at}
OFFICIAL PAGE TEXT:
{text[:30000]}

EXTERNAL OSINT SOURCES:
{external_context}

JSON SCHEMA:
{json.dumps(schema, ensure_ascii=False)}"""

    if runtime.output_mode.value == "strict_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "aimeton_site_analysis",
                "strict": True,
                "schema": schema,
            },
        }
    else:
        response_format = {"type": "json_object"}
    payload = {
        "model": runtime.model,
        "temperature": runtime.temperature,
        "max_tokens": runtime.max_tokens,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": "Возвращай валидный JSON без Markdown. Главный результат — доказанная AI-коммерческая возможность; профиль и КМ усиливают её, но не заменяют."},
            {"role": "user", "content": prompt},
        ],
    }
    if runtime.output_mode.value == "strict_schema":
        payload["structured_outputs"] = True
    if runtime.reasoning_mode is LlmReasoningMode.ON:
        reasoning = {"enabled": True}
        if runtime.reasoning_effort is not None:
            reasoning["effort"] = runtime.reasoning_effort.value
        payload["reasoning"] = reasoning
    else:
        payload["reasoning"] = {"enabled": False}
    record_llm_start()
    async with httpx.AsyncClient(timeout=operation_timeout("llm", runtime.timeout_seconds)) as client:
        response = await client.post(
            f"{runtime.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {runtime.api_key}"},
            json=payload,
        )
        response.raise_for_status()
    body = response.json()
    record_llm_usage(body)
    result = SiteAnalysis.model_validate(_extract_json(body["choices"][0]["message"]["content"]))

    if not any(source.id == "S1" for source in result.sources):
        result.sources.insert(0, EvidenceSource(
            id="S1", title=title or url, url=url, accessed_at=accessed_at,
            evidence_quote=_fallback_quote(text), source_type="official_page", evidence_level="confirmed_fact",
        ))

    allowed_external = {str(item.get("id")): item for item in external_sources}
    clean_sources: list[EvidenceSource] = []
    seen_ids: set[str] = set()
    for source in result.sources:
        if source.id in seen_ids:
            continue
        if source.id == "S1":
            clean_sources.append(source)
            seen_ids.add(source.id)
            continue
        item = allowed_external.get(source.id)
        if not item:
            continue
        clean_sources.append(EvidenceSource(
            id=source.id,
            title=str(item.get("title") or source.title),
            url=str(item.get("url") or source.url),
            accessed_at=str(item.get("accessed_at") or accessed_at),
            evidence_quote=str(
                item.get("evidence_quote")
                or item.get("snippet")
                or source.evidence_quote
            )[:900],
            source_type=str(item.get("source_type") or "external_source"),
            evidence_level=str(item.get("evidence_level") or "unverified_mention"),
            document_url=item.get("document_url"),
            document_title=item.get("document_title"),
            document_accessed_at=item.get("document_accessed_at"),
            document_digest=item.get("document_digest"),
            evidence_locator=item.get("evidence_locator"),
            evidence_digest=item.get("evidence_digest"),
            fetch_path=item.get("fetch_path"),
        ))
        seen_ids.add(source.id)
    result.sources = clean_sources

    known_ids = {source.id for source in result.sources}
    for signal in result.economic_signals:
        signal.source_ids = [source_id for source_id in signal.source_ids if source_id in known_ids]
    for fact in result.company_facts:
        fact.source_ids = [source_id for source_id in fact.source_ids if source_id in known_ids]
    for cell in result.business_machine_4x4:
        cell.source_ids = [source_id for source_id in cell.source_ids if source_id in known_ids]
    fact_fields = {item.field for item in result.company_facts if item.source_ids}
    vertical_fields = {
        "identity": {
            "legal_name",
            "brand_name",
            "inn",
            "ogrn",
            "registration_status",
            "address",
        },
        "contacts": {"phones", "emails", "website", "social_accounts"},
        "workforce": {"headcount"},
        "financials": {"revenue", "profit", "assets", "taxes"},
        "ownership": {
            "founders",
            "executives",
            "beneficial_owners",
            "affiliates",
        },
        "legal_events": set(),
    }
    confirmed_sources = sum(
        source.evidence_level in {"confirmed_fact", "corroborated_signal"}
        and source.document_digest is not None
        and source.evidence_digest is not None
        for source in result.sources
    )
    result.readiness = PreliminaryResultReadiness(
        analysis_state="schema_validated",
        profile_completeness=min(len(fact_fields) / 25, 1),
        evidence_quality=(
            confirmed_sources / len(result.sources) if result.sources else 0
        ),
        commercial_priority=result.commercial_opportunity.score,
        required_verticals=[
            PreliminaryVerticalStatus(
                code=code,
                state=(
                    "partially_verified"
                    if fields and fact_fields.intersection(fields)
                    else "not_searched"
                ),
            )
            for code, fields in vertical_fields.items()
        ],
        provider_states={"routerai": "active"},
        release_blockers=[
            "preliminary_result",
            "identity_unresolved",
            "sufficiency_below_l4",
            "mandatory_verticals_incomplete",
            "provider_state_unknown",
            "budget_unknown",
            "human_review_and_signed_report_required",
        ],
    )
    return result


@research_timed("llm")
async def chat_with_routerai(analysis: SiteAnalysis, messages: list[dict]) -> str:
    runtime = resolve_llm_runtime(LlmRole.REASONING)
    if not runtime.configured:
        return "Для диалога необходимо настроить активный LLM-профиль."
    system = (
        "Ты AI-консультант по продаже решений AIMETON. Сохраняй главной целью развитие доказанной коммерческой возможности. "
        "Используй company_facts и канонические элементы КМ для углубления понимания компании, но не подменяй ими продажу. "
        "Помогай уточнять профиль компании: объясняй пробелы и противоречия, предлагай конкретные направления проверки. "
        "user_clarifications — утверждения и пожелания пользователя, они не являются проверенными фактами. "
        "Не утверждай, что выполнил поиск, если research_status этого не подтверждает. "
        "Явно разделяй факты, выводы и гипотезы; не обещай неподтверждённый эффект. Анализ: "
        + analysis.model_dump_json()
    )
    payload = {
        "model": runtime.model,
        "temperature": runtime.temperature,
        "max_tokens": runtime.max_tokens,
        "messages": [{"role": "system", "content": system}] + messages[-12:],
    }
    if runtime.reasoning_mode is LlmReasoningMode.ON:
        reasoning = {"enabled": True}
        if runtime.reasoning_effort is not None:
            reasoning["effort"] = runtime.reasoning_effort.value
        payload["reasoning"] = reasoning
    else:
        payload["reasoning"] = {"enabled": False}
    record_llm_start()
    async with httpx.AsyncClient(timeout=operation_timeout("llm", runtime.timeout_seconds)) as client:
        response = await client.post(
            f"{runtime.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {runtime.api_key}"},
            json=payload,
        )
        response.raise_for_status()
    body = response.json()
    record_llm_usage(body)
    return body["choices"][0]["message"]["content"]
