from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evidence_source_projection import evidence_parent_id
from app.research_control import deep_research_enabled
from app.external_sources import IdentityAnchors
from app.fast_research_model import request_fast_json
from app.models import IntelligenceSource, SourceKind


CoverageState = Literal["covered", "searched_no_evidence", "missing"]

# Search completeness and evidence sufficiency are intentionally separate. A vertical
# may be searched without finding evidence; that stops duplicate exact waves but never
# turns absence into a verified company fact.
MANDATORY_VERTICAL_KINDS: dict[str, tuple[SourceKind, ...]] = {
    "identity": ("official", "registry"),
    "contacts": ("contact",),
    "ownership": ("ownership",),
    "financials": ("finance",),
    "workforce": ("workforce", "jobs"),
    "legal_events": ("arbitration", "court", "enforcement"),
    "operations": ("other",),
}

# The first deep wave deliberately covers only the business-critical directions.
# Official-site crawling can discover first-party subpages after this initial fetch.
INITIAL_CORE_KINDS: tuple[SourceKind, ...] = (
    "official",
    "registry",
    "contact",
    "ownership",
    "finance",
    "other",
)

ENRICHMENT_KINDS: tuple[SourceKind, ...] = (
    "affiliation",
    "news",
    "review",
    "social",
    "tender",
    "patent",
)

DEFAULT_DEEP_RESULTS_PER_QUERY = 8

_EVIDENCE_LEVEL_RANK = {
    "unverified_mention": 0,
    "weak_signal": 1,
    "corroborated_signal": 2,
    "confirmed_fact": 3,
}
_MIN_VERTICAL_EVIDENCE_LEVEL = {
    "identity": "corroborated_signal",
    "contacts": "weak_signal",
    "ownership": "weak_signal",
    "financials": "corroborated_signal",
    "workforce": "weak_signal",
    "legal_events": "corroborated_signal",
    "operations": "weak_signal",
}


@dataclass(frozen=True)
class CoverageSnapshot:
    states: dict[str, CoverageState]
    searched_kinds: frozenset[SourceKind]
    evidence_kinds: frozenset[SourceKind]
    evidence_documents_by_kind: dict[SourceKind, int]
    qualifying_documents_by_vertical: dict[str, int]

    @property
    def missing_verticals(self) -> tuple[str, ...]:
        return tuple(name for name, state in self.states.items() if state == "missing")

    @property
    def searched_without_evidence(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, state in self.states.items()
            if state == "searched_no_evidence"
        )

    @property
    def search_complete(self) -> bool:
        return not self.missing_verticals

    @property
    def evidence_sufficient(self) -> bool:
        return bool(self.states) and all(
            state == "covered" for state in self.states.values()
        )

    def safe_dict(self) -> dict[str, object]:
        return {
            "states": dict(self.states),
            "missing_verticals": list(self.missing_verticals),
            "searched_without_evidence": list(self.searched_without_evidence),
            "search_complete": self.search_complete,
            "evidence_sufficient": self.evidence_sufficient,
            "searched_kinds": sorted(self.searched_kinds),
            "evidence_kinds": sorted(self.evidence_kinds),
            "evidence_documents_by_kind": {
                str(kind): count
                for kind, count in sorted(self.evidence_documents_by_kind.items())
            },
            "qualifying_documents_by_vertical": dict(self.qualifying_documents_by_vertical),
        }


@dataclass(frozen=True)
class WaveSelection:
    queries: tuple[tuple[SourceKind, str], ...]
    candidate_count: int
    model_used: bool = False
    model_unavailable: bool = False

    def safe_dict(self) -> dict[str, object]:
        return {
            "queries": len(self.queries),
            "candidate_count": self.candidate_count,
            "model_used": self.model_used,
            "model_unavailable": self.model_unavailable,
            "query_kinds": [kind for kind, _ in self.queries],
        }


class FastCoverageWaveChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class SemanticGainDecision(BaseModel):
    """Fast-model judgment of whether a completed wave added business meaning."""

    model_config = ConfigDict(extra="forbid")
    meaningful_gain: bool
    new_information: list[str] = Field(default_factory=list)
    authority_gain: list[str] = Field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class SemanticGainAssessment:
    meaningful_gain: bool
    semantic_state: tuple[str, ...]
    model_used: bool = False
    model_unavailable: bool = False
    reason: str = ""

    def safe_dict(self) -> dict[str, object]:
        return {
            "meaningful_gain": self.meaningful_gain,
            "semantic_state_items": len(self.semantic_state),
            "model_used": self.model_used,
            "model_unavailable": self.model_unavailable,
            "reason": self.reason,
        }


def _document_kinds(
    verified: Iterable[IntelligenceSource],
) -> dict[SourceKind, set[str]]:
    by_kind: dict[SourceKind, set[str]] = {}
    for item in verified:
        if item.lifecycle_state != "evidence":
            continue
        by_kind.setdefault(item.query_kind, set()).add(evidence_parent_id(item.id))
    return by_kind


def evidence_query_kinds(
    verified: Iterable[IntelligenceSource],
) -> frozenset[SourceKind]:
    return frozenset(_document_kinds(verified))


def _qualifying_documents_by_vertical(
    verified: Iterable[IntelligenceSource],
) -> dict[str, set[str]]:
    qualifying = {vertical: set() for vertical in MANDATORY_VERTICAL_KINDS}
    for item in verified:
        if item.lifecycle_state != "evidence":
            continue
        level = _EVIDENCE_LEVEL_RANK.get(str(item.evidence_level), 0)
        parent_id = evidence_parent_id(item.id)
        for vertical, kinds in MANDATORY_VERTICAL_KINDS.items():
            minimum = _EVIDENCE_LEVEL_RANK[_MIN_VERTICAL_EVIDENCE_LEVEL[vertical]]
            if item.query_kind in kinds and level >= minimum:
                qualifying[vertical].add(parent_id)
    return qualifying


def assess_coverage(
    verified: Iterable[IntelligenceSource],
    searched_kinds: Iterable[SourceKind],
) -> CoverageSnapshot:
    searched = frozenset(searched_kinds)
    verified = list(verified)
    document_kinds = _document_kinds(verified)
    evidence = frozenset(document_kinds)
    qualifying = _qualifying_documents_by_vertical(verified)
    states: dict[str, CoverageState] = {}

    for vertical, kinds in MANDATORY_VERTICAL_KINDS.items():
        kind_set = set(kinds)
        if qualifying[vertical]:
            states[vertical] = "covered"
        elif searched.intersection(kind_set):
            # Searched evidence below the vertical's authority threshold is not
            # sufficient to close the gap; bounded recovery remains eligible.
            states[vertical] = "searched_no_evidence"
        else:
            states[vertical] = "missing"

    return CoverageSnapshot(
        states=states,
        searched_kinds=searched,
        evidence_kinds=evidence,
        evidence_documents_by_kind={
            kind: len(documents)
            for kind, documents in document_kinds.items()
        },
        qualifying_documents_by_vertical={
            vertical: len(documents)
            for vertical, documents in qualifying.items()
        },
    )


def _first_queries_for_kinds(
    plan: Iterable[tuple[SourceKind, str]],
    kinds: Iterable[SourceKind],
    *,
    already_attempted: set[str] | None = None,
) -> list[tuple[SourceKind, str]]:
    wanted = set(kinds)
    attempted = already_attempted or set()
    selected: list[tuple[SourceKind, str]] = []
    seen_kind: set[SourceKind] = set()

    for kind, query in plan:
        if kind not in wanted or kind in seen_kind or query in attempted:
            continue
        seen_kind.add(kind)
        selected.append((kind, query))
    return selected


def initial_wave(
    full_plan: Iterable[tuple[SourceKind, str]],
) -> list[tuple[SourceKind, str]]:
    """One exact query per business-critical kind, not the full 20-query plan."""
    return _first_queries_for_kinds(full_plan, INITIAL_CORE_KINDS)


def gap_wave(
    full_plan: Iterable[tuple[SourceKind, str]],
    coverage: CoverageSnapshot,
    *,
    already_attempted: set[str],
) -> list[tuple[SourceKind, str]]:
    """Search only mandatory directions that have not been searched at all."""
    missing_kinds: list[SourceKind] = []
    for vertical in coverage.missing_verticals:
        missing_kinds.extend(MANDATORY_VERTICAL_KINDS[vertical])
    return _first_queries_for_kinds(
        full_plan,
        missing_kinds,
        already_attempted=already_attempted,
    )


def enrichment_wave(
    full_plan: Iterable[tuple[SourceKind, str]],
    *,
    already_attempted: set[str],
) -> list[tuple[SourceKind, str]]:
    return _first_queries_for_kinds(
        full_plan,
        ENRICHMENT_KINDS,
        already_attempted=already_attempted,
    )


def relaxed_recovery_wave(
    *,
    company_name: str,
    anchors: IdentityAnchors,
    coverage: CoverageSnapshot,
    already_attempted: set[str],
) -> list[tuple[SourceKind, str]]:
    """One relaxed recovery query per searched-but-empty mandatory vertical."""
    from app.adaptive_external_sources import relaxed_query

    selected: list[tuple[SourceKind, str]] = []
    used_kinds: set[SourceKind] = set()
    for vertical in coverage.searched_without_evidence:
        for kind in MANDATORY_VERTICAL_KINDS[vertical]:
            if kind in used_kinds:
                continue
            query = relaxed_query(kind, company_name, anchors=anchors)
            if not query or query in already_attempted:
                continue
            selected.append((kind, query))
            used_kinds.add(kind)
            break
    return selected


def _deduplicate_queries(
    queries: Iterable[tuple[SourceKind, str]],
    *,
    already_attempted: set[str],
) -> list[tuple[SourceKind, str]]:
    result: list[tuple[SourceKind, str]] = []
    seen = set(already_attempted)
    for kind, query in queries:
        if not query or query in seen:
            continue
        seen.add(query)
        result.append((kind, query))
    return result



def _coverage_state_rank(state: CoverageState) -> int:
    return {"missing": 0, "searched_no_evidence": 1, "covered": 2}[state]


def _structural_semantic_gain(
    before: CoverageSnapshot,
    after: CoverageSnapshot,
) -> tuple[bool, list[str]]:
    gains: list[str] = []
    for vertical, after_state in after.states.items():
        before_state = before.states.get(vertical, "missing")
        if _coverage_state_rank(after_state) > _coverage_state_rank(before_state):
            gains.append(f"{vertical}:{before_state}->{after_state}")
            continue
        before_docs = before.qualifying_documents_by_vertical.get(vertical, 0)
        after_docs = after.qualifying_documents_by_vertical.get(vertical, 0)
        if after_docs > before_docs:
            gains.append(f"{vertical}:authority_evidence_added")
    return bool(gains), gains


def _semantic_evidence_rows(items: Iterable[IntelligenceSource]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        if item.lifecycle_state != "evidence":
            continue
        text = " ".join(
            value.strip()
            for value in (
                str(item.title or ""),
                str(item.evidence_quote or ""),
                str(item.snippet or ""),
                str(item.verification_note or ""),
            )
            if value and str(value).strip()
        )
        rows.append({
            "id": str(item.id),
            "kind": str(item.query_kind),
            "source_class": str(item.source_class),
            "evidence_level": str(item.evidence_level),
            "content": text,
        })
    return rows


def _merge_semantic_state(
    state: Iterable[str],
    additions: Iterable[str],
) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*state, *additions]:
        compact = " ".join(str(value or "").split()).strip()
        key = compact.casefold()
        if not compact or key in seen:
            continue
        seen.add(key)
        merged.append(compact)
    return tuple(merged)


async def assess_wave_semantic_gain(
    before: CoverageSnapshot,
    after: CoverageSnapshot,
    *,
    new_verified: Iterable[IntelligenceSource],
    semantic_state: Iterable[str] = (),
    request_json=None,
) -> SemanticGainAssessment:
    """Judge marginal research value by meaning, not by document/query counts.

    A wave is useful when it adds a new business fact/entity/relation, closes or
    strengthens an evidence/authority gap, or changes the research picture. More
    documents that only restate existing meaning are not progress.
    """
    new_verified = list(new_verified)
    structural_gain, structural_reasons = _structural_semantic_gain(before, after)
    rows = _semantic_evidence_rows(new_verified)
    current_state = tuple(semantic_state)
    if not rows:
        return SemanticGainAssessment(
            meaningful_gain=structural_gain,
            semantic_state=_merge_semantic_state(current_state, structural_reasons),
            reason="; ".join(structural_reasons) or "no_new_verified_evidence",
        )

    request = request_json or request_fast_json
    try:
        response = await request(
            "research_semantic_gain",
            SemanticGainDecision,
            system=(
                "Ты Research Semantic Gain Judge. Оценивай прирост смысла, а не объёма. "
                "Новые документы сами по себе не являются прогрессом. meaningful_gain=true "
                "только если появилась новая бизнес-сущность, факт, отношение, изменение, "
                "новый уровень authority/corroboration или был закрыт содержательный пробел. "
                "Повторы, перефразы и дополнительные документы с тем же смыслом — false. "
                "Не извлекай коммерческие рекомендации."
            ),
            prompt=(
                "SEMANTIC STATE BEFORE:\n"
                + str(list(current_state))
                + "\nCOVERAGE BEFORE:\n"
                + str(before.safe_dict())
                + "\nCOVERAGE AFTER:\n"
                + str(after.safe_dict())
                + "\nNEW VERIFIED EVIDENCE:\n"
                + str(rows)
                + "\nВерни только действительно новые смысловые утверждения в "
                  "new_information и отдельно прирост authority в authority_gain."
            ),
            max_tokens=1200,
            timeout_seconds=15,
        )
        additions = [
            *response.new_information,
            *response.authority_gain,
            *structural_reasons,
        ]
        meaningful = bool(response.meaningful_gain or structural_gain)
        return SemanticGainAssessment(
            meaningful_gain=meaningful,
            semantic_state=(
                _merge_semantic_state(current_state, additions)
                if meaningful
                else current_state
            ),
            model_used=True,
            reason=response.reason,
        )
    except Exception:
        return SemanticGainAssessment(
            meaningful_gain=structural_gain,
            semantic_state=(
                _merge_semantic_state(current_state, structural_reasons)
                if structural_gain
                else current_state
            ),
            model_unavailable=True,
            reason=(
                "structural_coverage_gain_fallback"
                if structural_gain
                else "semantic_gain_model_unavailable_no_structural_gain"
            ),
        )


async def optional_wave(
    full_plan: Iterable[tuple[SourceKind, str]],
    coverage: CoverageSnapshot,
    *,
    company_name: str,
    anchors: IdentityAnchors,
    already_attempted: set[str],
    semantic_state: Iterable[str] = (),
    request_json=None,
) -> WaveSelection:
    """Let the fast model choose only queries with expected marginal semantic value.

    The candidate set remains deterministic and policy-bounded. The model may select
    any subset of those candidates or select none. There is no arbitrary query-count
    threshold: stopping is driven by expected/observed semantic gain.
    """
    recovery = relaxed_recovery_wave(
        company_name=company_name,
        anchors=anchors,
        coverage=coverage,
        already_attempted=already_attempted,
    )
    enrichment = enrichment_wave(
        full_plan,
        already_attempted=already_attempted,
    )
    candidates = _deduplicate_queries(
        [*recovery, *enrichment],
        already_attempted=already_attempted,
    )
    if not candidates:
        return WaveSelection(queries=(), candidate_count=0)

    rows = [
        {
            "id": f"Q{index}",
            "kind": kind,
            "mode": "recovery" if (kind, query) in recovery else "enrichment",
            "query": query,
        }
        for index, (kind, query) in enumerate(candidates)
    ]
    by_id = {row["id"]: candidates[index] for index, row in enumerate(rows)}
    request = request_json or request_fast_json

    try:
        response = await request(
            "research_coverage_wave",
            FastCoverageWaveChoice,
            system=(
                "Ты Research Coverage Router. Выбирай только query_ids из списка. "
                "Выбирай запрос только если он способен добавить новый бизнес-смысл, "
                "закрыть evidence/authority gap или проверить важное противоречие. "
                "Не выбирай запросы ради количества документов. Если ожидаемый смысловой "
                "прирост отсутствует, верни пустой query_ids."
            ),
            prompt=(
                "COVERAGE:\n"
                + str(coverage.safe_dict())
                + "\nCURRENT SEMANTIC STATE:\n"
                + str(list(semantic_state))
                + "\nCANDIDATE QUERIES:\n"
                + str(rows)
                + "\nВыбери только query_ids с ожидаемым независимым смысловым приростом."
            ),
            max_tokens=1000,
            timeout_seconds=10,
        )
        chosen: list[tuple[SourceKind, str]] = []
        seen_ids: set[str] = set()
        for query_id in response.query_ids:
            if query_id in seen_ids or query_id not in by_id:
                continue
            seen_ids.add(query_id)
            chosen.append(by_id[query_id])
        return WaveSelection(
            queries=tuple(chosen),
            candidate_count=len(candidates),
            model_used=True,
        )
    except Exception:
        # Fail closed for optional enrichment. Mandatory recovery queries remain
        # eligible because they correspond to explicit unresolved coverage gaps.
        fallback = tuple(recovery)
        return WaveSelection(
            queries=fallback,
            candidate_count=len(candidates),
            model_unavailable=True,
        )

