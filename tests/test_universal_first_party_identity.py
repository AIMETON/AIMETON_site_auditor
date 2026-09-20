from types import SimpleNamespace as NS

from app import external_verification as verification
from app import verified_analysis as audit
from app.models import IntelligenceSource


def _official(source_id: str, quote: str, *, note: str = "") -> IntelligenceSource:
    return IntelligenceSource(
        id=source_id,
        title="Example Dental legal information",
        url="https://example.org/legal/",
        accessed_at="2026-09-20T00:00:00Z",
        source_class="official",
        query_kind="official",
        classification_state="classified",
        lifecycle_state="evidence",
        evidence_level="confirmed_fact",
        document_url="https://example.org/legal/",
        document_title="Legal information — Example Dental",
        document_accessed_at="2026-09-20T00:00:00Z",
        document_digest="sha256:" + "a" * 64,
        evidence_quote=quote,
        evidence_locator="body/main",
        evidence_digest="sha256:" + "b" * 64,
        fetch_path="static",
        verification_note=note,
    )


def test_identity_block_selection_is_url_agnostic_and_requires_local_target_context():
    blocks = [
        NS(text='ООО "ПРИМЕР ДЕНТ"', locator="main/section[2]/div[1]"),
        NS(text="ИНН:", locator="main/section[2]/div[2]"),
        NS(text="1234567894", locator="main/section[2]/div[3]"),
        NS(text="ОГРН:", locator="main/section[2]/div[4]"),
        NS(text="1234567890127", locator="main/section[2]/div[5]"),
        NS(text='ООО "ПАРТНЕР СЕРВИС" ИНН 7707083893', locator="main/section[5]/p[1]"),
        NS(text="ИНН 9999999999", locator="footer/legal"),
    ]

    selected = verification._official_identity_block_indices(
        blocks,
        company_name="Пример Дент",
        anchors=NS(legal_name='ООО "ПРИМЕР ДЕНТ"'),
        document_title="Юридическая информация — Пример Дент",
    )

    assert selected == [1, 2, 3, 4]


def test_identity_block_selection_supports_single_block_without_site_specific_path():
    blocks = [
        NS(
            text='ООО "ПРИМЕР ДЕНТ" — ИНН 1234567894, ОГРН 1234567890127',
            locator="main/article/p[7]",
        )
    ]

    selected = verification._official_identity_block_indices(
        blocks,
        company_name="Пример Дент",
        anchors=NS(legal_name=None),
        document_title="О компании Пример Дент",
    )

    assert selected == [0]


def test_deterministic_identity_projection_ignores_non_target_first_party_entities():
    records = [
        _official("DOC", "Юридическая информация Example Dental"),
        _official(
            "DOC-b1-0",
            'ООО "ПРИМЕР ДЕНТ"',
            note="Evidence triage: target/registry; local target name.",
        ),
        _official(
            "DOC-b2-0",
            "ИНН:",
            note="Evidence triage: target/registry; labelled identifier.",
        ),
        _official(
            "DOC-b3-0",
            "1234567894",
            note="Evidence triage: target/registry; labelled identifier value.",
        ),
        _official(
            "DOC-b4-0",
            "ОГРН:",
            note="Evidence triage: target/registry; labelled identifier.",
        ),
        _official(
            "DOC-b5-0",
            "1234567890127",
            note="Evidence triage: target/registry; labelled identifier value.",
        ),
        _official(
            "DOC-b9-0",
            'ООО "ПАРТНЕР СЕРВИС" ИНН 7707083893',
            note="Evidence triage: counterparty/registry; payment processor.",
        ),
    ]

    ordered = audit._ordered_official_evidence(records)
    assert len(ordered) == 1
    assert "7707083893" not in ordered[0][1]

    facts = audit._deterministic_official_identity_facts(records, "https://example.org/")
    assert {(fact.field, fact.value) for fact in facts} == {
        ("inn", "1234567894"),
        ("ogrn", "1234567890127"),
    }
    assert all(fact.source_ids == ["DOC"] for fact in facts)



def test_all_first_party_identifier_candidates_keep_competing_entities_for_dadata():
    records = [
        _official(
            "DOC-b1-0",
            'ООО "ПРИМЕР ДЕНТ"',
            note="Evidence triage: target/registry; local target name.",
        ),
        _official(
            "DOC-b2-0",
            "ИНН:",
            note="Evidence triage: target/registry; labelled identifier.",
        ),
        _official(
            "DOC-b3-0",
            "1234567894",
            note="Evidence triage: target/registry; labelled identifier value.",
        ),
        _official(
            "DOC-b9-0",
            'ООО "ПАРТНЕР СЕРВИС" ИНН 7707083893',
            note="Evidence triage: counterparty/registry; payment processor.",
        ),
    ]

    candidates = audit._all_first_party_identifier_candidates(records)

    assert ("inn", "1234567894", True) in candidates
    assert ("inn", "7707083893", False) in candidates


def test_identifier_candidates_do_not_restore_target_scope_from_parent_quote():
    records = [
        _official(
            "DOC",
            'ООО "ПРИМЕР ДЕНТ" ИНН 1234567894; платежный агент ООО "ПАРТНЕР" ИНН 7707083893',
        ),
        _official(
            "DOC-b1-0",
            'ООО "ПРИМЕР ДЕНТ" ИНН 1234567894',
            note="Evidence triage: target/registry; local target identity.",
        ),
        _official(
            "DOC-b2-0",
            'ООО "ПАРТНЕР" ИНН 7707083893',
            note="Evidence triage: counterparty/registry; payment processor.",
        ),
    ]

    candidates = audit._all_first_party_identifier_candidates(records)

    assert ("inn", "1234567894", True) in candidates
    assert ("inn", "7707083893", False) in candidates
    assert ("inn", "7707083893", True) not in candidates
