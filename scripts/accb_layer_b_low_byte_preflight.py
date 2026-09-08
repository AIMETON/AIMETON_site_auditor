#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import accb_layer_b_dry_run as dry
import accb_layer_b_payload as payload

TARGETS = (
    ("low-32kib", 32768),
    ("low-64kib", 65536),
)
SEED_OFFSET = 1_000_000_000
MAX_PADDING_BYTES = 64


class LowByteError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_for_logical(
    scenario: dict[str, Any],
    trace_schema: dict[str, Any],
    *,
    target_bytes: int,
    logical_context_tokens: int,
) -> tuple[str, str, dict[str, Any], int, str]:
    seed_anchor = SEED_OFFSET + target_bytes
    seed, digest = dry.derive_assembly_seed(
        str(scenario["scenario_id"]),
        str(scenario["scenario_version"]),
        seed_anchor,
    )
    density = float((scenario.get("context_targets") or {}).get("distractor_density") or 0)
    context, manifest = dry.assemble_context(
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
            payload._candidate_contract(scenario, trace_schema),
        ]
    )
    return payload.SYSTEM_TEXT, user_text, manifest, seed, digest


def exact_fit(
    scenario: dict[str, Any],
    trace_schema: dict[str, Any],
    *,
    tier_id: str,
    target_bytes: int,
) -> dict[str, Any]:
    def measure(logical: int):
        system_text, user_text, manifest, seed, digest = build_for_logical(
            scenario,
            trace_schema,
            target_bytes=target_bytes,
            logical_context_tokens=logical,
        )
        request_text = payload.local_count_text(system_text, user_text)
        return len(request_text.encode("utf-8")), system_text, user_text, manifest, seed, digest

    low = 256
    low_bytes, *_ = measure(low)
    if low_bytes > target_bytes:
        raise LowByteError(f"fixed payload overhead exceeds {tier_id}: {low_bytes} > {target_bytes}")

    high = 512
    high_bytes, *_ = measure(high)
    while high_bytes <= target_bytes and high < 131072:
        low = high
        low_bytes = high_bytes
        high *= 2
        high_bytes, *_ = measure(high)
    if high_bytes <= target_bytes:
        raise LowByteError(f"unable to bracket byte target for {tier_id}")

    best = None
    while low <= high:
        mid = (low + high) // 2
        measured = measure(mid)
        size = measured[0]
        if size <= target_bytes:
            best = (mid, measured)
            low = mid + 1
        else:
            high = mid - 1
    if best is None:
        raise LowByteError(f"no payload fits below {tier_id}")

    logical, measured = best
    size, system_text, user_text, manifest, seed, digest = measured
    pad = target_bytes - size
    if pad < 0 or pad > MAX_PADDING_BYTES:
        raise LowByteError(f"exact-fit padding too large for {tier_id}: {pad}")

    marker = "\nTEMPORAL_EVIDENCE_END"
    idx = user_text.index(marker)
    user_text = user_text[:idx] + (" " * pad) + user_text[idx:]
    request_text = payload.local_count_text(system_text, user_text)
    final_bytes = len(request_text.encode("utf-8"))
    if final_bytes != target_bytes:
        raise LowByteError(f"exact byte fit failed for {tier_id}: {final_bytes} != {target_bytes}")

    context_begin = user_text.index("TEMPORAL_EVIDENCE_BEGIN\n") + len("TEMPORAL_EVIDENCE_BEGIN\n")
    context_end = user_text.index("\nTEMPORAL_EVIDENCE_END", context_begin)
    context = user_text[context_begin:context_end]
    context_sha = hashlib.sha256(context.encode("utf-8")).hexdigest()

    # Record byte-relative positions so the low-byte extension can verify that
    # padding does not materially move the five frozen critical facts.
    realized: dict[str, float] = {}
    context_bytes = context.encode("utf-8")
    for event_id in ("B1", "B2", "B3", "B4", "B5"):
        marker_bytes = f"[{event_id}:".encode("utf-8")
        pos = context_bytes.index(marker_bytes)
        realized[event_id] = round(pos / len(context_bytes), 6)

    targets = [float(x) for x in ((scenario.get("context_targets") or {}).get("critical_fact_positions") or [])]
    observed = [realized[x] for x in ("B1", "B2", "B3", "B4", "B5")]
    max_abs_error = max(abs(a - b) for a, b in zip(observed, targets))

    return {
        "tier_id": tier_id,
        "target_request_text_bytes": target_bytes,
        "request_text_bytes": final_bytes,
        "request_text_characters": len(request_text),
        "payload_sha256": sha256_text(request_text),
        "context_sha256": context_sha,
        "context_bytes": len(context_bytes),
        "logical_context_tokens": logical,
        "padding_bytes": pad,
        "assembly_seed_anchor": SEED_OFFSET + target_bytes,
        "assembly_seed_u32": seed,
        "assembly_seed_derivation_sha256": digest,
        "target_critical_fact_positions": targets,
        "realized_critical_fact_byte_positions": realized,
        "max_abs_critical_position_error": round(max_abs_error, 6),
        "measured_filler_distractor_density": manifest.get("measured_filler_distractor_density"),
        "scenario_id": scenario.get("scenario_id"),
        "scenario_version": scenario.get("scenario_version"),
        "tokenizer_required": False,
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
    }


def build_report(architecture_root: Path) -> dict[str, Any]:
    frozen = payload.load_frozen_artifacts(architecture_root)
    rows = [
        exact_fit(
            frozen.scenario,
            frozen.trace_schema,
            tier_id=tier_id,
            target_bytes=target,
        )
        for tier_id, target in TARGETS
    ]
    if [x["request_text_bytes"] for x in rows] != [32768, 65536]:
        raise LowByteError("low-byte exact targets were not materialized")
    if any(x["max_abs_critical_position_error"] > 0.01 for x in rows):
        raise LowByteError("critical fact byte-position drift exceeds 1 percentage point")
    return {
        "schema_version": "0.1",
        "status": "ACCB_LAYER_B_LOW_BYTE_PREFLIGHT_READY",
        "planned_cells": 10,
        "model_count": 5,
        "tier_count": 2,
        "primary_cross_model_input_axis": "request_text_bytes",
        "tokenizer_required_at_execution": False,
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
        "tiers": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.architecture_root)
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
