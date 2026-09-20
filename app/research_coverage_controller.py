from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.analysis_mode import compiled_two_call_enabled, focused_multipass_enabled, minimal_llm_routing_enabled
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
MAX_PROGRESSIVE_WAVES = 3
MAX_OPTIONAL_QUERIES_PER_WAVE = 6

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
    query_ids: list[str] = Field(default_factory=list, max_length=MAX_OPTIONAL_QUERIES_PER_WAVE)
    reason: str = Field(default="", max_length=500)


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


async def optional_wave(
    full_plan: Iterable[tuple[SourceKind, str]],
    coverage: CoverageSnapshot,
    *,
    company_name: str,
    anchors: IdentityAnchors,
    already_attempted: set[str],
    request_json=None,
) -> WaveSelection:
    """Fast-model prioritization for the final bounded recovery/enrichment wave.

    Deterministic coverage defines the candidate set. The cheap model may only select
    candidate IDs; it cannot invent search queries, provider policies or evidence.
    Failure is fail-open to a bounded deterministic priority order.
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

    if len(candidates) <= MAX_OPTIONAL_QUERIES_PER_WAVE:
        return WaveSelection(
            queries=tuple(candidates),
            candidate_count=len(candidates),
        )

    if (
        deep_research_enabled()
        and (focused_multipass_enabled() or compiled_two_call_enabled())
        and minimal_llm_routing_enabled()
    ):
        return WaveSelection(
            queries=tuple(candidates[:MAX_OPTIONAL_QUERIES_PER_WAVE]),
            candidate_count=len(candidates),
            model_used=False,
            model_unavailable=False,
        )

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
                "Ты быстрый Research Coverage Router. Выбирай только query_ids из "
                "переданного списка. Не извлекай факты, не меняй provider policy и "
                "не объявляй evidence подтверждённым. Приоритет: recovery для "
                "mandatory verticals без evidence, затем недостающие бизнес-сигналы."
            ),
            prompt=(
                "COVERAGE:\n"
                + str(coverage.safe_dict())
                + "\nCANDIDATE QUERIES:\n"
                + str(rows)
                + f"\nВыбери не более {MAX_OPTIONAL_QUERIES_PER_WAVE} query_ids."
            ),
            max_tokens=900,
            timeout_seconds=10,
        )
        chosen: list[tuple[SourceKind, str]] = []
        seen_ids: set[str] = set()
        for query_id in response.query_ids:
            if query_id in seen_ids or query_id not in by_id:
                continue
            seen_ids.add(query_id)
            chosen.append(by_id[query_id])
        if chosen:
            return WaveSelection(
                queries=tuple(chosen[:MAX_OPTIONAL_QUERIES_PER_WAVE]),
                candidate_count=len(candidates),
                model_used=True,
            )
    except Exception:
        return WaveSelection(
            queries=tuple(candidates[:MAX_OPTIONAL_QUERIES_PER_WAVE]),
            candidate_count=len(candidates),
            model_unavailable=True,
        )

    return WaveSelection(
        queries=tuple(candidates[:MAX_OPTIONAL_QUERIES_PER_WAVE]),
        candidate_count=len(candidates),
        model_used=True,
    )
