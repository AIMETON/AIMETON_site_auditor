from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from app.models import CompanyFact, EconomicSignal


_CHILD_ID = re.compile(r"^(?P<parent>.+)-b\d+-\d+$")
_TRIAGE_RELATION = re.compile(r"Evidence triage:\s*([a-z_]+)/[a-z_]+;")
_PLACEHOLDER = re.compile(
    r"^(?:нет данных|данные отсутствуют|не найдено|не найден|не указано|не указана|"
    r"неизвестно|unknown|n/?a|none|отсутствует|в этом чанке[^.]*)[.!\s]*$",
    re.IGNORECASE,
)
_SENSITIVE_FIELDS = {
    "inn", "ogrn", "founders", "executives", "beneficial_owners",
    "revenue", "profit", "assets", "taxes",
}
_REJECTED_RELATIONS = {
    "publisher", "competitor", "counterparty", "mentioned_only", "unknown",
}
_CONFIDENCE_RANK = {"Низкая": 0, "Средняя": 1, "Высокая": 2}
_STANDALONE_MONEY = re.compile(
    r"^(?:от\s*)?\d[\d\s.,]*(?:₽|руб(?:\.|лей)?)\.?$",
    re.IGNORECASE,
)
_PRODUCT_PRICE = re.compile(
    r"(?:(?:цена|стоимость)\s*[:\-–—]?\s*)?(?:от\s*)?"
    r"\d[\d\s.,]*(?:₽|руб(?:\.|лей)?)",
    re.IGNORECASE,
)
_PRODUCT_PROMO_TAG = re.compile(
    r"\((?:акция|спецпредложение|скидка|цена|стоимость)[^)]*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConsolidationStats:
    input_facts: int
    output_facts: int
    placeholders_removed: int
    semantic_duplicates_merged: int
    foreign_sensitive_facts_rejected: int
    low_information_other_removed: int
    input_signals: int
    output_signals: int
    product_facts_input: int
    product_facts_output: int
    other_facts_input: int
    other_facts_output: int

    def safe_dict(self) -> dict[str, int]:
        return {
            "input_facts": self.input_facts,
            "output_facts": self.output_facts,
            "placeholders_removed": self.placeholders_removed,
            "semantic_duplicates_merged": self.semantic_duplicates_merged,
            "foreign_sensitive_facts_rejected": self.foreign_sensitive_facts_rejected,
            "low_information_other_removed": self.low_information_other_removed,
            "input_signals": self.input_signals,
            "output_signals": self.output_signals,
            "product_facts_input": self.product_facts_input,
            "product_facts_output": self.product_facts_output,
            "other_facts_input": self.other_facts_input,
            "other_facts_output": self.other_facts_output,
        }


def _parent_id(source_id: str) -> str:
    match = _CHILD_ID.match(source_id)
    return match.group("parent") if match else source_id


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.fullmatch(_clean_text(value)))


def _is_low_information_other(fact: CompanyFact) -> bool:
    """Reject orphan monetary values that have no business object attached.

    A price such as "от 1 200 руб." is not a standalone company fact. Labeled
    product/service prices remain intact because their text contains semantic context.
    """
    return fact.field == "other" and bool(_STANDALONE_MONEY.fullmatch(_clean_text(fact.value)))


def _normalized_key(field: str, value: str) -> str:
    compact = _clean_text(value)
    if field in {"inn", "ogrn"}:
        digits = re.sub(r"\D", "", compact)
        return digits or compact.casefold()
    if field == "phones":
        digits = re.sub(r"\D", "", compact)
        if len(digits) >= 10:
            return digits[-10:]
    if field == "emails":
        return compact.casefold()
    if field == "website":
        raw = compact if "://" in compact else f"https://{compact}"
        try:
            parsed = urlsplit(raw)
            host = (parsed.hostname or "").removeprefix("www.").casefold()
            path = parsed.path.rstrip("/")
            return f"{host}{path}"
        except Exception:
            pass
    if field == "products":
        compact = _PRODUCT_PROMO_TAG.sub(" ", compact)
        compact = _PRODUCT_PRICE.sub(" ", compact)
        compact = re.sub(r"\b(?:акция|спецпредложение|скидка)\b", " ", compact, flags=re.IGNORECASE)
        compact = " ".join(compact.split()).strip(" -–—:;,")
    folded = compact.casefold()
    folded = folded.translate(str.maketrans({
        "«": '"', "»": '"', "„": '"', "“": '"', "”": '"', "’": "'", "`": "'",
    }))
    if field in {"legal_name", "brand_name"}:
        folded = re.sub(r"^(?:ооо|ао|пао|зао|оао|ип)\s+", "", folded)
    folded = re.sub(r"[^0-9a-zа-яё]+", " ", folded, flags=re.IGNORECASE)
    return " ".join(folded.split())


def _source_relations(external_sources: list[dict[str, Any]]) -> dict[str, str]:
    relations: dict[str, str] = {"S1": "target"}
    for source in external_sources:
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            continue
        relation: str | None = None
        if str(source.get("source_class") or "") == "official" or str(source.get("query_kind") or "") == "official":
            relation = "target"
        note = str(source.get("verification_note") or "")
        match = _TRIAGE_RELATION.search(note)
        if match:
            relation = match.group(1)
        if relation is None and source.get("lifecycle_state") == "evidence":
            # Parent document identity was already verified before promotion. Child
            # records carry the stricter block relation when it differs.
            relation = "target"
        if relation:
            relations[source_id] = relation
            relations.setdefault(_parent_id(source_id), relation)
    return relations


def _foreign_only_sensitive_fact(fact: CompanyFact, relations: dict[str, str]) -> bool:
    if fact.field not in _SENSITIVE_FIELDS or not fact.source_ids:
        return False
    observed = [relations.get(source_id) or relations.get(_parent_id(source_id)) for source_id in fact.source_ids]
    known = [relation for relation in observed if relation]
    return bool(known) and all(relation in _REJECTED_RELATIONS for relation in known)


def _merge_fact(existing: CompanyFact, incoming: CompanyFact) -> CompanyFact:
    source_ids: list[str] = []
    seen: set[str] = set()
    for source_id in [*existing.source_ids, *incoming.source_ids]:
        parent = _parent_id(source_id)
        if parent in seen:
            continue
        seen.add(parent)
        source_ids.append(parent)
    confidence = existing.confidence
    if _CONFIDENCE_RANK.get(incoming.confidence, 0) > _CONFIDENCE_RANK.get(existing.confidence, 0):
        confidence = incoming.confidence
    note_parts = []
    for note in (existing.note, incoming.note):
        note = _clean_text(note)
        if note and note not in note_parts:
            note_parts.append(note)
    return existing.model_copy(update={
        "source_ids": source_ids,
        "confidence": confidence,
        "note": "; ".join(note_parts),
    })


def consolidate_facts(
    facts: list[CompanyFact],
    *,
    external_sources: list[dict[str, Any]],
) -> tuple[list[CompanyFact], int, int, int]:
    relations = _source_relations(external_sources)
    merged: dict[tuple[str, str, str], CompanyFact] = {}
    order: list[tuple[str, str, str]] = []
    placeholders_removed = 0
    duplicates_merged = 0
    foreign_rejected = 0

    for fact in facts:
        if _is_placeholder(fact.value):
            placeholders_removed += 1
            continue
        if _foreign_only_sensitive_fact(fact, relations):
            foreign_rejected += 1
            continue
        key = (
            fact.field,
            _normalized_key(fact.field, fact.value),
            _normalized_key("period", fact.period or ""),
        )
        if not key[1]:
            placeholders_removed += 1
            continue
        if key in merged:
            merged[key] = _merge_fact(merged[key], fact)
            duplicates_merged += 1
            continue
        order.append(key)
        normalized_sources: list[str] = []
        seen_sources: set[str] = set()
        for source_id in fact.source_ids:
            parent = _parent_id(source_id)
            if parent not in seen_sources:
                seen_sources.add(parent)
                normalized_sources.append(parent)
        merged[key] = fact.model_copy(update={"source_ids": normalized_sources})

    return [merged[key] for key in order], placeholders_removed, duplicates_merged, foreign_rejected


def _canonical_company_name(current: str, facts: list[CompanyFact]) -> str:
    """Prefer consolidated identity facts over a per-chunk free-text name.

    Every identity chunk must emit company_name even when the chunk only carries
    generic site copy. A later verified registry/brand fact is a stronger canonical
    identity signal than the first chunk's SEO title.
    """
    for field in ("brand_name", "legal_name"):
        candidates = [
            fact for fact in facts
            if fact.field == field and not _is_placeholder(fact.value) and _clean_text(fact.value)
        ]
        if candidates:
            best = max(
                enumerate(candidates),
                key=lambda item: (_CONFIDENCE_RANK.get(item[1].confidence, 0), -item[0]),
            )[1]
            return _clean_text(best.value)
    return _clean_text(current)

def consolidate_signals(signals: list[EconomicSignal]) -> list[EconomicSignal]:
    result: list[EconomicSignal] = []
    seen: set[tuple[str, str, str]] = set()
    for signal in signals:
        if _is_placeholder(signal.signal) or _is_placeholder(signal.evidence):
            continue
        key = (
            _normalized_key("signal", signal.signal),
            _normalized_key("evidence", signal.evidence),
            _normalized_key("effect", signal.business_effect),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(signal.model_copy(update={
            "source_ids": list(dict.fromkeys(_parent_id(source_id) for source_id in signal.source_ids)),
        }))
    return result


def consolidate_merged_profile(merged, *, external_sources: list[dict[str, Any]]):
    """Build the deterministic clean profile consumed by expensive reasoning.

    The full extraction ledger remains persisted separately. Consolidation removes
    placeholders, merges formatting-equivalent facts and rejects sensitive facts
    whose known provenance is only publisher/competitor/mentioned-only evidence.
    """
    filtered_facts = [
        fact for fact in merged.company_facts
        if not _is_low_information_other(fact)
    ]
    low_information_other_removed = len(merged.company_facts) - len(filtered_facts)
    facts, placeholders, duplicates, foreign = consolidate_facts(
        filtered_facts,
        external_sources=external_sources,
    )
    signals = consolidate_signals(merged.economic_signals)
    stats = ConsolidationStats(
        input_facts=len(merged.company_facts),
        output_facts=len(facts),
        placeholders_removed=placeholders,
        semantic_duplicates_merged=duplicates,
        foreign_sensitive_facts_rejected=foreign,
        low_information_other_removed=low_information_other_removed,
        input_signals=len(merged.economic_signals),
        output_signals=len(signals),
        product_facts_input=sum(fact.field == "products" for fact in merged.company_facts),
        product_facts_output=sum(fact.field == "products" for fact in facts),
        other_facts_input=sum(fact.field == "other" for fact in merged.company_facts),
        other_facts_output=sum(fact.field == "other" for fact in facts),
    )
    risks = list(merged.risks_and_assumptions)
    if placeholders or duplicates or foreign or low_information_other_removed:
        risks.append(
            "Консолидация профиля перед reasoning: "
            f"удалено пустых/служебных фактов={placeholders}, "
            f"удалено standalone price/no-context other={low_information_other_removed}, "
            f"объединено семантических дублей={duplicates}, "
            f"отклонено sensitive-фактов с чужим provenance={foreign}."
        )
    return replace(
        merged,
        company_name=_canonical_company_name(merged.company_name, facts),
        company_facts=facts,
        economic_signals=signals,
        risks_and_assumptions=risks,
    ), stats
