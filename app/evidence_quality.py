from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models import EvidenceSource
from app.search_gateway.gateway import canonical_url


_EVIDENCE_LEVEL_WEIGHT = {
    "confirmed_fact": 1.0,
    "corroborated_signal": 0.8,
    "weak_signal": 0.35,
    "unverified_mention": 0.0,
}


@dataclass(frozen=True)
class EvidenceQualityAssessment:
    score: float
    unique_documents: int
    traceable_documents: int
    confirmed_documents: int
    corroborated_documents: int
    weak_documents: int
    unverified_documents: int

    def safe_dict(self) -> dict[str, int | float]:
        return {
            "score": self.score,
            "unique_documents": self.unique_documents,
            "traceable_documents": self.traceable_documents,
            "confirmed_documents": self.confirmed_documents,
            "corroborated_documents": self.corroborated_documents,
            "weak_documents": self.weak_documents,
            "unverified_documents": self.unverified_documents,
        }


def _document_key(source: EvidenceSource) -> str:
    url = canonical_url(str(source.document_url or source.url or ""))
    if url:
        return "url:" + url
    if source.document_digest:
        return "digest:" + str(source.document_digest)
    return "id:" + str(source.id)


def assess_evidence_quality(
    sources: Iterable[EvidenceSource],
) -> EvidenceQualityAssessment:
    """Score evidence authority independently from breadth/coverage.

    One document contributes at most once even if several extracted claims reference
    it. A document only receives its declared evidence-level weight when it retains
    traceable document+quote digests; otherwise it contributes zero authority.
    """
    documents: dict[str, tuple[float, str, bool]] = {}
    for source in sources:
        key = _document_key(source)
        traceable = bool(source.document_digest and source.evidence_digest)
        level = str(source.evidence_level or "unverified_mention")
        weight = _EVIDENCE_LEVEL_WEIGHT.get(level, 0.0) if traceable else 0.0
        previous = documents.get(key)
        if previous is None or weight > previous[0]:
            documents[key] = (weight, level, traceable)

    if not documents:
        return EvidenceQualityAssessment(
            score=0.0,
            unique_documents=0,
            traceable_documents=0,
            confirmed_documents=0,
            corroborated_documents=0,
            weak_documents=0,
            unverified_documents=0,
        )

    levels = [level for _, level, _ in documents.values()]
    score = sum(weight for weight, _, _ in documents.values()) / len(documents)
    return EvidenceQualityAssessment(
        score=round(score, 6),
        unique_documents=len(documents),
        traceable_documents=sum(traceable for _, _, traceable in documents.values()),
        confirmed_documents=sum(level == "confirmed_fact" for level in levels),
        corroborated_documents=sum(level == "corroborated_signal" for level in levels),
        weak_documents=sum(level == "weak_signal" for level in levels),
        unverified_documents=sum(level == "unverified_mention" for level in levels),
    )
