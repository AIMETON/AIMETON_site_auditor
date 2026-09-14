from __future__ import annotations

from typing import Any


DISCOVERY_SNIPPET_CHARS = 420

_BASE_KEYS = (
    "id",
    "title",
    "url",
    "accessed_at",
    "query_kind",
    "result_kind",
    "source_class",
    "classification_state",
    "lifecycle_state",
    "source_type",
    "evidence_level",
)
_EVIDENCE_KEYS = (
    "document_url",
    "document_title",
    "document_accessed_at",
    "document_digest",
    "evidence_quote",
    "evidence_locator",
    "evidence_digest",
    "fetch_path",
)


def compact_routerai_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact LLM-only source projection without dropping source identities.

    Discovery hints are deliberately bounded because they are search snippets,
    not verified evidence. Verified evidence keeps its supplied evidence quote
    and document metadata. Empty service fields and the repeated verification
    note are omitted from the LLM payload, while the authoritative source model
    remains unchanged elsewhere in the application.
    """
    compact: list[dict[str, Any]] = []
    for source in sources:
        item: dict[str, Any] = {}
        for key in _BASE_KEYS:
            value = source.get(key)
            if value not in (None, "", [], {}):
                item[key] = value

        lifecycle = source.get("lifecycle_state")
        if lifecycle == "evidence":
            quote = source.get("evidence_quote") or source.get("snippet")
            if quote not in (None, ""):
                item["snippet"] = str(quote)
            for key in _EVIDENCE_KEYS:
                value = source.get(key)
                if value not in (None, "", [], {}):
                    item[key] = value
        else:
            snippet = source.get("snippet")
            if snippet not in (None, ""):
                item["snippet"] = str(snippet)[:DISCOVERY_SNIPPET_CHARS]

        compact.append(item)
    return compact
