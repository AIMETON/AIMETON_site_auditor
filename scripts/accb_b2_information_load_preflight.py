#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ARCH_SHA = "d390b2f56c0b2dae4be0cc4810dcb86403f2dc26"
SNAPSHOT = Path("docs/research/accb_b2_snapshot") / ARCH_SHA
GENERATOR = SNAPSHOT / "generate_accb_b2_information_load.py"


class PreflightError(RuntimeError):
    pass


def load_generator():
    if not GENERATOR.is_file():
        raise PreflightError("vendored B2 generator missing")
    spec = importlib.util.spec_from_file_location("accb_b2_frozen_generator", GENERATOR)
    if spec is None or spec.loader is None:
        raise PreflightError("cannot load vendored B2 generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_report() -> dict[str, Any]:
    gen = load_generator()
    suite = gen.build_suite()
    if suite.get("status") != "ACCB_B2_INFORMATION_LOAD_READY":
        raise PreflightError("B2 generator did not reach READY")
    tiers = suite.get("tiers")
    if not isinstance(tiers, list) or len(tiers) != 5:
        raise PreflightError("B2 tier matrix must contain exactly five tiers")

    expected = [32768, 65536, 143934, 575367, 2297725]
    observed = [int(x["request_text_bytes"]) for x in tiers]
    if observed != expected:
        raise PreflightError(f"exact byte grid drift: {observed!r}")

    event_counts = [int(x["event_count"]) for x in tiers]
    auth_counts = [int(x["authoritative_transition_count"]) for x in tiers]
    entity_counts = [int(x["tier_entity_count"]) for x in tiers]
    if not all(a < b for a, b in zip(event_counts, event_counts[1:])):
        raise PreflightError("event count does not strictly increase")
    if not all(a < b for a, b in zip(auth_counts, auth_counts[1:])):
        raise PreflightError("authoritative transition count does not strictly increase")
    if entity_counts != [16, 24, 32, 48, 64]:
        raise PreflightError("entity-width scaling drift")

    for row in tiers:
        proof = row.get("semantic_consequence") or {}
        if proof.get("all_authoritative_events_consequential") is not True:
            raise PreflightError(f"semantic consequence proof failed: {row.get('tier_id')}")
        if proof.get("rejected_records_consequential_via_aggregate") is not True:
            raise PreflightError(f"rejected-record consequence failed: {row.get('tier_id')}")
        if float(row.get("semantic_evidence_ratio", 0.0)) < 0.98:
            raise PreflightError(f"semantic density below 0.98: {row.get('tier_id')}")
        if int(row.get("terminal_padding_bytes", 999999)) > 256:
            raise PreflightError(f"padding exceeds bound: {row.get('tier_id')}")

    return {
        **suite,
        "frozen_architecture_sha": ARCH_SHA,
        "tokenizer_required_at_execution": False,
        "legacy_global_8192_output_cap_allowed": False,
        "provider_api_secrets_used": False,
        "network_calls_performed": 0,
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
