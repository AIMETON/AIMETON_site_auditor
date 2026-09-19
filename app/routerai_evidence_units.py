from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


DEFAULT_EVIDENCE_CHUNK_CHARS = 12000
DEEP_EVIDENCE_CHUNK_CHARS = 24000
DEFAULT_MAX_FAST_PATH_UNITS = 16


class EvidenceCoverageOverflow(RuntimeError):
    """Fast path cannot cover all evidence without exceeding its bounded unit budget."""


@dataclass(frozen=True)
class EvidenceCoverage:
    official_chars_total: int
    official_chunks_total: int
    official_chunks_processed: int
    sources_total: int
    sources_processed: int
    source_chunks_total: int
    source_chunks_processed: int
    extraction_units_total: int
    extraction_units_processed: int
    complete: bool

    def safe_dict(self) -> dict[str, int | bool]:
        return {
            "official_chars_total": self.official_chars_total,
            "official_chunks_total": self.official_chunks_total,
            "official_chunks_processed": self.official_chunks_processed,
            "sources_total": self.sources_total,
            "sources_processed": self.sources_processed,
            "source_chunks_total": self.source_chunks_total,
            "source_chunks_processed": self.source_chunks_processed,
            "extraction_units_total": self.extraction_units_total,
            "extraction_units_processed": self.extraction_units_processed,
            "complete": self.complete,
        }


def chunk_text(text: str, *, chunk_chars: int = DEFAULT_EVIDENCE_CHUNK_CHARS) -> list[str]:
    """Split text without dropping or overlapping characters."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if not text:
        return []
    return [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]


def project_sources(
    sources: list[dict[str, Any]],
    kinds: set[str],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Project only semantically routed sources without truncation.

    Evidence lifecycle expresses trust, not relevance. A verified document/block must
    still match the vertical's query_kind; otherwise registry/review/catalog evidence
    is multiplied into unrelated identity/management/operations prompts.
    """
    selected: list[dict[str, Any]] = []
    for source in sources:
        kind = str(source.get("query_kind") or "unknown")
        if kind not in kinds:
            continue
        selected.append(
            {
                key: source[key]
                for key in keys
                if source.get(key) not in (None, "", [], {})
            }
        )
    return selected


def chunk_sources(
    projected: list[dict[str, Any]],
    *,
    chunk_chars: int = DEFAULT_EVIDENCE_CHUNK_CHARS,
) -> list[str]:
    """Pack whole source records into JSON chunks; never cut a source record."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if not projected:
        return []

    chunks: list[str] = []
    current: list[dict[str, Any]] = []
    for item in projected:
        candidate = current + [item]
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if current and len(encoded) > chunk_chars:
            chunks.append(json.dumps(current, ensure_ascii=False, separators=(",", ":")))
            current = [item]
        else:
            current = candidate
    if current:
        chunks.append(json.dumps(current, ensure_ascii=False, separators=(",", ":")))
    return chunks


def evidence_units(
    official_text: str,
    projected_sources: list[dict[str, Any]],
    *,
    chunk_chars: int = DEFAULT_EVIDENCE_CHUNK_CHARS,
    max_units: int | None = DEFAULT_MAX_FAST_PATH_UNITS,
) -> list[tuple[str, str]]:
    """Return full-coverage units as (official_text_chunk, source_json_chunk).

    Official text and external sources are mapped independently to avoid a cartesian
    explosion. Empty evidence still yields one unit so extractors can return empty DTOs.
    """
    text_chunks = chunk_text(official_text, chunk_chars=chunk_chars)
    source_chunks = chunk_sources(projected_sources, chunk_chars=chunk_chars)
    units = [(chunk, "[]") for chunk in text_chunks] + [("", chunk) for chunk in source_chunks]
    if not units:
        units = [("", "[]")]
    if max_units is not None and len(units) > max_units:
        raise EvidenceCoverageOverflow(
            f"evidence_units={len(units)} exceeds fast_path_max_units={max_units}"
        )
    return units
