from __future__ import annotations

import asyncio

import app.routerai_split_v2 as split_v2
from app.models import (
    BusinessMachineCell,
    CompanyFact,
    EconomicSignal,
)
from app.routerai_evidence_units import EvidenceCoverage
from app.routerai_profile_extraction import MergedProfileExtraction
from app.routerai_split_synthesis import BusinessMachineSynthesis, CommercialSynthesis, SplitSynthesisPhaseError


def _coverage() -> EvidenceCoverage:
    return EvidenceCoverage(
        official_chars_total=13,
        official_chunks_total=1,
        official_chunks_processed=1,
        sources_total=0,
        sources_processed=0,
        source_chunks_total=0,
        source_chunks_processed=0,
        extraction_units_total=5,
        extraction_units_processed=5,
        complete=True,
    )


def _km_cell(code: str) -> BusinessMachineCell:
    operators = {
        "I": "I — Коммуникационные системы",
        "II": "II — Люди",
        "III": "III — Технологии",
        "IV": "IV — Менеджмент",
    }
    vertices = {
        "I-I": "Взаимодействие",
        "I-II": "Влияние",
        "I-III": "Зависимость",
        "I-IV": "Противодействие",
        "II-I": "Учредители и собственники",
        "II-II": "Ось люди-управленцы",
        "II-III": "Обслуживающий персонал и роботы",
        "II-IV": "Виртуозы и специалисты",
        "III-I": "Знания и наука",
        "III-II": "Стандартная процедура",
        "III-III": "Рабочая процедура",
        "III-IV": "Продукты, товар и услуга",
        "IV-I": "Управление коммуникационными системами",
        "IV-II": "Управление людьми",
        "IV-III": "Управление технологиями",
        "IV-IV": "Самоуправление",
    }
    quadrant = code.rsplit("-", 1)[0]
    return BusinessMachineCell(
        code=code,
        detail_operator=operators[quadrant],
        vertex=vertices[code],
        finding=f"Finding {code}",
        status="Подтверждено",
        confidence="Высокая",
        source_ids=["S1"],
        sales_relevance=f"Sales {code}",
    )


def test_split_v2_stages_km_quadrant_ii_cells_and_commercial_execution(monkeypatch) -> None:
    async def fake_profile(**kwargs):
        return MergedProfileExtraction(
            company_name="Example",
            business_summary="Engineering company",
            evidence=["Official evidence"],
            company_facts=[
                CompanyFact(
                    field="website",
                    value="https://example.com",
                    confidence="Высокая",
                    source_ids=["S1"],
                )
            ],
            economic_signals=[
                EconomicSignal(
                    signal="Automation signal",
                    evidence="Official process description",
                    business_effect="Potential pilot",
                    confidence="Средняя",
                    source_ids=["S1"],
                )
            ],
            risks_and_assumptions=[],
            coverage=_coverage(),
        )

    phases: list[str] = []
    phase_kwargs: dict[str, dict] = {}

    async def fake_request(phase, model_type, **kwargs):
        raise AssertionError((phase, model_type))

    async def fake_strict_request(phase, model_type, **kwargs):
        phases.append(phase)
        phase_kwargs[phase] = kwargs
        if model_type is split_v2.CompactBusinessMachineQuadrant:
            quadrant = phase.removeprefix("km_reasoning_")
            return split_v2.CompactBusinessMachineQuadrant(
                business_machine_4x4=[_km_cell(f"{quadrant}-I")]
            )
        if model_type is split_v2.CompactBusinessMachineCell:
            code = phase.removeprefix("km_reasoning_").replace("_", "-")
            return split_v2.CompactBusinessMachineCell(
                business_machine_4x4=[_km_cell(code)]
            )
        if model_type is split_v2.CompactCommercialOpportunity:
            return split_v2.CompactCommercialOpportunity(
                opportunity_type="AI automation",
                problem_hypothesis="Manual process",
                recommended_solution="AIMETON pilot",
                expected_value="Reduce manual work",
                score=72,
                qualification="Перспективная",
                source_ids=["S1", "MISSING"],
            )
        if model_type is split_v2.CompactCommercialExecution:
            assert '"score":72' in kwargs["prompt"]
            return split_v2.CompactCommercialExecution(
                agents=[
                    split_v2.CompactAgentRecommendation(name="A1", purpose="Search", benefit="Speed"),
                    split_v2.CompactAgentRecommendation(name="A2", purpose="Analyze", benefit="Evidence"),
                    split_v2.CompactAgentRecommendation(name="A3", purpose="Report", benefit="Structure"),
                ],
                action_package=split_v2.CompactActionPackage(
                    decision_maker_hypothesis="Digital lead",
                    contact_reason="Pilot",
                    demo_scenario=["Run audit"],
                    first_message="Pilot proposal",
                    next_action="Demo",
                ),
            )
        raise AssertionError(model_type)

    monkeypatch.setattr(split_v2, "extract_profile_parallel", fake_profile)
    monkeypatch.setattr(split_v2, "persist_merged_evidence_ledger", lambda merged: None)
    monkeypatch.setattr(split_v2, "_request_json", fake_request)
    monkeypatch.setattr(split_v2, "request_json_strict", fake_strict_request)

    result = asyncio.run(
        split_v2.analyze_with_routerai_split_v2(
            "https://example.com",
            "Example",
            "Official text",
            [],
        )
    )

    assert set(phases) == {
        "km_reasoning_I",
        "km_reasoning_II_I",
        "km_reasoning_II_II",
        "km_reasoning_II_III",
        "km_reasoning_II_IV",
        "km_reasoning_III",
        "km_reasoning_IV",
        "commercial_opportunity_reasoning",
        "commercial_execution",
    }
    for quadrant in ("I", "III", "IV"):
        km_kwargs = phase_kwargs[f"km_reasoning_{quadrant}"]
        assert km_kwargs["reasoning_effort"] == "high"
        assert km_kwargs["max_tokens"] == 1200
        assert km_kwargs["timeout_seconds"] == 18.0
        assert "Engineering company" in km_kwargs["prompt"]
        assert f"квадрант {quadrant}" in km_kwargs["prompt"]
    for suffix in ("I", "II", "III", "IV"):
        km_kwargs = phase_kwargs[f"km_reasoning_II_{suffix}"]
        assert km_kwargs["reasoning_effort"] == "high"
        assert km_kwargs["max_tokens"] == 700
        assert km_kwargs["timeout_seconds"] == 18.0
        assert "Engineering company" in km_kwargs["prompt"]
        assert f"II-{suffix}" in km_kwargs["prompt"]
    opportunity_kwargs = phase_kwargs["commercial_opportunity_reasoning"]
    assert opportunity_kwargs["reasoning_effort"] == "high"
    assert "reasoning_enabled" not in opportunity_kwargs
    assert opportunity_kwargs["max_tokens"] == 1500
    assert opportunity_kwargs["timeout_seconds"] == 25.0
    execution_kwargs = phase_kwargs["commercial_execution"]
    assert execution_kwargs["reasoning_enabled"] is False
    assert "reasoning_effort" not in execution_kwargs
    assert execution_kwargs["max_tokens"] == 1000
    assert execution_kwargs["timeout_seconds"] == 15.0
    assert "Engineering company" in opportunity_kwargs["prompt"]
    assert "Engineering company" in execution_kwargs["prompt"]
    assert result.company_name == "Example"
    assert result.sources[0].id == "S1"
    assert [cell.code for cell in result.business_machine_4x4] == [
        "I-I",
        "II-I",
        "II-II",
        "II-III",
        "II-IV",
        "III-I",
        "IV-I",
    ]
    assert result.commercial_opportunity.score == 72
    assert result.commercial_opportunity.source_ids == ["S1"]
    assert result.readiness.provider_states["routerai"] == "active"


def test_full_reasoning_profile_has_no_legacy_30_fact_or_16_signal_cap() -> None:
    facts = [
        CompanyFact(field="other", value=f"fact-{index}", source_ids=["S1"])
        for index in range(75)
    ]
    signals = [
        EconomicSignal(
            signal=f"signal-{index}",
            evidence=f"evidence-{index}",
            business_effect=f"effect-{index}",
            source_ids=["S1"],
        )
        for index in range(40)
    ]
    merged = MergedProfileExtraction(
        company_name="Large Co",
        business_summary="Large dossier",
        evidence=[f"evidence-item-{index}" for index in range(25)],
        company_facts=facts,
        economic_signals=signals,
        risks_and_assumptions=[f"risk-{index}" for index in range(20)],
        coverage=_coverage(),
    )

    reasoning = split_v2._full_reasoning_profile(merged)

    assert len(reasoning.company_facts) == 75
    assert reasoning.company_facts[-1].value == "fact-74"
    assert len(reasoning.economic_signals) == 40
    assert reasoning.economic_signals[-1].signal == "signal-39"
    assert len(reasoning.evidence) == 25
    assert reasoning.coverage["complete"] is True


def test_split_v2_km_envelopes_are_bounded_and_merge_is_fail_closed() -> None:
    quadrant_schema = split_v2.CompactBusinessMachineQuadrant.model_json_schema()
    assert quadrant_schema["properties"]["business_machine_4x4"]["maxItems"] == 4
    cell_schema = split_v2.CompactBusinessMachineCell.model_json_schema()
    assert cell_schema["properties"]["business_machine_4x4"]["maxItems"] == 1
    requests = [
        (
            ("I-I", "I-II", "I-III", "I-IV"),
            split_v2.CompactBusinessMachineQuadrant(
                business_machine_4x4=[_km_cell("I-I"), _km_cell("I-II")]
            ),
        ),
        (
            ("II-I",),
            split_v2.CompactBusinessMachineCell(
                business_machine_4x4=[_km_cell("I-II")]
            ),
        ),
        (
            ("II-II",),
            split_v2.CompactBusinessMachineCell(
                business_machine_4x4=[_km_cell("II-II")]
            ),
        ),
        (
            ("III-I", "III-II", "III-III", "III-IV"),
            split_v2.CompactBusinessMachineQuadrant(
                business_machine_4x4=[_km_cell("III-I")]
            ),
        ),
        (
            ("IV-I", "IV-II", "IV-III", "IV-IV"),
            split_v2.CompactBusinessMachineQuadrant(
                business_machine_4x4=[_km_cell("IV-I")]
            ),
        ),
    ]
    merged = split_v2._merge_km_results(requests)
    assert isinstance(merged, BusinessMachineSynthesis)
    assert [cell.code for cell in merged.business_machine_4x4] == [
        "I-I",
        "I-II",
        "II-II",
        "III-I",
        "IV-I",
    ]


def test_split_v2_commercial_envelopes_are_bounded_and_expandable() -> None:
    execution_schema = split_v2.CompactCommercialExecution.model_json_schema()
    assert execution_schema["properties"]["agents"]["minItems"] == 3
    assert execution_schema["properties"]["agents"]["maxItems"] == 5
    action_ref = execution_schema["properties"]["action_package"]["$ref"].split("/")[-1]
    action_schema = execution_schema["$defs"][action_ref]
    assert action_schema["properties"]["demo_scenario"]["maxItems"] == 3
    opportunity = split_v2.CompactCommercialOpportunity(
        opportunity_type="AI audit",
        problem_hypothesis="Manual review",
        recommended_solution="Pilot",
        expected_value="Faster evidence",
        score=70,
        qualification="Перспективная",
        source_ids=["S1"],
    )
    execution = split_v2.CompactCommercialExecution(
        agents=[
            split_v2.CompactAgentRecommendation(name="A1", purpose="Search", benefit="Speed"),
            split_v2.CompactAgentRecommendation(name="A2", purpose="Analyze", benefit="Evidence"),
            split_v2.CompactAgentRecommendation(name="A3", purpose="Report", benefit="Structure"),
        ],
        action_package=split_v2.CompactActionPackage(
            decision_maker_hypothesis="Digital lead",
            contact_reason="Pilot",
            demo_scenario=["Run audit"],
            first_message="Pilot proposal",
            next_action="Demo",
        ),
    )
    expanded = split_v2._expand_commercial(opportunity, execution)
    assert isinstance(expanded, CommercialSynthesis)
    assert len(expanded.agents) == 3
    assert expanded.commercial_opportunity.score == 70
    assert expanded.commercial_opportunity.source_ids == ["S1"]


def test_reasoning_failure_preserves_extracted_company_facts(monkeypatch):
    async def extract(**kwargs):
        return MergedProfileExtraction(
            company_name="Example", business_summary="Extracted profile", evidence=[],
            company_facts=[CompanyFact(field="executives", value="Поздний руководитель", source_ids=["S1"])],
            economic_signals=[], risks_and_assumptions=[], coverage=_coverage(),
        )
    async def fail(*args):
        raise SplitSynthesisPhaseError("commercial_execution", "OutputTruncated")
    monkeypatch.setattr(split_v2, "extract_profile_parallel", extract)
    monkeypatch.setattr(split_v2, "_reason_and_assemble", fail)
    result = asyncio.run(split_v2.analyze_with_routerai_split_v2("https://example.org", "Example", "Official text"))
    assert result.company_facts[0].value == "Поздний руководитель"
    assert result.company_facts[0].source_ids == ["S1"]
    assert result.readiness.provider_states["routerai"] == "reasoning_failed_extraction_preserved"
    assert result.readiness.client_release_eligible is False
    assert result.research_status["commercial_reasoning_error_phase"] == "commercial_execution"
    assert result.research_status["commercial_reasoning_phase_error_type"] == "OutputTruncated"


def test_partial_km_failure_does_not_cancel_commercial_and_opportunity_retries(monkeypatch):
    async def fake_profile(**kwargs):
        return MergedProfileExtraction(
            company_name="Example",
            business_summary="Engineering company",
            evidence=["Official evidence"],
            company_facts=[
                CompanyFact(field="website", value="https://example.com", source_ids=["S1"])
            ],
            economic_signals=[
                EconomicSignal(
                    signal="Manual process",
                    evidence="Official text describes manual process",
                    business_effect="Potential pilot",
                    source_ids=["S1"],
                )
            ],
            risks_and_assumptions=[],
            coverage=_coverage(),
        )

    opportunity_calls = 0

    async def fake_strict_request(phase, model_type, **kwargs):
        nonlocal opportunity_calls
        if phase == "km_reasoning_III":
            raise SplitSynthesisPhaseError(phase, "ProviderSchemaError")
        if model_type is split_v2.CompactBusinessMachineQuadrant:
            return split_v2.CompactBusinessMachineQuadrant()
        if model_type is split_v2.CompactBusinessMachineCell:
            return split_v2.CompactBusinessMachineCell()
        if model_type is split_v2.CompactCommercialOpportunity:
            opportunity_calls += 1
            if opportunity_calls == 1:
                raise SplitSynthesisPhaseError(phase, "OutputTruncated")
            assert phase == "commercial_opportunity_reasoning_retry"
            assert kwargs["reasoning_enabled"] is False
            return split_v2.CompactCommercialOpportunity(
                opportunity_type="AI automation",
                problem_hypothesis="Manual process",
                recommended_solution="AIMETON pilot",
                expected_value="Reduce manual work",
                score=72,
                qualification="Перспективная",
                source_ids=["S1"],
            )
        if model_type is split_v2.CompactCommercialExecution:
            return split_v2.CompactCommercialExecution(
                agents=[
                    split_v2.CompactAgentRecommendation(name="A1", purpose="Search", benefit="Speed"),
                    split_v2.CompactAgentRecommendation(name="A2", purpose="Analyze", benefit="Evidence"),
                    split_v2.CompactAgentRecommendation(name="A3", purpose="Report", benefit="Structure"),
                ],
                action_package=split_v2.CompactActionPackage(
                    decision_maker_hypothesis="Digital lead",
                    contact_reason="Pilot",
                    demo_scenario=["Run audit"],
                    first_message="Pilot proposal",
                    next_action="Demo",
                ),
            )
        raise AssertionError((phase, model_type))

    monkeypatch.setattr(split_v2, "extract_profile_parallel", fake_profile)
    monkeypatch.setattr(split_v2, "persist_merged_evidence_ledger", lambda merged: None)
    monkeypatch.setattr(split_v2, "request_json_strict", fake_strict_request)

    result = asyncio.run(
        split_v2.analyze_with_routerai_split_v2(
            "https://example.com",
            "Example",
            "Official text describes manual process",
            [],
        )
    )

    assert result.commercial_opportunity.score == 72
    assert result.research_status["commercial_reasoning_state"] == "succeeded"
    assert result.research_status["commercial_reasoning_retry_used"] is True
    assert result.research_status["km_reasoning_partial"] is True
    assert result.research_status["km_reasoning_failed_phases"] == (
        "km_reasoning_III:ProviderSchemaError"
    )
    assert result.readiness.provider_states["routerai"] == "active"
