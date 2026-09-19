from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from app.models import CompanyFact, EconomicSignal
from app.research_control import ResearchControl, bind_research, current_research, deep_research_enabled
from app.routerai_evidence_units import (
    DEFAULT_EVIDENCE_CHUNK_CHARS,
    DEEP_EVIDENCE_CHUNK_CHARS,
    DEFAULT_MAX_FAST_PATH_UNITS,
    EvidenceCoverage,
    EvidenceCoverageOverflow,
    chunk_sources,
    chunk_text,
    evidence_units,
    project_sources,
)


FactField = Literal[
    "legal_name", "brand_name", "inn", "ogrn", "registration_status",
    "address", "phones", "emails", "website", "social_accounts",
    "headcount", "revenue", "profit", "assets", "taxes",
    "founders", "executives", "beneficial_owners", "affiliates",
    "geography", "products", "customers", "suppliers", "other",
]
Confidence = Literal["Высокая", "Средняя", "Низкая"]
ShortText = Annotated[str, Field(max_length=180)]
ManagementShortText = Annotated[str, Field(max_length=120)]
BoundedSourceId = Annotated[str, Field(max_length=64)]


class CompactCompanyFact(BaseModel):
    field: FactField
    value: str = Field(max_length=200)
    period: str | None = Field(default=None, max_length=64)
    confidence: Confidence = "Средняя"
    source_ids: list[str] = Field(default_factory=list, max_length=3)


class ManagementCompanyFact(CompactCompanyFact):
    field: Literal["founders", "executives"]
    value: str = Field(max_length=140)
    period: str | None = Field(default=None, max_length=32)
    source_ids: list[BoundedSourceId] = Field(default_factory=list, max_length=2)


class OwnershipNetworkCompanyFact(CompactCompanyFact):
    field: Literal["beneficial_owners", "affiliates", "social_accounts"]
    value: str = Field(max_length=140)
    period: str | None = Field(default=None, max_length=32)
    source_ids: list[BoundedSourceId] = Field(default_factory=list, max_length=2)


class CompactEconomicSignal(BaseModel):
    signal: str = Field(max_length=140)
    evidence: str = Field(max_length=170)
    business_effect: str = Field(max_length=170)
    confidence: Confidence = "Средняя"
    source_ids: list[str] = Field(default_factory=list, max_length=3)


class IdentityCoreSlice(BaseModel):
    company_name: str = Field(max_length=160)
    business_summary: str = Field(max_length=380)
    evidence: list[ShortText] = Field(default_factory=list, max_length=4)
    company_facts: list[CompactCompanyFact] = Field(default_factory=list, max_length=12)
    risks_and_assumptions: list[ShortText] = Field(default_factory=list, max_length=3)


class ManagementSlice(BaseModel):
    company_facts: list[ManagementCompanyFact] = Field(default_factory=list, max_length=2)
    risks_and_assumptions: list[ManagementShortText] = Field(default_factory=list, max_length=1)


class OwnershipNetworkSlice(BaseModel):
    company_facts: list[OwnershipNetworkCompanyFact] = Field(default_factory=list, max_length=3)
    risks_and_assumptions: list[ManagementShortText] = Field(default_factory=list, max_length=1)


class OperationsProfileSlice(BaseModel):
    company_facts: list[CompactCompanyFact] = Field(default_factory=list, max_length=12)
    risks_and_assumptions: list[ShortText] = Field(default_factory=list, max_length=4)


class SignalProfileSlice(BaseModel):
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=8)
    risks_and_assumptions: list[ShortText] = Field(default_factory=list, max_length=4)


class DeepIdentitySlice(IdentityCoreSlice):
    company_name: str
    business_summary: str
    evidence: list[str] = Field(default_factory=list)
    company_facts: list[CompanyFact] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)


class DeepManagementFact(CompanyFact):
    field: Literal["founders", "executives"]


class DeepManagementSlice(ManagementSlice):
    company_facts: list[DeepManagementFact] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)


class DeepOwnershipFact(CompanyFact):
    field: Literal["beneficial_owners", "affiliates", "social_accounts"]


class DeepOwnershipSlice(OwnershipNetworkSlice):
    company_facts: list[DeepOwnershipFact] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)


class DeepOperationsSlice(OperationsProfileSlice):
    company_facts: list[CompanyFact] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)


class DeepSignalSlice(SignalProfileSlice):
    economic_signals: list[EconomicSignal] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)


_DEEP_MODELS = {IdentityCoreSlice: DeepIdentitySlice, ManagementSlice: DeepManagementSlice,
                OwnershipNetworkSlice: DeepOwnershipSlice, OperationsProfileSlice: DeepOperationsSlice,
                SignalProfileSlice: DeepSignalSlice}


@dataclass(frozen=True)
class MergedProfileExtraction:
    company_name: str
    business_summary: str
    evidence: list[str]
    company_facts: list[CompanyFact]
    economic_signals: list[EconomicSignal]
    risks_and_assumptions: list[str]
    coverage: EvidenceCoverage


RequestJson = Callable[..., Awaitable[BaseModel]]

_IDENTITY_CORE_KINDS = {"official", "contact", "registry"}
_MANAGEMENT_KINDS = {"official", "registry", "ownership"}
_OWNERSHIP_NETWORK_KINDS = {
    "official", "registry", "ownership", "affiliation", "social",
}
_OPERATIONS_KINDS = {
    "finance", "workforce", "jobs", "tender", "patent", "other",
}
_SIGNAL_KINDS = {
    "arbitration", "court", "enforcement", "news", "review", "social",
    "tender", "finance", "other",
}
_ALL_ROUTED_KINDS = (
    _IDENTITY_CORE_KINDS
    | _MANAGEMENT_KINDS
    | _OWNERSHIP_NETWORK_KINDS
    | _OPERATIONS_KINDS
    | _SIGNAL_KINDS
)
_SLICE_SOURCE_KEYS = (
    "id", "title", "query_kind", "result_kind", "source_class",
    "evidence_level", "snippet", "lifecycle_state", "url",
    "document_url", "evidence_locator", "evidence_digest",
)

# Broad profile slices inspect every official-site chunk. Narrow slices inspect only
# chunks with deterministic topical markers; their external evidence remains routed
# independently below. This prevents multiplying a large official corpus across all
# five LLM extractors while retaining relevant first-party management/network/signal text.
_BROAD_OFFICIAL_SLICES = {"identity", "operations"}
_OFFICIAL_SLICE_HINTS = {
    "management": (
        "директор", "руководител", "генеральн", "главн", "founder", "ceo",
        "executive", "management", "команда", "врач", "доктор",
    ),
    "ownership": (
        "учредител", "собственник", "владел", "бенефициар", "аффилир",
        "owner", "ownership", "beneficial", "vk.com", "t.me/", "telegram",
        "instagram", "youtube", "соцсет",
    ),
    "signals": (
        "суд", "арбитраж", "исполнительн", "отзыв", "ваканси", "новост",
        "тендер", "патент", "court", "review", "news", "vacanc", "tender",
        "enforcement", "иск ",
    ),
}


def _slice_evidence_units(
    official_text: str,
    projected_sources: list[dict[str, Any]],
    slice_name: str,
    *,
    chunk_chars: int = DEFAULT_EVIDENCE_CHUNK_CHARS,
) -> list[tuple[str, str]]:
    official_chunks = chunk_text(official_text, chunk_chars=chunk_chars)
    if slice_name not in _BROAD_OFFICIAL_SLICES:
        hints = _OFFICIAL_SLICE_HINTS.get(slice_name, ())
        official_chunks = [
            chunk for chunk in official_chunks
            if any(hint in chunk.casefold() for hint in hints)
        ]
    source_chunks = chunk_sources(projected_sources, chunk_chars=chunk_chars)
    units = [(chunk, "[]") for chunk in official_chunks]
    units.extend(("", chunk) for chunk in source_chunks)
    # Keep one empty unit so every vertical can deterministically return an empty DTO.
    return units or [("", "[]")]


def _source_slice(
    sources: list[dict[str, Any]],
    kinds: set[str],
    *,
    char_budget: int | None = None,
) -> str:
    """Compatibility helper: full projected evidence, never prefix-truncated."""
    del char_budget
    return json.dumps(
        project_sources(sources, kinds, _SLICE_SOURCE_KEYS),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _to_fact(item: CompactCompanyFact) -> CompanyFact:
    return CompanyFact(
        field=item.field,
        value=item.value,
        period=item.period,
        confidence=item.confidence,
        source_ids=item.source_ids,
        note="",
    )


def _to_signal(item: CompactEconomicSignal) -> EconomicSignal:
    return EconomicSignal(
        signal=item.signal,
        evidence=item.evidence,
        business_effect=item.business_effect,
        confidence=item.confidence,
        source_ids=item.source_ids,
    )


def _unique_text(parts: list[str], limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = " ".join(str(part).split()).strip()
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(normalized)
        if limit is not None and len(result) >= limit:
            break
    return result


def _merge_facts(*groups: list[CompactCompanyFact]) -> list[CompanyFact]:
    """Keep all distinct field/value/period facts; never apply a presentation cap."""
    result: list[CompanyFact] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for item in group:
            key = (
                item.field,
                " ".join(item.value.split()).casefold(),
                " ".join((item.period or "").split()).casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(_to_fact(item))
    return result


def _merge_signals(*groups: list[CompactEconomicSignal]) -> list[EconomicSignal]:
    result: list[EconomicSignal] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for item in group:
            key = (
                " ".join(item.signal.split()).casefold(),
                " ".join(item.evidence.split()).casefold(),
                " ".join(item.business_effect.split()).casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(_to_signal(item))
    return result


def _flatten_attr(results: list[BaseModel], attr: str) -> list[Any]:
    flattened: list[Any] = []
    for result in results:
        flattened.extend(getattr(result, attr))
    return flattened


async def extract_profile_parallel(
    *,
    request_json: RequestJson,
    strict_request_json: RequestJson | None = None,
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]],
    accessed_at: str,
) -> MergedProfileExtraction:
    """Coverage-preserving map→merge extraction followed by compact reasoning."""
    deep = deep_research_enabled()
    chunked = deep
    chunk_chars = DEEP_EVIDENCE_CHUNK_CHARS if deep else DEFAULT_EVIDENCE_CHUNK_CHARS
    control = current_research()
    processed_units = 0
    incomplete_reasons = []
    unrouted = [
        source for source in external_sources
        if str(source.get("query_kind") or "unknown") not in _ALL_ROUTED_KINDS
    ]
    if unrouted:
        raise EvidenceCoverageOverflow(f"unrouted_sources={len(unrouted)}")

    projected_by_slice = {
        "identity": project_sources(external_sources, _IDENTITY_CORE_KINDS, _SLICE_SOURCE_KEYS),
        "management": project_sources(external_sources, _MANAGEMENT_KINDS, _SLICE_SOURCE_KEYS),
        "ownership": project_sources(external_sources, _OWNERSHIP_NETWORK_KINDS, _SLICE_SOURCE_KEYS),
        "operations": project_sources(external_sources, _OPERATIONS_KINDS, _SLICE_SOURCE_KEYS),
        "signals": project_sources(external_sources, _SIGNAL_KINDS, _SLICE_SOURCE_KEYS),
    }
    units_by_slice = {
        name: _slice_evidence_units(text, projected, name, chunk_chars=chunk_chars)
        for name, projected in projected_by_slice.items()
    }

    # A large corpus is a scheduling decision, not a reason to abandon synthesis.
    chunked = chunked or any(len(units) > DEFAULT_MAX_FAST_PATH_UNITS for units in units_by_slice.values())
    if chunked:
        control = control or ResearchControl()
        control.checkpoint("corpus", {"url": url, "title": title, "text": text, "sources": external_sources})

    def deterministic_request(phase, model_type, **kwargs):
        if strict_request_json is not None:
            return strict_request_json(
                phase, model_type, reasoning_enabled=False, **kwargs
            )
        return request_json(phase, model_type, **kwargs)

    common_rules = """
Правила:
- Это один coverage chunk полного evidence ledger; другие chunks обрабатываются отдельно.
- S1 — официальный сайт; внешние источники имеют переданные id.
- Не создавай факты, цифры, людей, контакты или source_ids, которых нет во входе.
- Поисковый сниппет — сигнал, не проверенный первичный документ.
- Пиши кратко; не повторяй один факт разными словами.
- Не скрывай отдельные значения/периоды одного поля: если они различны и помещаются в схему, верни отдельные факты.
- Если данных в этом chunk нет, верни пустые массивы, а не выдумывай их.
"""

    async def run_units(
        phase: str,
        model_type: type[BaseModel],
        slice_name: str,
        *,
        system: str,
        instructions: str,
        max_tokens: int,
        timeout_seconds: float,
        include_accessed_at: bool = False,
    ) -> list[BaseModel]:
        nonlocal processed_units
        if chunked:
            output = []
            deep_model = _DEEP_MODELS[model_type]

            async def process(official, sources, key):
                if control.stop_requested:
                    return []
                prompt = (f"{instructions}\n{common_rules}\n"
                          "Сохрани ВСЕ отдельные факты, имена, значения и периоды без сжатия списков.\n"
                          f"URL: {url}\nTITLE: {title}\nCHUNK: {key}\n"
                          f"OFFICIAL PAGE TEXT CHUNK:\n{official}\nRELEVANT SOURCES CHUNK:\n{sources}")
                try:
                    async with control.semaphore:
                        if control.stop_requested:
                            return []
                        with bind_research(control):
                            item = await deterministic_request(
                                phase, deep_model, system=system, prompt=prompt,
                                max_tokens=8192, timeout_seconds=120.0,
                            )
                except Exception as exc:
                    if getattr(exc, "error_type", None) != "OutputTruncated":
                        raise
                    # Subdivide the evidence, not the returned fact list.
                    if len(official) > 1:
                        midpoint = len(official) // 2
                        pieces = [(official[:midpoint], "[]"), (official[midpoint:], "[]")]
                    else:
                        records = json.loads(sources)
                        if len(records) > 1:
                            midpoint = len(records) // 2
                            pieces = [("", json.dumps(records[:midpoint], ensure_ascii=False)),
                                      ("", json.dumps(records[midpoint:], ensure_ascii=False))]
                        elif records and len(records[0].get("snippet", "")) > 1:
                            content = records[0]["snippet"]
                            midpoint = len(content) // 2
                            pieces = [("", json.dumps([{**records[0], "snippet": part}], ensure_ascii=False))
                                      for part in (content[:midpoint], content[midpoint:])]
                        else:
                            raise
                    result = []
                    for index, (part, context) in enumerate(pieces):
                        result.extend(await process(part, context, f"{key}.{index}"))
                    return result
                control.completed_chunks += 1
                control.checkpoint(f"{phase}/{key}", item.model_dump(mode="json"))
                output.append(item)
                return [item]

            for index, (official, sources) in enumerate(units_by_slice[slice_name]):
                if control.stop_requested:
                    break
                try:
                    await process(official, sources, str(index))
                    if not control.stop_requested:
                        processed_units += 1
                except Exception as exc:
                    incomplete_reasons.append(f"{phase}: {type(exc).__name__}")
                    control.checkpoint(f"{phase}/failure", {"error_type": type(exc).__name__, "chunk": index})
                    break
            return output
        calls = []
        units = units_by_slice[slice_name]
        for index, (official_chunk, source_chunk) in enumerate(units, start=1):
            prompt = f"""{instructions}
{common_rules}
URL: {url}
TITLE: {title}
CHUNK: {index}/{len(units)}
{f'ACCESSED AT: {accessed_at}' if include_accessed_at else ''}
OFFICIAL PAGE TEXT CHUNK:\n{official_chunk}
RELEVANT SOURCES CHUNK:\n{source_chunk}
"""
            calls.append(
                deterministic_request(
                    phase,
                    model_type,
                    system=system,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
            )
        return list(await asyncio.gather(*calls))

    identity_task = run_units(
        "profile_identity_core",
        IdentityCoreSlice,
        "identity",
        system="Возвращай только компактный валидный JSON по схеме.",
        instructions="""Извлеки только базовую идентичность и прямые контакты компании.
Разрешённые поля: legal_name, brand_name, inn, ogrn, registration_status, address,
phones, emails, website, geography. Не извлекай собственников, руководителей,
аффилированность, соцсети, финансы и коммерческое решение.""",
        max_tokens=850,
        timeout_seconds=20.0,
        include_accessed_at=True,
    )
    management_task = run_units(
        "profile_management",
        ManagementSlice,
        "management",
        system="Возвращай только компактный валидный JSON по схеме.",
        instructions="""Извлеки только сведения о формальном управлении компанией.
Разрешённые поля: founders, executives. Объединяй список лиц одного типа компактно,
но не теряй разные подтверждённые значения. Учредитель не является автоматически
бенефициаром. Не извлекай affiliates, social_accounts, контакты, финансы и продукты.""",
        max_tokens=900,
        timeout_seconds=18.0,
    )
    ownership_task = run_units(
        "profile_ownership_network",
        OwnershipNetworkSlice,
        "ownership",
        system="Возвращай только компактный валидный JSON по схеме.",
        instructions="""Извлеки только ownership/network сведения компании.
Разрешённые поля: beneficial_owners, affiliates, social_accounts. Бенефициарность
и аффилированность маркируй осторожно и не выводи только из факта учредительства.
Не повторяй founders/executives, адреса, телефоны, финансы и продукты.""",
        max_tokens=1000,
        timeout_seconds=18.0,
    )
    operations_task = run_units(
        "profile_operations",
        OperationsProfileSlice,
        "operations",
        system="Возвращай только компактный валидный JSON по схеме.",
        instructions="""Извлеки операционные и экономические факты компании.
Разрешённые поля: headcount, revenue, profit, assets, taxes, products, customers,
suppliers, other. Для финансовых значений обязательно указывай period, если он
виден. Разные периоды одного показателя сохраняй как разные факты. Не формируй
economic_signals и не делай коммерческое предложение.""",
        max_tokens=1100,
        timeout_seconds=22.0,
    )
    signals_task = run_units(
        "profile_signals",
        SignalProfileSlice,
        "signals",
        system="Возвращай только компактный валидный JSON по схеме.",
        instructions="""Извлеки только экономические/правовые/рыночные сигналы, которые
могут влиять на бизнес или AI-пилот. Для каждого сигнала дай короткое evidence,
business_effect и реальные source_ids. Не повторяй профиль компании.""",
        max_tokens=1000,
        timeout_seconds=22.0,
    )

    identity_results, management_results, ownership_results, operations_results, signal_results = await asyncio.gather(
        identity_task,
        management_task,
        ownership_task,
        operations_task,
        signals_task,
    )

    identity_results = [item for item in identity_results if isinstance(item, IdentityCoreSlice)]
    management_results = [item for item in management_results if isinstance(item, ManagementSlice)]
    ownership_results = [item for item in ownership_results if isinstance(item, OwnershipNetworkSlice)]
    operations_results = [item for item in operations_results if isinstance(item, OperationsProfileSlice)]
    signal_results = [item for item in signal_results if isinstance(item, SignalProfileSlice)]

    company_name = next((item.company_name for item in identity_results if item.company_name.strip()), title)
    business_summary = next((item.business_summary for item in identity_results if item.business_summary.strip()), "")
    evidence = _unique_text(_flatten_attr(identity_results, "evidence"))
    facts = _merge_facts(
        _flatten_attr(identity_results, "company_facts"),
        _flatten_attr(management_results, "company_facts"),
        _flatten_attr(ownership_results, "company_facts"),
        _flatten_attr(operations_results, "company_facts"),
    )
    signals = _merge_signals(_flatten_attr(signal_results, "economic_signals"))
    risks = _unique_text(
        _flatten_attr(identity_results, "risks_and_assumptions")
        + _flatten_attr(management_results, "risks_and_assumptions")
        + _flatten_attr(ownership_results, "risks_and_assumptions")
        + _flatten_attr(operations_results, "risks_and_assumptions")
        + _flatten_attr(signal_results, "risks_and_assumptions")
    )

    official_chunks = chunk_text(text, chunk_chars=chunk_chars)
    source_chunk_count = sum(
        len(chunk_sources(projected, chunk_chars=chunk_chars))
        for projected in projected_by_slice.values()
    )
    unit_count = sum(len(units) for units in units_by_slice.values())
    complete = not chunked or (processed_units == unit_count and not incomplete_reasons)
    if not complete:
        risks.append("Углублённое извлечение неполное: остановлено пользователем или ошибкой; сохранены обработанные порции.")
    coverage = EvidenceCoverage(
        official_chars_total=len(text),
        official_chunks_total=len(official_chunks),
        official_chunks_processed=len(official_chunks) if complete else 0,
        sources_total=len(external_sources),
        sources_processed=len(external_sources) if complete else 0,
        source_chunks_total=source_chunk_count,
        source_chunks_processed=source_chunk_count if complete else 0,
        extraction_units_total=unit_count,
        extraction_units_processed=unit_count if not chunked else processed_units,
        complete=complete,
    )

    return MergedProfileExtraction(
        company_name=company_name,
        business_summary=business_summary,
        evidence=evidence,
        company_facts=facts,
        economic_signals=signals,
        risks_and_assumptions=risks,
        coverage=coverage,
    )
