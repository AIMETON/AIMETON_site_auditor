from datetime import UTC, datetime

from app.evidence_freshness import assess_source_freshness, parse_source_datetime, summarize_source_freshness
from app.models import EvidenceSource


def _source(identifier: str, freshness: str) -> EvidenceSource:
    return EvidenceSource(
        id=identifier,
        title=identifier,
        url=f"https://example.test/{identifier}",
        accessed_at="2026-09-19T00:00:00Z",
        evidence_quote="evidence",
        freshness=freshness,
    )


def test_explicit_publication_date_is_classified_without_using_fetch_time():
    as_of = datetime(2026, 9, 19, tzinfo=UTC)

    assert assess_source_freshness("2026-09-01T00:00:00Z", as_of=as_of) == "current"
    assert assess_source_freshness("2024-09-01T00:00:00Z", as_of=as_of) == "stale"
    assert assess_source_freshness("2026-10-01T00:00:00Z", as_of=as_of) == "not_yet_valid"
    assert assess_source_freshness(None, as_of=as_of) == "unassessed"


def test_unparseable_or_missing_date_fails_closed_to_unassessed():
    assert parse_source_datetime("not-a-date") is None
    assert assess_source_freshness("not-a-date") == "unassessed"


def test_rfc_date_is_supported_and_summary_keeps_unassessed_visible():
    parsed = parse_source_datetime("Wed, 02 Sep 2026 10:00:00 GMT")
    assert parsed == datetime(2026, 9, 2, 10, 0, tzinfo=UTC)

    summary = summarize_source_freshness([
        _source("C", "current"),
        _source("S", "stale"),
        _source("U", "unassessed"),
    ])
    assert summary == {
        "current": 1,
        "stale": 1,
        "not_yet_valid": 0,
        "unassessed": 1,
    }
