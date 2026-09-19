from app.evidence_quality import assess_evidence_quality
from app.models import EvidenceSource


def _source(
    source_id: str,
    *,
    level: str,
    document_digest: str | None = None,
    evidence_digest: str | None = None,
    url: str | None = None,
) -> EvidenceSource:
    return EvidenceSource(
        id=source_id,
        title=source_id,
        url=url or f"https://example.test/{source_id}",
        accessed_at="2026-09-19T00:00:00+00:00",
        evidence_quote=f"Evidence for {source_id}",
        evidence_level=level,
        document_url=url or f"https://example.test/{source_id}",
        document_digest=document_digest,
        evidence_locator="body/p[1]" if evidence_digest else None,
        evidence_digest=evidence_digest,
    )


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def test_many_weak_documents_do_not_inflate_authority_to_high_quality() -> None:
    sources = [
        _source(
            f"W{i}",
            level="weak_signal",
            document_digest=_digest(format(i % 16, "x")),
            evidence_digest=_digest(format((i + 1) % 16, "x")),
        )
        for i in range(10)
    ]

    quality = assess_evidence_quality(sources)

    assert quality.unique_documents == 10
    assert quality.traceable_documents == 10
    assert quality.weak_documents == 10
    assert quality.score == 0.35


def test_quality_weights_confirmed_and_corroborated_documents() -> None:
    sources = [
        _source("S1", level="confirmed_fact", document_digest=_digest("a"), evidence_digest=_digest("b")),
        _source("R1", level="corroborated_signal", document_digest=_digest("c"), evidence_digest=_digest("d")),
    ]

    quality = assess_evidence_quality(sources)

    assert quality.confirmed_documents == 1
    assert quality.corroborated_documents == 1
    assert quality.score == 0.9


def test_same_document_is_counted_once_using_strongest_traceable_record() -> None:
    shared_url = "https://example.test/company"
    sources = [
        _source(
            "S1",
            level="weak_signal",
            document_digest=_digest("a"),
            evidence_digest=_digest("b"),
            url=shared_url,
        ),
        _source(
            "S1-b1-0",
            level="confirmed_fact",
            document_digest=_digest("a"),
            evidence_digest=_digest("c"),
            url=shared_url,
        ),
    ]

    quality = assess_evidence_quality(sources)

    assert quality.unique_documents == 1
    assert quality.confirmed_documents == 1
    assert quality.score == 1.0


def test_untraceable_claim_does_not_gain_authority_from_declared_level() -> None:
    quality = assess_evidence_quality([
        _source("S1", level="confirmed_fact"),
    ])

    assert quality.unique_documents == 1
    assert quality.traceable_documents == 0
    assert quality.score == 0.0


def test_traceable_fetched_document_replaces_synthetic_same_url_projection() -> None:
    shared_url = "https://example.test/"
    synthetic = _source(
        "S1",
        level="confirmed_fact",
        url=shared_url,
    )
    fetched = _source(
        "OFFICIAL",
        level="confirmed_fact",
        document_digest=_digest("e"),
        evidence_digest=_digest("f"),
        url=shared_url,
    )

    quality = assess_evidence_quality([synthetic, fetched])

    assert quality.unique_documents == 1
    assert quality.traceable_documents == 1
    assert quality.confirmed_documents == 1
    assert quality.score == 1.0
