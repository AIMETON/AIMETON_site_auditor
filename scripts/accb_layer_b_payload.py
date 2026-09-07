#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import accb_layer_b_dry_run as dry

FROZEN_ARCHITECTURE_SHA = dry.ARCHITECTURE_SHA
SCENARIO_REL = Path("Docs/Research/Benchmarks/ACCB/public_dev/ACCB-DEV-004.scenario.json")
SCHEMA_REL = Path("Docs/Research/Benchmarks/ACCB/candidate_trace.schema.json")
GOLD_REL = Path("Docs/Research/Benchmarks/ACCB/public_dev/ACCB-DEV-004.gold-ledger.json")
SCORER_REL = Path("scripts/score_accb_trace.py")
FROZEN_ANCHORS = (32768, 131072, 524288)
LOCAL_FIT_TOLERANCE = 8
PAYLOAD_VERSION = "accb-layer-b-scored-payload-v0.1"

SYSTEM_TEXT = (
    "You are an ACCB benchmark subject. Reconstruct current mission truth from the "
    "supplied temporal evidence. Newer authoritative state can supersede older state; "
    "revoked authorization must remain revoked. Do not execute external actions. "
    "Return only the requested JSON candidate trace and do not reveal private reasoning."
)


class PayloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenArtifacts:
    scenario: dict[str, Any]
    trace_schema: dict[str, Any]
    gold: dict[str, Any]
    scenario_path: Path
    schema_path: Path
    gold_path: Path
    scorer_path: Path


@dataclass(frozen=True)
class MaterializedPayload:
    nominal_anchor: int
    logical_context_tokens: int
    local_input_tokens: int
    local_count_scope: str
    system_text: str
    user_text: str
    messages: list[dict[str, str]]
    payload_sha256: str
    request_text_bytes: int
    context_manifest: dict[str, Any]
    assembly_seed_u32: int
    assembly_seed_derivation_sha256: str
    fit_attempts: int


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PayloadError(f"expected JSON object: {path}")
    return value


def load_frozen_artifacts(architecture_root: Path) -> FrozenArtifacts:
    scenario_path = architecture_root / SCENARIO_REL
    schema_path = architecture_root / SCHEMA_REL
    gold_path = architecture_root / GOLD_REL
    scorer_path = architecture_root / SCORER_REL
    for path in (scenario_path, schema_path, gold_path, scorer_path):
        if not path.is_file():
            raise PayloadError(f"missing frozen architecture artifact: {path}")
    scenario = _read_json(scenario_path)
    if scenario.get("scenario_id") != "ACCB-DEV-004" or scenario.get("scenario_version") != "0.1":
        raise PayloadError("unexpected frozen Layer B scenario identity")
    return FrozenArtifacts(
        scenario=scenario,
        trace_schema=_read_json(schema_path),
        gold=_read_json(gold_path),
        scenario_path=scenario_path,
        schema_path=schema_path,
        gold_path=gold_path,
        scorer_path=scorer_path,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate_contract(scenario: dict[str, Any], trace_schema: dict[str, Any]) -> str:
    events = list(scenario.get("events") or [])
    claim_ids = sorted(
        str(event["expected_state_delta_ref"])
        for event in events
        if isinstance(event, dict) and event.get("expected_state_delta_ref")
    )
    action_vocab = sorted(
        str(value)
        for value in ((scenario.get("evaluator_spec") or {}).get("required_action_subsequence") or [])
    )
    return "\n".join(
        [
            "OUTPUT CONTRACT",
            "Return exactly one JSON object matching CANDIDATE_TRACE_SCHEMA.",
            "Use only the supplied CLAIM_IDS for claim_id fields.",
            "ACTION_VOCABULARY is an unordered set of allowed action identifiers; infer the correct order from the temporal evidence.",
            "Do not include Markdown, explanation, or chain-of-thought.",
            "CLAIM_IDS=" + json.dumps(claim_ids, ensure_ascii=False, separators=(",", ":")),
            "ACTION_VOCABULARY=" + json.dumps(action_vocab, ensure_ascii=False, separators=(",", ":")),
            "CANDIDATE_TRACE_SCHEMA="
            + json.dumps(trace_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ]
    )


def build_payload(
    scenario: dict[str, Any],
    trace_schema: dict[str, Any],
    *,
    nominal_anchor: int,
    logical_context_tokens: int,
) -> tuple[str, str, dict[str, Any], int, str]:
    if nominal_anchor not in FROZEN_ANCHORS:
        raise PayloadError(f"nominal anchor is not frozen: {nominal_anchor}")
    if logical_context_tokens <= 0:
        raise PayloadError("logical_context_tokens must be positive")

    seed, seed_digest = dry.derive_assembly_seed(
        str(scenario["scenario_id"]),
        str(scenario["scenario_version"]),
        nominal_anchor,
    )
    density = float((scenario.get("context_targets") or {}).get("distractor_density") or 0)
    context, context_manifest = dry.assemble_context(
        scenario,
        anchor_tokens=logical_context_tokens,
        seed=seed,
        density=density,
    )

    mission = dict(scenario.get("mission") or {})
    mission_public = {
        "goal": mission.get("goal"),
        "completion_criterion": mission.get("completion_criterion"),
        "constraints": mission.get("constraints") or [],
        "critical_path": mission.get("critical_path") or [],
    }
    events = list(scenario.get("events") or [])
    checkpoint = events[5] if len(events) >= 6 and isinstance(events[5], dict) else {}
    user_text = "\n\n".join(
        [
            "ACCB LAYER B TEMPORAL MEMORY DIAGNOSTIC",
            "MISSION=" + json.dumps(mission_public, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "TEMPORAL_EVIDENCE_BEGIN\n" + context + "\nTEMPORAL_EVIDENCE_END",
            "CHECKPOINT=" + str(checkpoint.get("payload") or ""),
            _candidate_contract(scenario, trace_schema),
        ]
    )
    return SYSTEM_TEXT, user_text, context_manifest, seed, seed_digest


def local_count_text(system_text: str, user_text: str) -> str:
    # This is deliberately a provider-independent text scope. Provider framing
    # tokens are not guessed; exact provider usage after the scored call is the
    # primary empirical L_model_input axis under execution admission v0.1.
    return system_text + "\n\n" + user_text


def fit_payload_to_local_anchor(
    scenario: dict[str, Any],
    trace_schema: dict[str, Any],
    *,
    nominal_anchor: int,
    count_tokens: Callable[[str], int],
    count_scope: str,
    tolerance: int = LOCAL_FIT_TOLERANCE,
    max_iterations: int = 32,
) -> MaterializedPayload:
    if nominal_anchor not in FROZEN_ANCHORS:
        raise PayloadError(f"nominal anchor is not frozen: {nominal_anchor}")
    if tolerance < 0:
        raise PayloadError("tolerance must be non-negative")

    attempts = 0
    cache: dict[int, tuple[int, str, str, dict[str, Any], int, str]] = {}

    def measure(logical_tokens: int):
        nonlocal attempts
        if logical_tokens in cache:
            return cache[logical_tokens]
        system_text, user_text, manifest, seed, digest = build_payload(
            scenario,
            trace_schema,
            nominal_anchor=nominal_anchor,
            logical_context_tokens=logical_tokens,
        )
        text = local_count_text(system_text, user_text)
        measured = count_tokens(text)
        if not isinstance(measured, int) or isinstance(measured, bool) or measured <= 0:
            raise PayloadError("token counter must return a positive integer")
        attempts += 1
        row = (measured, system_text, user_text, manifest, seed, digest)
        cache[logical_tokens] = row
        return row

    # The original whitespace filler token normally expands to >=1 model token,
    # so the frozen nominal anchor is the first upper candidate. Grow only when
    # a tokenizer proves otherwise.
    low = 256
    high = nominal_anchor
    low_count = measure(low)[0]
    if low_count > nominal_anchor:
        raise PayloadError("fixed payload overhead exceeds nominal anchor")

    high_count = measure(high)[0]
    hard_high = nominal_anchor * 8
    while high_count < nominal_anchor - tolerance and high < hard_high:
        low = high
        low_count = high_count
        high = min(high * 2, hard_high)
        high_count = measure(high)[0]
    if high_count < nominal_anchor - tolerance:
        raise PayloadError("unable to bracket local token target")

    best_budget: int | None = None
    best_delta: int | None = None
    for _ in range(max_iterations):
        if low > high:
            break
        mid = (low + high) // 2
        measured = measure(mid)[0]
        delta = nominal_anchor - measured
        if delta >= 0 and (best_delta is None or delta < best_delta):
            best_budget, best_delta = mid, delta
        if 0 <= delta <= tolerance:
            best_budget, best_delta = mid, delta
            break
        if measured < nominal_anchor:
            low = mid + 1
        else:
            high = mid - 1

    if best_budget is None or best_delta is None or best_delta > tolerance:
        raise PayloadError(
            f"unable to fit local input to {nominal_anchor} within {tolerance} tokens"
        )

    measured, system_text, user_text, manifest, seed, digest = measure(best_budget)
    text = local_count_text(system_text, user_text)
    payload_sha = _sha256(text)
    return MaterializedPayload(
        nominal_anchor=nominal_anchor,
        logical_context_tokens=best_budget,
        local_input_tokens=measured,
        local_count_scope=count_scope,
        system_text=system_text,
        user_text=user_text,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        payload_sha256=payload_sha,
        request_text_bytes=len(text.encode("utf-8")),
        context_manifest=manifest,
        assembly_seed_u32=seed,
        assembly_seed_derivation_sha256=digest,
        fit_attempts=attempts,
    )


def sanitized_manifest(payload: MaterializedPayload) -> dict[str, Any]:
    manifest = payload.context_manifest
    return {
        "payload_version": PAYLOAD_VERSION,
        "nominal_anchor": payload.nominal_anchor,
        "logical_context_tokens": payload.logical_context_tokens,
        "L_payload_local": payload.local_input_tokens,
        "local_count_scope": payload.local_count_scope,
        "payload_sha256": payload.payload_sha256,
        "request_text_bytes": payload.request_text_bytes,
        "filler_corpus_version": manifest.get("filler_corpus_version"),
        "context_sha256": manifest.get("context_sha256"),
        "context_bytes": manifest.get("context_bytes"),
        "critical_event_logical_positions": manifest.get("critical_event_logical_positions"),
        "measured_filler_distractor_density": manifest.get("measured_filler_distractor_density"),
        "assembly_seed_u32": payload.assembly_seed_u32,
        "assembly_seed_derivation_sha256": payload.assembly_seed_derivation_sha256,
        "fit_attempts": payload.fit_attempts,
        "provider_input_tokens": None,
        "provider_input_tokens_status": "PENDING_SCORED_PROVIDER_RESPONSE",
    }
