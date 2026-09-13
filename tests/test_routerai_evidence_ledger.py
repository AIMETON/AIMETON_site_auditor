from __future__ import annotations

import asyncio
import json

import pytest

import app.routerai_split_v2 as split_v2
from app.mission_contract import MissionCreate
from app.mission_sqlite import SQLiteMissionRepository
from app.models import CompanyFact, EconomicSignal
from app.routerai_evidence_ledger import (
    evidence_ledger_digest,
    evidence_ledger_payload,
    persist_merged_evidence_ledger,
)
from app.routerai_evidence_units import EvidenceCoverage
from app.routerai_profile_extraction import MergedProfileExtraction
from app.trace_context import bind_trace_identity


def _merged() -> MergedProfileExtraction:
    return MergedProfileExtraction(
        company_name="Large Co",
        business_summary="Structured dossier",
        evidence=["first evidence", "tail evidence"],
        company_facts=[
            CompanyFact(field="revenue", value=str(index), period=str(2000 + index), source_ids=["S1"])
            for index in range(40)
        ]
        + [CompanyFact(field="other", value="TAIL_FACT", source_ids=["E99"])],
        economic_signals=[
            EconomicSignal(
                signal=f"signal-{index}",
                evidence=f"evidence-{index}",
                business_effect=f"effect-{index}",
                source_ids=["S1"],
            )
            for index in range(20)
        ],
        risks_and_assumptions=["risk-a", "risk-z"],
        coverage=EvidenceCoverage(
            official_chars_total=50000,
            official_chunks_total=5,
            official_chunks_processed=5,
            sources_total=99,
            sources_processed=99,
            source_chunks_total=7,
            source_chunks_processed=7,
            extraction_units_total=32,
            extraction_units_processed=32,
            complete=True,
        ),
    )


def test_evidence_ledger_persists_full_tail_digest_and_is_idempotent(tmp_path, monkeypatch) -> None:
    path = tmp_path / "missions.sqlite3"
    monkeypatch.setenv("AIMETON_MISSION_DB", str(path))
    repo = SQLiteMissionRepository(path)
    mission = repo.create(
        42,
        MissionCreate(
            title="Large company audit",
            target_ref="https://example.test",
            input_snapshot={"source": "test"},
            correlation_id="corr-ledger",
        ),
    )
    merged = _merged()

    with bind_trace_identity(mission.id, "attempt-001"):
        first = persist_merged_evidence_ledger(merged)
        second = persist_merged_evidence_ledger(merged)

    assert first == second == "evidence_ledger_attempt-001"
    records = repo.records_for_owner(42, mission.id)
    assert records is not None
    ledger_records = [record for record in records if record["kind"] == "evidence_ledger"]
    assert len(ledger_records) == 1
    record = ledger_records[0]
    payload = record["payload"]
    assert payload["schema_version"] == 1
    assert payload["company_facts"][-1]["value"] == "TAIL_FACT"
    assert len(payload["company_facts"]) == 41
    assert len(payload["economic_signals"]) == 20
    assert payload["coverage"]["complete"] is True
    assert payload["coverage"]["official_chars_total"] == 50000
    assert record["digest"] == evidence_ledger_digest(payload)

    rendered = json.dumps(payload, ensure_ascii=False)
    assert "prompt" not in rendered.lower()
    assert "authorization" not in rendered.lower()
    assert "api_key" not in rendered.lower()
    assert "cookie" not in rendered.lower()


def test_evidence_ledger_payload_is_structured_domain_data_only() -> None:
    payload = evidence_ledger_payload(_merged())
    assert set(payload) == {
        "schema_version",
        "company_name",
        "business_summary",
        "evidence",
        "company_facts",
        "economic_signals",
        "risks_and_assumptions",
        "coverage",
    }
    assert payload["company_facts"][-1]["source_ids"] == ["E99"]


def test_direct_non_mission_call_skips_persistence(tmp_path) -> None:
    repo = SQLiteMissionRepository(tmp_path / "missions.sqlite3")
    assert persist_merged_evidence_ledger(_merged(), repository=repo) is None
    assert repo.list_for_admin() == []


def test_split_v2_persists_merged_ledger_before_any_reasoning(monkeypatch) -> None:
    events: list[str] = []

    async def fake_extract(**kwargs):
        events.append("extract")
        return _merged()

    def fake_persist(merged):
        assert merged.company_facts[-1].value == "TAIL_FACT"
        events.append("persist")
        return "evidence_ledger_attempt-test"

    async def fail_reasoning(*args, **kwargs):
        assert events[:2] == ["extract", "persist"]
        events.append("reasoning")
        raise RuntimeError("intentional_reasoning_failure")

    monkeypatch.setattr(split_v2, "extract_profile_parallel", fake_extract)
    monkeypatch.setattr(split_v2, "persist_merged_evidence_ledger", fake_persist)
    monkeypatch.setattr(split_v2, "_request_json", fail_reasoning)
    monkeypatch.setattr(split_v2, "request_json_strict", fail_reasoning)

    result = asyncio.run(split_v2.analyze_with_routerai_split_v2(
        "https://example.test", "Example", "Official text", [],
    ))
    assert result.company_facts[-1].value == "TAIL_FACT"
    assert result.readiness.provider_states["routerai"] == "reasoning_failed_extraction_preserved"

    assert events[0:2] == ["extract", "persist"]
    assert "reasoning" in events
