from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.html_text_coverage import uncovered_text_runs

from app.document_pipeline.models import (
    BlockKind,
    ContentRegion,
    ExtractedBlock,
    ExtractedLink,
    ExtractedTable,
)


SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return SPACE_RE.sub(" ", value).strip()


def digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _text_blocks(*, locator: str, kind: BlockKind, text: str,
                 region: ContentRegion = ContentRegion.BODY) -> list[ExtractedBlock]:
    """Fit the block contract without dropping the end of a large DOM element."""
    if len(text) <= 20_000:
        return [ExtractedBlock(locator=locator, kind=kind, text=text, region=region)]
    return [
        ExtractedBlock(locator=f"{locator}/part[{offset // 20_000 + 1}]",
                       kind=kind, text=text[offset:offset + 20_000], region=region)
        for offset in range(0, len(text), 20_000)
    ]


@dataclass(frozen=True)
class Extraction:
    title: str
    text: str
    blocks: list[ExtractedBlock]
    links: list[ExtractedLink]
    tables: list[ExtractedTable]
    declared_canonical_url: str | None


def _region_for(element) -> ContentRegion:
    if element.find_parent("header") is not None or element.name == "header":
        return ContentRegion.HEADER
    if element.find_parent("footer") is not None or element.name == "footer":
        return ContentRegion.FOOTER
    return ContentRegion.BODY


def _region_prefix(region: ContentRegion) -> str:
    return region.value


def extract_html(html: str, *, base_url: str) -> Extraction:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()

    blocks: list[ExtractedBlock] = []
    title = normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if title:
        blocks.extend(_text_blocks(locator="head/title", kind=BlockKind.TITLE, text=title))

    counters: dict[tuple[ContentRegion, str], int] = {}
    root = soup.body or soup
    for element in root.select("h1, h2, h3, h4, p, li, dt, dd"):
        text = normalize_text(element.get_text(" ", strip=True))
        if not text:
            continue
        tag = element.name.lower()
        region = _region_for(element)
        counter_key = (region, tag)
        counters[counter_key] = counters.get(counter_key, 0) + 1
        kind = (
            BlockKind.HEADING
            if tag.startswith("h")
            else BlockKind.LIST_ITEM
            if tag == "li"
            else BlockKind.PARAGRAPH
        )
        blocks.extend(
            _text_blocks(
                locator=(
                    f"{_region_prefix(region)}/{tag}[{counters[counter_key]}]"
                ),
                kind=kind,
                region=region,
                text=text,
            )
        )

    tables: list[ExtractedTable] = []
    table_counters: dict[ContentRegion, int] = {}
    for table in root.select("table"):
        region = _region_for(table)
        table_counters[region] = table_counters.get(region, 0) + 1
        table_index = table_counters[region]
        prefix = _region_prefix(region)
        rows: list[list[str]] = []
        for row_index, row in enumerate(table.select("tr"), start=1):
            cells: list[str] = []
            for cell_index, cell in enumerate(row.select("th, td"), start=1):
                value = normalize_text(cell.get_text(" ", strip=True))
                cells.append(value)
                if value:
                    blocks.extend(
                        _text_blocks(
                            locator=(
                                f"{prefix}/table[{table_index}]/row[{row_index}]"
                                f"/cell[{cell_index}]"
                            ),
                            kind=BlockKind.TABLE_CELL,
                            region=region,
                            text=value,
                        )
                    )
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(
                ExtractedTable(
                    locator=f"{prefix}/table[{table_index}]",
                    region=region,
                    rows=rows,
                )
            )

    uncovered_counters: dict[ContentRegion, int] = {}
    for element, text in uncovered_text_runs(
        root, frozenset({"head", "title", "h1", "h2", "h3", "h4", "p", "li", "dt", "dd", "th", "td"}),
    ):
        region = _region_for(element)
        uncovered_counters[region] = uncovered_counters.get(region, 0) + 1
        blocks.extend(_text_blocks(
            locator=f"{region.value}/text[{uncovered_counters[region]}]",
            kind=BlockKind.PARAGRAPH, region=region, text=text,
        ))

    links: list[ExtractedLink] = []
    link_counters: dict[ContentRegion, int] = {}
    for anchor in root.select("a[href]"):
        absolute = urljoin(base_url, str(anchor.get("href") or "").strip())
        if not absolute.startswith(("http://", "https://")):
            continue
        region = _region_for(anchor)
        link_counters[region] = link_counters.get(region, 0) + 1
        links.append(
            ExtractedLink(
                locator=(
                    f"{_region_prefix(region)}/a[{link_counters[region]}]"
                ),
                region=region,
                text=normalize_text(anchor.get_text(" ", strip=True)),
                url=absolute,
            )
        )

    unique_blocks: list[ExtractedBlock] = []
    seen: set[tuple[ContentRegion, BlockKind, str]] = set()
    for block in blocks:
        # Equal chunks at different offsets still belong to the original corpus.
        if "/part[" in block.locator:
            unique_blocks.append(block)
            continue
        key = (block.region, block.kind, block.text)
        if key in seen:
            continue
        seen.add(key)
        unique_blocks.append(block)

    text = "\n".join(block.text for block in unique_blocks)
    canonical = soup.select_one("link[rel~='canonical'][href]")
    declared_canonical_url = None
    if canonical is not None:
        candidate = urljoin(base_url, str(canonical.get("href") or "").strip())
        if candidate.startswith(("http://", "https://")):
            declared_canonical_url = candidate

    return Extraction(
        title=title or (unique_blocks[0].text[:1_000] if unique_blocks else "Документ"),
        text=text,
        blocks=unique_blocks,
        links=links,
        tables=tables,
        declared_canonical_url=declared_canonical_url,
    )
