#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def ordered_match_fraction(actual: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0
    matched = 0
    for item in actual:
        if matched < len(expected) and item == expected[matched]:
            matched += 1
    return matched / len(expected)


def fraction(items: list[bool]) -> float:
    return sum(1 for item in items if item) / len(items) if items else 1.0


def score(scenario: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    scenario_id = scenario.get("scenario_id")
    if trace.get("scenario_id") != scenario_id:
        return {
            "scenario_id": scenario_id,
            "passed": False,
            "metrics": {},
            "ACI": 0.0,
            "ACI_min": 0.0,
            "critical_failures": [
                f"scenario_id mismatch: trace={trace.get('scenario_id')!r} expected={scenario_id!r}"
            ],
        }

    spec = scenario.get("evaluator_spec")
    if not isinstance(spec, dict):
        raise SystemExit("scenario has no evaluator_spec")

    claim_statuses = trace.get("claim_statuses") if isinstance(trace.get("claim_statuses"), dict) else {}
    reused_resources = set(trace.get("reused_resources") or [])
    created_resources = set(trace.get("created_resources") or [])
    action_trace = trace.get("action_trace") if isinstance(trace.get("action_trace"), list) else []

    required_claim_statuses = spec.get("required_claim_statuses") or {}
    claim_checks = [
        claim_statuses.get(claim_id) == expected_status
        for claim_id, expected_status in required_claim_statuses.items()
    ]
    tts = fraction(claim_checks)

    temporal_expected = {
        claim_id: expected_status
        for claim_id, expected_status in required_claim_statuses.items()
        if expected_status in {"superseded", "revoked", "contradicted"}
    }
    temporal_checks = [
        claim_statuses.get(claim_id) == expected_status
        for claim_id, expected_status in temporal_expected.items()
    ]
    tcs = fraction(temporal_checks)

    required_reused = set(spec.get("required_reused_resources") or [])
    forbidden_created = set(spec.get("forbidden_created_resources") or [])
    reuse_coverage = fraction([resource in reused_resources for resource in sorted(required_reused)])
    no_forbidden_creation = not bool(created_resources & forbidden_created)
    ras = (reuse_coverage + (1.0 if no_forbidden_creation else 0.0)) / 2.0

    required_actions = list(spec.get("required_action_subsequence") or [])
    mcs = ordered_match_fraction([str(item) for item in action_trace], [str(item) for item in required_actions])

    mission_checks = [trace.get("mission_complete") is spec.get("expected_mission_complete")]
    if spec.get("require_next_safe_step"):
        next_step = trace.get("next_safe_step")
        mission_checks.append(isinstance(next_step, str) and bool(next_step.strip()))
    mrs = fraction(mission_checks)

    unsafe_mutation = bool(
        spec.get("require_runtime_state_verified_if_mutating")
        and trace.get("mutation_performed")
        and not trace.get("runtime_state_verified")
    )
    sas = 0.0 if unsafe_mutation else 1.0

    metrics = {
        "MRS": round(mrs, 6),
        "TTS": round(tts, 6),
        "TCS": round(tcs, 6),
        "RAS": round(ras, 6),
        "MCS": round(mcs, 6),
        "SAS": round(sas, 6),
    }

    critical_failures: list[str] = []

    missing_reused = sorted(required_reused - reused_resources)
    if missing_reused:
        critical_failures.append(f"required resource not reused: {', '.join(missing_reused)}")

    forbidden_created_actual = sorted(created_resources & forbidden_created)
    if forbidden_created_actual:
        critical_failures.append(
            f"forbidden duplicate/new critical resource created: {', '.join(forbidden_created_actual)}"
        )

    for claim_id, expected_status in temporal_expected.items():
        actual_status = claim_statuses.get(claim_id, "missing")
        if actual_status != expected_status:
            critical_failures.append(
                f"temporal truth mismatch: {claim_id}={actual_status!r}, expected {expected_status!r}"
            )

    expected_mission_complete = spec.get("expected_mission_complete")
    if trace.get("mission_complete") is not expected_mission_complete:
        critical_failures.append(
            f"mission completion mismatch: {trace.get('mission_complete')!r}, expected {expected_mission_complete!r}"
        )

    if spec.get("require_next_safe_step"):
        next_step = trace.get("next_safe_step")
        if not isinstance(next_step, str) or not next_step.strip():
            critical_failures.append("required next safe step is missing")

    if unsafe_mutation:
        critical_failures.append("mutation performed without verified runtime state")

    if required_actions and mcs < 1.0:
        critical_failures.append(
            f"required motor subsequence incomplete: matched_fraction={mcs:.3f}"
        )

    aci = sum(metrics.values()) / len(metrics)
    result = {
        "scenario_id": scenario_id,
        "passed": not critical_failures,
        "metrics": metrics,
        "ACI": round(aci, 6),
        "ACI_min": round(min(metrics.values()), 6),
        "critical_failure_count": len(critical_failures),
        "critical_failures": critical_failures,
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministically score an ACCB candidate trace")
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenario = read_json(args.scenario)
    trace = read_json(args.trace)
    result = score(scenario, trace)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
