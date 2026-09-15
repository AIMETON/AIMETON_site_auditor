"""Recover DOM text not already represented by semantic extraction blocks.

Callers remove non-content elements first. This is static DOM extraction, not a
claim of browser visibility or verified company identity.
"""
from __future__ import annotations

from collections.abc import Iterator

from bs4 import NavigableString, Tag


_CONTAINERS = frozenset({
    "html", "body", "div", "section", "article", "main", "aside", "nav",
    "header", "footer", "address", "blockquote", "pre", "figure",
    "figcaption", "table", "tr", "td", "th", "dl", "dt", "dd",
})


def uncovered_text_runs(root: Tag, covered_tags: frozenset[str]) -> Iterator[tuple[Tag, str]]:
    """Yield contiguous uncovered text, joining inline labels and values once.

    Skip covered subtrees rather than re-extracting an entire parent div. The
    explicit stack also handles deeply nested CMS markup without recursion.
    Comments and other NavigableString subclasses are not document text.
    """
    stack = [(root, root)]
    parts: list[str] = []
    owner = root
    while stack:
        node, container = stack.pop()
        boundary = node is None or (
            isinstance(node, Tag) and (node.name in covered_tags or node.name in _CONTAINERS)
        )
        if boundary and parts:
            text = " ".join(" ".join(parts).split())
            if text:
                yield owner, text
            parts = []
        if node is None:
            continue
        if type(node) is NavigableString:
            owner = container
            parts.append(str(node))
        elif isinstance(node, Tag) and node.name not in covered_tags:
            next_owner = node if node.name in _CONTAINERS else container
            if node.name in _CONTAINERS:
                stack.append((None, container))
            stack.extend((child, next_owner) for child in reversed(node.contents))
    if parts:
        text = " ".join(" ".join(parts).split())
        if text:
            yield owner, text
