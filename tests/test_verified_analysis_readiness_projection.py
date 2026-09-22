from app.models import PreliminaryResultReadiness, PreliminaryVerticalStatus
from app.research_coverage_controller import CoverageSnapshot
from app.verified_analysis import _reconcile_search_coverage_readiness


def _coverage(states: dict[str, str]) -> CoverageSnapshot:
    return CoverageSnapshot(
        states=states,
        searched_kinds=frozenset(),
        evidence_kinds=frozenset(),
        evidence_documents_by_kind={},
        qualifying_documents_by_vertical={code: 0 for code in states},
    )


def test_search_coverage_replaces_false_not_searched_states() -> None:
    readiness = PreliminaryResultReadiness(
        required_verticals=[
            PreliminaryVerticalStatus(code="identity", state="verified"),
            PreliminaryVerticalStatus(code="workforce", state="not_searched"),
            PreliminaryVerticalStatus(code="legal_events", state="not_searched"),
        ]
    )

    _reconcile_search_coverage_readiness(
        readiness,
        _coverage({
            "identity": "covered",
            "workforce": "covered",
            "legal_events": "searched_no_evidence",
        }),
    )

    states = {item.code: item.state for item in readiness.required_verticals}
    assert states["identity"] == "verified"
    assert states["workforce"] == "partially_verified"
    assert states["legal_events"] == "not_found_after_sufficient_search"


def test_search_coverage_does_not_overwrite_stronger_or_degraded_states() -> None:
    readiness = PreliminaryResultReadiness(
        required_verticals=[
            PreliminaryVerticalStatus(code="contacts", state="partially_verified"),
            PreliminaryVerticalStatus(code="financials", state="degraded"),
            PreliminaryVerticalStatus(code="ownership", state="not_searched"),
        ]
    )

    _reconcile_search_coverage_readiness(
        readiness,
        _coverage({
            "contacts": "covered",
            "financials": "covered",
            "ownership": "missing",
        }),
    )

    states = {item.code: item.state for item in readiness.required_verticals}
    assert states["contacts"] == "partially_verified"
    assert states["financials"] == "degraded"
    assert states["ownership"] == "not_searched"
