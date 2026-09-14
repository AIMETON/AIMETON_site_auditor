from types import SimpleNamespace as NS

import pytest

from app.document_preflight import screen_document, preview_document, RelevanceVote


def document(text, headings=()):
    return NS(normalized_text=text, document=NS(title="Document"),
              blocks=[NS(kind="heading", text=h) for h in headings])


@pytest.mark.asyncio
async def test_small_documents_skip_classifier():
    async def fail(*args, **kwargs):
        raise AssertionError("unnecessary LLM call")
    result = await screen_document(document("short"), company_name="Company", anchors=NS(), request_json=fail)
    assert result.decision == "include" and result.passes == 0


@pytest.mark.asyncio
async def test_irrelevance_requires_two_high_confidence_votes():
    calls = []
    async def request(*args, **kwargs):
        calls.append(kwargs)
        return RelevanceVote(decision="exclude", confidence=.99, reason="Unrelated manual")
    result = await screen_document(document("x" * 60000), company_name="Company", anchors=NS(), request_json=request)
    assert result.decision == "exclude" and len(calls) == 2
    assert result.sampled_chars < result.document_chars


@pytest.mark.asyncio
async def test_late_relevant_content_rescues_uninformative_beginning():
    async def request(*args, **kwargs):
        found = "TAIL_PRODUCT" in kwargs["prompt"]
        return RelevanceVote(decision="include" if found else "exclude", confidence=.99, reason="Product" if found else "Noise")
    result = await screen_document(document("x" * 60000 + "TAIL_PRODUCT"), company_name="Company", anchors=NS(), request_json=request)
    assert result.decision == "include" and result.passes == 2


@pytest.mark.asyncio
async def test_low_confidence_or_failure_never_excludes():
    async def uncertain(*args, **kwargs):
        return RelevanceVote(decision="exclude", confidence=.7, reason="Unclear")
    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline")
    for request in (uncertain, unavailable):
        result = await screen_document(document("x" * 60000), company_name="Company", anchors=NS(), request_json=request)
        assert result.decision == "uncertain"


def test_preview_uses_late_headings_and_company_mentions():
    text = "x" * 44000 + "Company important fact" + "y" * 16000
    preview = preview_document(document(text, [f"Heading {i}" for i in range(100)]), "Company", NS(), confirm=True)
    assert "Heading 99" in preview["headings"]
    assert any("important fact" in value for value in preview["company_mentions"])

@pytest.mark.asyncio
async def test_excluded_document_never_reaches_evidence_pool(monkeypatch):
    from app import external_verification as verify
    from app.document_preflight import PreflightResult
    from app.external_sources import IdentityAnchors
    from app.models import IntelligenceSource

    class Pipeline:
        async def fetch_hint(self, *args):
            return NS(normalized_text="x" * 60000,
                      document=NS(url="https://company.test/manual"), links=[])
        def promote_quote(self, *args, **kwargs):
            raise AssertionError("excluded content must not be promoted")
    async def screen(*args, **kwargs):
        return PreflightResult(decision="exclude", reason="Unrelated reference", document_chars=60000, passes=2)
    monkeypatch.setattr(verify, "get_document_pipeline", lambda: Pipeline())
    monkeypatch.setattr(verify, "screen_document", screen)
    sources = [IntelligenceSource(id="M", title="Manual", url="https://company.test/manual", accessed_at="2026-09-13T00:00:00Z")]
    result = await verify.verify_external_sources(sources, company_name="Company",
        anchors=IdentityAnchors(domain="company.test"), include_official=True, preserve_blocks=True)
    assert result == []
    assert sources[0].preflight_decision == "exclude"
    assert sources[0].lifecycle_state == "source_candidate"
    assert "Unrelated reference" in sources[0].verification_note
