from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import accb_layer_b_dry_run as dry
import accb_layer_b_payload as payload


TRACE_SCHEMA = {
    "type": "object",
    "required": ["claim_trace", "action_trace", "mission_complete", "next_safe_step"],
    "properties": {
        "claim_trace": {"type": "array"},
        "action_trace": {"type": "array"},
        "mission_complete": {"type": "boolean"},
        "next_safe_step": {"type": "string"},
    },
}


def scenario() -> dict:
    return dry._read_json(dry.SCENARIO_PATH)


def test_scored_payload_is_deterministic_and_does_not_embed_gold_statuses() -> None:
    s = scenario()
    first = payload.build_payload(
        s, TRACE_SCHEMA, nominal_anchor=32768, logical_context_tokens=12000
    )
    second = payload.build_payload(
        s, TRACE_SCHEMA, nominal_anchor=32768, logical_context_tokens=12000
    )

    assert first == second
    system_text, user_text, manifest, seed, digest = first
    assert "C-B01" in user_text
    assert "C-B05" in user_text
    assert "required_claim_statuses" not in user_text
    assert '"C-B01":"superseded"' not in user_text
    assert '"C-B04":"revoked"' not in user_text
    assert "ACTION_VOCABULARY=" in user_text
    assert "CANDIDATE_TRACE_SCHEMA=" in user_text
    assert "TEMPORAL_EVIDENCE_BEGIN" in user_text
    assert manifest["filler_corpus_version"] == dry.FILLER_CORPUS_VERSION
    expected_seed, expected_digest = dry.derive_assembly_seed(
        "ACCB-DEV-004", "0.1", 32768
    )
    assert seed == expected_seed
    assert digest == expected_digest
    assert system_text == payload.SYSTEM_TEXT


def test_local_fit_measures_exact_payload_not_nominal_or_char_count() -> None:
    fitted = payload.fit_payload_to_local_anchor(
        scenario(),
        TRACE_SCHEMA,
        nominal_anchor=32768,
        count_tokens=lambda text: len(text.split()),
        count_scope="unit-test-whitespace-counter",
        tolerance=8,
    )

    assert 0 <= 32768 - fitted.local_input_tokens <= 8
    assert fitted.local_input_tokens != len(
        payload.local_count_text(fitted.system_text, fitted.user_text)
    )
    assert fitted.logical_context_tokens != fitted.nominal_anchor
    assert fitted.local_count_scope == "unit-test-whitespace-counter"
    manifest = payload.sanitized_manifest(fitted)
    assert manifest["L_payload_local"] == fitted.local_input_tokens
    assert manifest["provider_input_tokens"] is None
    assert manifest["provider_input_tokens_status"] == "PENDING_SCORED_PROVIDER_RESPONSE"
    assert manifest["context_local_tokens"] > 0
    assert set(manifest["critical_event_local_token_positions"]) == {"B1", "B2", "B3", "B4", "B5"}
    targets = manifest["target_critical_fact_positions"]
    observed = manifest["critical_event_local_token_positions"]
    for event_id, target in zip(("B1", "B2", "B3", "B4", "B5"), targets):
        assert abs(observed[event_id] - target) < 0.02


def test_fit_preserves_nominal_anchor_seed_when_logical_budget_changes() -> None:
    fitted = payload.fit_payload_to_local_anchor(
        scenario(),
        TRACE_SCHEMA,
        nominal_anchor=131072,
        count_tokens=lambda text: len(text.split()),
        count_scope="unit-test-whitespace-counter",
        tolerance=8,
    )
    seed, digest = dry.derive_assembly_seed("ACCB-DEV-004", "0.1", 131072)
    assert fitted.assembly_seed_u32 == seed
    assert fitted.assembly_seed_derivation_sha256 == digest
    assert fitted.context_manifest["target_critical_fact_positions"] == [
        0.08,
        0.28,
        0.54,
        0.72,
        0.94,
    ]
    assert abs(
        fitted.context_manifest["measured_filler_distractor_density"] - 0.35
    ) < 0.01
