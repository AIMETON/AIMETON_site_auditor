from datetime import datetime, timezone

import pytest

from app.document_pipeline.extractor import extract_html
from app.document_pipeline.models import ContentRegion
from app.document_pipeline.pipeline import DocumentPipeline
from app.scraper import extract_visible_text


HTML = """<html><head><title>Компания</title></head><body>
<header><div>Телефон <span>+7 999 123-45-67</span></div></header>
<main><div>Сотрудников: <span>127</span><p>Производим оборудование.</p>
<span>Филиалов: 8</span></div>
<table><tr><th>Выручка</th><td>12 млн рублей</td></tr></table>
<h5>Сертификат АБ-42</h5></main>
<footer><address>Красноярск, улица Мира, 10</address>
<div><span>ИНН</span> <strong>2460000000</strong></div>
<a href="/requisites">Реквизиты компании</a></footer>
<!-- COMMENT_SHOULD_NOT_BECOME_FACT -->
<script>EXECUTABLE_SHOULD_NOT_BECOME_FACT</script>
<template>TEMPLATE_SHOULD_NOT_BECOME_FACT</template>
</body></html>"""


def test_surface_facts_survive_both_extraction_paths_without_parent_duplicates():
    document = extract_html(HTML, base_url="https://example.com/")
    _, legacy = extract_visible_text(HTML)
    for text in (document.text, legacy):
        for fact in (
            "Телефон +7 999 123-45-67", "Сотрудников: 127", "Филиалов: 8",
            "Производим оборудование.", "12 млн рублей", "Сертификат АБ-42",
            "Красноярск, улица Мира, 10", "ИНН 2460000000", "Реквизиты компании",
        ):
            assert text.count(fact) == 1, (fact, text)
        assert "SHOULD_NOT_BECOME_FACT" not in text
    assert next(b for b in document.blocks if "ИНН 246" in b.text).region == ContentRegion.FOOTER
    assert next(b for b in document.blocks if "Телефон" in b.text).region == ContentRegion.HEADER
    assert str(document.links[0].url) == "https://example.com/requisites"


@pytest.mark.parametrize("tag", ["div", "p", "td"])
def test_long_elements_keep_late_facts_with_unique_stable_locators(tag):
    content = "Начало " + "оборудование " * 4000 + "КОНЕЧНЫЙ_ФАКТ_927"
    wrapped = f"<{tag}>{content}</{tag}>"
    if tag == "td":
        wrapped = f"<table><tr>{wrapped}</tr></table>"
    html = f"<html><body>{wrapped}</body></html>"
    result = extract_html(html, base_url="https://example.com/")
    repeated = extract_html(html, base_url="https://example.com/")
    assert "КОНЕЧНЫЙ_ФАКТ_927" in result.text
    assert "КОНЕЧНЫЙ_ФАКТ_927" in extract_visible_text(html)[1]
    assert all(len(block.text) <= 20_000 for block in result.blocks)
    assert len({block.locator for block in result.blocks}) == len(result.blocks)
    assert result.blocks == repeated.blocks
    # No prefix cap: reconstructing chunks reproduces every original character.
    assert "".join(b.text for b in result.blocks) == content


def test_uncovered_text_does_not_duplicate_nested_semantic_subtrees():
    html = "<body><div>До<p>Факт <span>внутри</span></p>После</div></body>"
    result = extract_html(html, base_url="https://example.com/")
    assert result.text.count("Факт внутри") == 1
    assert "До" in result.text and "После" in result.text
    assert next(b for b in result.blocks if b.text == "Факт внутри").locator == "body/p[1]"


def test_equal_chunks_at_different_offsets_are_not_discarded():
    content = "А" * 60_000 + "Конец"
    result = extract_html(f"<body><p>{content}</p></body>", base_url="https://example.com/")
    assert "".join(b.text for b in result.blocks) == content


def test_recovered_quote_promotes_only_at_its_real_locator():
    from app.document_pipeline.models import FetchedDocument, DocumentDiagnostics
    from app.sef.models import Document

    extracted = extract_html(HTML, base_url="https://example.com/")
    from app.document_pipeline.extractor import digest_text
    digest = digest_text(extracted.text)
    fetched = FetchedDocument(
        document=Document(id="doc_surface", mission_id="mission_surface", source_id="source_surface",
                          correlation_id="corr_surface", url="https://example.com/", title="Компания",
                          accessed_at=datetime.now(timezone.utc), fetch_status="fetched", content_digest=digest,
                          media_type="text/html"),
        raw_content_digest=digest, normalized_content_digest=digest, normalized_text=extracted.text,
        blocks=extracted.blocks,
        diagnostics=DocumentDiagnostics(request_fingerprint=digest, path="static", raw_bytes=len(HTML.encode()), latency_ms=0),
    )
    block = next(b for b in fetched.blocks if "ИНН 246" in b.text)
    promoted = DocumentPipeline.promote_quote(fetched, locator=block.locator, quote="ИНН 2460000000")
    assert promoted.evidence.locator == block.locator
    with pytest.raises(ValueError, match="not present"):
        DocumentPipeline.promote_quote(fetched, locator="head/title", quote="ИНН 2460000000")
