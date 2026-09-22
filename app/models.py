from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.search_gateway.models import SearchDiagnostics


class AgentRecommendation(BaseModel):
    name: str
    purpose: str
    benefit: str
    priority: Literal["Высокий", "Средний", "Низкий"] = "Средний"


class EvidenceBlock(BaseModel):
    """One retained, locatable block nested under a primary evidence document."""

    id: str = Field(description="Stable block identifier inside the parent document")
    evidence_quote: str = Field(description="Retained verbatim block text or bounded fragment")
    evidence_locator: str
    evidence_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    query_kind: str = "unknown"
    relevance: Literal["high", "medium", "low", "none"] = "medium"
    entity_relation: Literal[
        "target", "affiliate", "counterparty", "competitor", "publisher",
        "mentioned_only", "unknown",
    ] = "target"
    role: Literal[
        "primary_fact", "corroboration", "context", "navigation_noise",
        "publisher_metadata", "related_entity", "advertising",
    ] = "context"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class EvidenceSource(BaseModel):
    id: str = Field(description="Короткий идентификатор источника, например S1")
    title: str
    url: str
    accessed_at: str = Field(description="Дата и время проверки в ISO 8601")
    evidence_quote: str = Field(description="Короткая дословная цитата из загруженного первичного документа")
    source_type: Literal[
        "official_page", "registry", "court", "arbitration", "enforcement",
        "ownership", "affiliation", "finance", "workforce", "contact",
        "news", "social", "review", "jobs", "tender", "patent",
        "external_source", "visual_observation",
    ] = "external_source"
    evidence_level: Literal[
        "confirmed_fact", "corroborated_signal", "weak_signal", "unverified_mention"
    ] = "unverified_mention"
    published_at: str | None = None
    freshness: Literal["current", "stale", "not_yet_valid", "unassessed"] = "unassessed"
    document_url: str | None = None
    document_title: str | None = None
    document_accessed_at: str | None = None
    document_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    evidence_locator: str | None = None
    evidence_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    fetch_path: Literal["static", "crawl4ai", "browser", "cache"] | None = None
    evidence_blocks: list[EvidenceBlock] = Field(default_factory=list)


class EconomicSignal(BaseModel):
    signal: str
    evidence: str
    business_effect: str
    confidence: Literal["Высокая", "Средняя", "Низкая"] = "Средняя"
    source_ids: list[str] = Field(default_factory=list, description="Ссылки на EvidenceSource.id")


class CompanyFact(BaseModel):
    field: Literal[
        "legal_name", "brand_name", "inn", "ogrn", "registration_status",
        "address", "phones", "emails", "website", "social_accounts",
        "headcount", "revenue", "profit", "assets", "taxes",
        "founders", "executives", "beneficial_owners", "affiliates",
        "geography", "products", "customers", "suppliers", "other",
    ]
    value: str
    period: str | None = None
    confidence: Literal["Высокая", "Средняя", "Низкая"] = "Средняя"
    source_ids: list[str] = Field(default_factory=list)
    note: str = ""


class BusinessMachineCell(BaseModel):
    code: Literal[
        "I-I", "I-II", "I-III", "I-IV",
        "II-I", "II-II", "II-III", "II-IV",
        "III-I", "III-II", "III-III", "III-IV",
        "IV-I", "IV-II", "IV-III", "IV-IV",
    ]
    detail_operator: Literal[
        "I — Коммуникационные системы",
        "II — Люди",
        "III — Технологии",
        "IV — Менеджмент",
    ]
    vertex: Literal[
        "Взаимодействие", "Влияние", "Зависимость", "Противодействие",
        "Учредители и собственники", "Ось люди-управленцы", "Обслуживающий персонал и роботы", "Виртуозы и специалисты",
        "Знания и наука", "Стандартная процедура", "Рабочая процедура", "Продукты, товар и услуга",
        "Управление коммуникационными системами", "Управление людьми", "Управление технологиями", "Самоуправление",
    ]
    finding: str
    status: Literal["Подтверждено", "Частично", "Гипотеза", "Нет данных"] = "Нет данных"
    confidence: Literal["Высокая", "Средняя", "Низкая"] = "Низкая"
    source_ids: list[str] = Field(default_factory=list)
    sales_relevance: str = Field(default="", description="Как узел влияет на AI-продажу, пилот или ценностное предложение")


class CommercialOpportunity(BaseModel):
    opportunity_type: str
    problem_hypothesis: str
    recommended_solution: str
    expected_value: str
    score: int = Field(ge=0, le=100)
    qualification: Literal["Приоритетная", "Перспективная", "Наблюдение", "Недостаточно данных"]
    source_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceSource.id, непосредственно поддерживающие коммерческую гипотезу",
    )


class ActionPackage(BaseModel):
    decision_maker_hypothesis: str
    contact_reason: str
    demo_scenario: list[str] = Field(default_factory=list)
    first_message: str
    next_action: str


class PreliminaryVerticalStatus(BaseModel):
    code: str
    state: Literal[
        "verified",
        "partially_verified",
        "not_found_after_sufficient_search",
        "not_searched",
        "blocked",
        "degraded",
    ] = "not_searched"


class PreliminaryResultReadiness(BaseModel):
    result_status: Literal["preliminary"] = "preliminary"
    analysis_state: Literal[
        "schema_validated",
        "preliminary_hypothesis",
        "validation_error",
        "not_applicable",
    ] = "preliminary_hypothesis"
    client_release_eligible: Literal[False] = False
    sufficiency_level: Literal["L0", "L1", "L2", "L3", "L4", "L5"] = "L0"
    identity_state: Literal[
        "resolved",
        "provisional",
        "unresolved",
        "conflicting",
    ] = "unresolved"
    profile_completeness: float = Field(default=0, ge=0, le=1)
    evidence_quality: float = Field(default=0, ge=0, le=1)
    commercial_priority: int = Field(default=0, ge=0, le=100)
    required_verticals: list[PreliminaryVerticalStatus] = Field(
        default_factory=lambda: [
            PreliminaryVerticalStatus(code=code)
            for code in (
                "identity",
                "contacts",
                "workforce",
                "financials",
                "ownership",
                "legal_events",
            )
        ]
    )
    provider_states: dict[str, str] = Field(
        default_factory=lambda: {"routerai": "not_attempted"}
    )
    budget_state: Literal["within_budget", "unknown", "exhausted"] = "unknown"
    release_blockers: list[str] = Field(
        default_factory=lambda: [
            "preliminary_result",
            "identity_unresolved",
            "sufficiency_below_l4",
            "human_review_and_signed_report_required",
        ]
    )


class TargetApplicability(BaseModel):
    applicability: Literal["applicable", "not_applicable", "ambiguous", "unavailable"]
    target_kind: Literal[
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
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    target_entity_name: str = Field(default="", max_length=240)
    reason: str = Field(default="", max_length=500)
    classifier: str = "fast_research"


class SiteAnalysis(BaseModel):
    research_queries: list[str] = Field(default_factory=list)
    target_applicability: TargetApplicability | None = None
    research_status: dict[str, str | int | bool] = Field(default_factory=dict)
    profile_revision: int = Field(default=0, ge=0)
    user_clarifications: list[str] = Field(default_factory=list)
    mission_id: str | None = None
    analysis_id: str | None = None
    url: str
    company_name: str
    business_summary: str
    evidence: list[str] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    company_facts: list[CompanyFact] = Field(default_factory=list)
    business_machine_4x4: list[BusinessMachineCell] = Field(default_factory=list, max_length=16)
    economic_signals: list[EconomicSignal] = Field(default_factory=list)
    commercial_opportunity: CommercialOpportunity
    agents: list[AgentRecommendation] = Field(default_factory=list, max_length=10)
    action_package: ActionPackage
    risks_and_assumptions: list[str] = Field(default_factory=list)
    readiness: PreliminaryResultReadiness = Field(
        default_factory=PreliminaryResultReadiness
    )


class ResearchOptions(BaseModel):
    research_settings_revision: int | None = Field(default=None, ge=0, strict=True)
    deep_research: bool = False
    unlimited_llm_budget: bool = False

    @model_validator(mode="after")
    def explicit_uncapped_consent(self):
        if self.deep_research != self.unlimited_llm_budget:
            raise ValueError("deep_research_requires_explicit_unlimited_llm_budget_consent")
        return self


class AnalyzeRequest(ResearchOptions):
    url: str = Field(min_length=1, max_length=2048)


class HuntRequest(BaseModel):
    region: str = Field(min_length=2, description="Территория экономической разведки")
    search_zone: str | None = Field(default=None, description="Район, агломерация или дополнительная зона")
    industries: list[str] = Field(default_factory=list, description="Приоритетные отрасли; пусто — агент формирует базовый набор")
    focus: list[str] = Field(default_factory=list, description="Дополнительные признаки или типы возможностей")
    max_queries: int = Field(default=20, ge=1, le=100)
    results_per_query: int = Field(default=10, ge=1, le=30)
    max_candidates: int = Field(default=100, ge=1, le=500)
    minimum_pre_score: int = Field(default=35, ge=0, le=100)
    deep_audit_score: int = Field(default=60, ge=0, le=100)
    output_limit: int = Field(default=25, ge=1, le=100)
    concurrency: int = Field(default=4, ge=1, le=12)


class HuntCandidate(BaseModel):
    company_name: str
    url: str
    source_title: str
    source_snippet: str = ""
    region_confirmed: bool | None = None
    preliminary_score: int | None = Field(default=None, ge=0, le=100)
    pre_score_status: Literal["calculated", "insufficient_data"] = "calculated"
    pre_score_factors: dict[str, int | None] = Field(default_factory=dict)
    deep_analysis_performed: bool = False
    final_score: int | None = Field(default=None, ge=0, le=100)
    qualification: str
    business_summary: str
    recommended_solution: str
    reasons: list[str] = Field(default_factory=list)
    lead_fit: Literal[
        "commercial_candidate",
        "unknown_candidate",
        "institutional_candidate",
        "not_applicable",
    ] = "unknown_candidate"
    lead_fit_reason: str = ""
    lead_fit_evidence: list[str] = Field(default_factory=list)
    analysis: SiteAnalysis | None = None


class HuntFunnel(BaseModel):
    raw_results: int = Field(default=0, ge=0)
    excluded_results: int = Field(default=0, ge=0)
    duplicate_results: int = Field(default=0, ge=0)
    pool_omitted_results: int = Field(default=0, ge=0)
    unique_candidates: int = Field(default=0, ge=0)
    inspected_candidates: int = Field(default=0, ge=0)
    qualified_candidates: int = Field(default=0, ge=0)
    returned_candidates: int = Field(default=0, ge=0)
    output_omitted_candidates: int = Field(default=0, ge=0)


class HuntResult(BaseModel):
    region: str
    search_zone: str | None = None
    queries: list[str] = Field(default_factory=list)
    discovered: int
    candidates: list[HuntCandidate] = Field(default_factory=list)
    funnel: HuntFunnel = Field(default_factory=HuntFunnel)
    notes: list[str] = Field(default_factory=list)
    search: SearchDiagnostics | None = None
    trace_mission_id: str | None = Field(default=None, exclude=True, repr=False)
    trace_attempt_id: str | None = Field(default=None, exclude=True, repr=False)


SourceKind = Literal[
    "official", "registry", "court", "arbitration", "enforcement",
    "ownership", "affiliation", "finance", "workforce", "contact",
    "news", "social", "review", "jobs", "tender", "patent",
    "aggregator", "other", "unknown",
]


class IntelligenceSource(BaseModel):
    id: str
    title: str
    url: str
    snippet: str = ""
    accessed_at: str
    source_class: SourceKind = "unknown"
    query_kind: SourceKind = "unknown"
    result_kind: SourceKind = "unknown"
    classification_state: Literal["classified", "ambiguous", "unknown"] = "unknown"
    lifecycle_state: Literal["discovery_hint", "source_candidate", "evidence"] = "discovery_hint"
    evidence_level: Literal["confirmed_fact", "corroborated_signal", "weak_signal", "unverified_mention"] = "unverified_mention"
    published_at: str | None = None
    document_url: str | None = None
    document_title: str | None = None
    document_accessed_at: str | None = None
    evidence_quote: str | None = None
    document_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    evidence_locator: str | None = None
    evidence_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    fetch_path: Literal["static", "crawl4ai", "browser", "cache"] | None = None
    verification_note: str = "Поисковый сниппет; первичный документ не проверен."
    preflight_decision: Literal["not_required", "include", "exclude", "uncertain"] = "not_required"
    preflight_reason: str = ""


class CompanyIntelligenceRequest(BaseModel):
    research_settings_revision: int | None = Field(default=None, ge=0, strict=True)
    company_name: str = Field(min_length=2)
    url: HttpUrl | None = None
    region: str | None = None
    max_sources: int = Field(default=60, ge=5, le=140)


class CompanyIntelligenceResult(BaseModel):
    company_name: str
    region: str | None = None
    official_url: str | None = None
    site_analysis: SiteAnalysis | None = None
    sources: list[IntelligenceSource] = Field(default_factory=list)
    scent_summary: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    commercial_score: int = Field(ge=0, le=100)
    recommended_solution: str
    status: Literal["complete", "partial"] = "partial"
    search: SearchDiagnostics | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(ResearchOptions):
    refine_search: bool = False
    analysis: SiteAnalysis
    messages: list[ChatMessage]