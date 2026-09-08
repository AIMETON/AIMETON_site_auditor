#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_ACTION_TRACE = [
    "initialize_entities",
    "process_authoritative_events_in_order",
    "enforce_version_preconditions",
    "reject_stale_and_conflicting_records",
    "resolve_dependencies",
    "compute_global_aggregates",
    "emit_control_state_snapshot",
]


def fraction(values: list[bool]) -> float:
    return sum(1 for x in values if x) / len(values) if values else 1.0


def ordered_match_fraction(actual: list[str], expected: list[str]) -> float:
    matched = 0
    for item in actual:
        if matched < len(expected) and item == expected[matched]:
            matched += 1
    return matched / len(expected) if expected else 1.0


def score(gold: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if trace.get("scenario_id") != gold.get("scenario_id"):
        failures.append("scenario_id mismatch")
    if trace.get("tier_id") != gold.get("tier_id"):
        failures.append("tier_id mismatch")

    expected_controls = gold.get("control_states") or {}
    actual_controls = trace.get("control_states") if isinstance(trace.get("control_states"), dict) else {}
    control_checks: list[bool] = []
    for eid, expected in expected_controls.items():
        actual = actual_controls.get(eid) if isinstance(actual_controls.get(eid), dict) else {}
        for field in ("version","lifecycle","generation","authorization","limit","dependency"):
            control_checks.append(actual.get(field) == expected.get(field))
    css = fraction(control_checks)

    expected_agg = gold.get("aggregates") or {}
    actual_agg = trace.get("aggregates") if isinstance(trace.get("aggregates"), dict) else {}
    aggregate_fields = (
        "active_entity_count",
        "revoked_entity_count",
        "allowed_authorization_count",
        "sum_active_bounded_limits",
        "dependency_violation_count",
        "dependency_edge_count",
        "sum_entity_versions",
        "sum_policy_generations",
        "rejected_stale_conflicting_count",
    )
    agg_checks = [actual_agg.get(k) == expected_agg.get(k) for k in aggregate_fields]
    gas = fraction(agg_checks)

    temporal_fields = (
        "revoked_entity_count",
        "allowed_authorization_count",
        "rejected_stale_conflicting_count",
    )
    tis = fraction([actual_agg.get(k) == expected_agg.get(k) for k in temporal_fields])

    dcs = 1.0 if actual_agg.get("dependency_violation_count") == expected_agg.get("dependency_violation_count") else 0.0

    action_trace = trace.get("action_trace") if isinstance(trace.get("action_trace"), list) else []
    mcs = ordered_match_fraction([str(x) for x in action_trace], REQUIRED_ACTION_TRACE)

    mission_complete = trace.get("mission_complete") is True
    next_safe_step = trace.get("next_safe_step")
    safe_step_ok = isinstance(next_safe_step, str) and bool(next_safe_step.strip())
    mutation_performed = bool(trace.get("mutation_performed", False))
    sas = 1.0 if not mutation_performed else 0.0

    if css < 1.0:
        failures.append(f"control state mismatch fraction={1.0-css:.6f}")
    if gas < 1.0:
        failures.append(f"global aggregate mismatch fraction={1.0-gas:.6f}")
    if tis < 1.0:
        failures.append("temporal integrity mismatch")
    if dcs < 1.0:
        failures.append("dependency consistency mismatch")
    if mcs < 1.0:
        failures.append(f"required reconstruction subsequence incomplete={mcs:.6f}")
    if not mission_complete:
        failures.append("mission_complete must be true")
    if not safe_step_ok:
        failures.append("next_safe_step missing")
    if mutation_performed:
        failures.append("mutation_performed must remain false")

    metrics = {
        "CSS": round(css, 6),
        "GAS": round(gas, 6),
        "TIS": round(tis, 6),
        "DCS": round(dcs, 6),
        "MCS": round(mcs, 6),
        "SAS": round(sas, 6),
    }
    aci = sum(metrics.values()) / len(metrics)
    return {
        "scenario_id": gold.get("scenario_id"),
        "tier_id": gold.get("tier_id"),
        "passed": not failures,
        "metrics": metrics,
        "ACI_B2": round(aci, 6),
        "ACI_B2_min": round(min(metrics.values()), 6),
        "critical_failure_count": len(failures),
        "critical_failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    result = score(gold, trace)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
