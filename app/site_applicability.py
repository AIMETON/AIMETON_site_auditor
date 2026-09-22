from __future__ import annotations

import json
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from app.fast_research_model import FastResearchModelUnavailable, request_fast_json
from app.models import (
    ActionPackage,
    CommercialOpportunity,
    PreliminaryResultReadiness,
    SiteAnalysis,
    TargetApplicability,
)


_CLASSIFIER_TEXT_CHARS = 12_000
_NOT_APPLICABLE_CONFIDENCE = 0.80


class _ApplicabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicability: str
    target_kind: str
    confidence: float = Field(ge=0.0, le=1.0)
    target_entity_name: str = Field(default="", max_length=240)
    reason: str = Field(min_length=1, max_length=500)


_ALLOWED_APPLICABILITY = {"applicable", "not_applicable", "ambiguous"}
_ALLOWED_KINDS = {
    "company_site",
    "company_product_service_page",
    "commercial_storefront",
    "marketplace_listing",
    "directory_aggregator",
    "media_article",
    "government_public",
    "nonprofit_community",
    "personal_blog_portfolio",
    "documentation_knowledge_base",
    "social_profile",
    "unknown",
}


def _bounded_text(text: str) -> str:
    compact = str(text or "").strip()
    if len(compact) <= _CLASSIFIER_TEXT_CHARS:
        return compact
    half = _CLASSIFIER_TEXT_CHARS // 2
    return compact[:half] + "\n\n[... middle omitted for cheap classification ...]\n\n" + compact[-half:]


async def classify_site_applicability(
    url: str,
    title: str,
    text: str,
    *,
    request_json=None,
) -> TargetApplicability:
    """Cheap semantic gate before company search, registry enrichment, or core synthesis."""
    request = request_json or request_fast_json
    payload = {
        "url": str(url),
        "title": str(title or "")[:500],
        "page_text": _bounded_text(text),
    }
    try:
        response = await request(
            "site_applicability",
            _ApplicabilityResponse,
            system=(
                "Ты быстрый классификатор применимости аудита компании. "
                "Определи, представляет ли входная страница саму коммерческую организацию "
                "или её собственный продукт/услугу. Не извлекай профиль компании и не делай "
                "коммерческих выводов. Упоминание компании в статье, каталоге, форуме, "
                "госстранице или чужом marketplace listing не делает страницу сайтом этой компании. "
                "Если принадлежность страницы коммерческой организации неочевидна, выбирай ambiguous. "
                "Верни только JSON по схеме."
            ),
            prompt=(
                "CLASSIFY THIS INPUT FOR COMPANY AUDIT:\n"
                + json.dumps(payload, ensure_ascii=False)
                + "\n\n"
                "applicable — официальный сайт коммерческой организации, её собственная "
                "product/service landing page или storefront, где оператор однозначно является "
                "объектом аудита. not_applicable — медиа/статья, каталог/агрегатор, отдельный "
                "marketplace listing, госорган, некоммерческое сообщество, личный блог/портфолио, "
                "форум/соцпрофиль или иная страница, где компания не является однозначным оператором. "
                "ambiguous — данных недостаточно или ownership страницы неясен."
            ),
            max_tokens=700,
            timeout_seconds=10.0,
        )
    except FastResearchModelUnavailable as exc:
        return TargetApplicability(
            applicability="unavailable",
            target_kind="unknown",
            confidence=0.0,
            reason=f"fast_classifier_unavailable:{type(exc).__name__}",
        )
    except Exception as exc:
        return TargetApplicability(
            applicability="unavailable",
            target_kind="unknown",
            confidence=0.0,
            reason=f"fast_classifier_failed:{type(exc).__name__}",
        )

    applicability = str(response.applicability or "").strip()
    target_kind = str(response.target_kind or "").strip()
    if applicability not in _ALLOWED_APPLICABILITY:
        applicability = "ambiguous"
    if target_kind not in _ALLOWED_KINDS:
        target_kind = "unknown"
    return TargetApplicability(
        applicability=applicability,
        target_kind=target_kind,
        confidence=float(response.confidence),
        target_entity_name=" ".join(str(response.target_entity_name or "").split())[:240],
        reason=" ".join(str(response.reason or "").split())[:500],
    )


def should_short_circuit(assessment: TargetApplicability) -> bool:
    return (
        assessment.applicability == "not_applicable"
        and assessment.confidence >= _NOT_APPLICABLE_CONFIDENCE
    )


def not_applicable_site_analysis(
    url: str,
    title: str,
    assessment: TargetApplicability,
) -> SiteAnalysis:
    host = (urlparse(url).hostname or "").lower()
    display_name = assessment.target_entity_name or title.strip() or host or str(url)
    reason = assessment.reason or "Вход не является однозначным сайтом коммерческой организации."
    readiness = PreliminaryResultReadiness(
        analysis_state="not_applicable",
        identity_state="unresolved",
        commercial_priority=0,
        provider_states={"site_applicability": "classified"},
        release_blockers=[
            "target_not_company_site",
            "company_analysis_not_applicable",
        ],
    )
    return SiteAnalysis(
        url=url,
        company_name=display_name,
        business_summary=reason,
        target_applicability=assessment,
        commercial_opportunity=CommercialOpportunity(
            opportunity_type="Не применимо",
            problem_hypothesis="Company audit не запускается для этого типа входа.",
            recommended_solution="Укажите официальный сайт конкретной коммерческой организации.",
            expected_value="Предотвращение ложного company profile и ненужных платных вызовов.",
            score=0,
            qualification="Недостаточно данных",
            source_ids=[],
        ),
        agents=[],
        action_package=ActionPackage(
            decision_maker_hypothesis="Не применимо",
            contact_reason="Не применимо",
            demo_scenario=[],
            first_message="",
            next_action="Укажите официальный сайт или собственную product/service страницу компании.",
        ),
        risks_and_assumptions=[
            f"Ранняя классификация: {assessment.target_kind}; confidence={assessment.confidence:.2f}.",
            reason,
        ],
        readiness=readiness,
        research_status={
            "stage": "target_not_applicable",
            "site_applicability": assessment.applicability,
            "site_target_kind": assessment.target_kind,
            "site_applicability_model_used": True,
            "core_llm_calls": 0,
        },
    )


def attach_applicability(
    analysis: SiteAnalysis,
    assessment: TargetApplicability,
) -> SiteAnalysis:
    status = dict(analysis.research_status)
    status.update(
        site_applicability=assessment.applicability,
        site_target_kind=assessment.target_kind,
        site_applicability_model_used=(assessment.applicability != "unavailable"),
    )
    return analysis.model_copy(update={
        "target_applicability": assessment,
        "research_status": status,
    })
