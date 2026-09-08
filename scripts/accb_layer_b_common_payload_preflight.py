#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import accb_layer_b_dry_run as dry
import accb_layer_b_payload as payload

SCHEDULE_PATH = Path("docs/research/ACCB_LAYER_B_COMMON_PAYLOAD_SCHEDULE_v0.3.json")


class CommonPayloadError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CommonPayloadError(f"expected JSON object: {path}")
    return value


def build_report(architecture_root: Path, schedule_path: Path = SCHEDULE_PATH) -> dict[str, Any]:
    schedule = _read_json(schedule_path)
    if schedule.get("status") != "FROZEN_EXECUTION_PAYLOAD_SCHEDULE":
        raise CommonPayloadError("payload schedule is not frozen")
    if schedule.get("tokenizer_required_at_execution") is not False:
        raise CommonPayloadError("runtime tokenizer dependency is not admitted")
    if schedule.get("execution_admission_sha") != "67c8ea3e84405884136119d7252fe7424ccf1631":
        raise CommonPayloadError("unexpected execution admission SHA")

    snapshot = dry.verify_snapshot(architecture_root)
    frozen = payload.load_frozen_artifacts(architecture_root)
    rows: list[dict[str, Any]] = []

    for spec in schedule.get("anchors") or []:
        anchor = int(spec["nominal_anchor"])
        logical = int(spec["logical_context_tokens"])
        system_text, user_text, manifest, seed, seed_digest = payload.build_payload(
            frozen.scenario,
            frozen.trace_schema,
            nominal_anchor=anchor,
            logical_context_tokens=logical,
        )
        request_text = payload.local_count_text(system_text, user_text)
        request_bytes = len(request_text.encode("utf-8"))
        request_chars = len(request_text)
        request_sha = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
        if request_bytes != int(spec["expected_request_text_bytes"]):
            raise CommonPayloadError(f"request byte drift at {anchor}: {request_bytes}")
        if request_sha != str(spec["expected_payload_sha256"]):
            raise CommonPayloadError(f"payload hash drift at {anchor}")
        if manifest.get("context_sha256") != spec["expected_context_sha256"]:
            raise CommonPayloadError(f"context hash drift at {anchor}")
        rows.append(
            {
                "nominal_anchor": anchor,
                "logical_context_tokens": logical,
                "payload_sha256": request_sha,
                "request_text_bytes": request_bytes,
                "request_text_characters": request_chars,
                "context_sha256": manifest.get("context_sha256"),
                "context_bytes": manifest.get("context_bytes"),
                "critical_event_logical_positions": manifest.get("critical_event_logical_positions"),
                "target_critical_fact_positions": manifest.get("target_critical_fact_positions"),
                "assembly_seed_u32": seed,
                "assembly_seed_derivation_sha256": seed_digest,
                "L_payload_local_estimate": None,
                "tokenizer_identity_optional": None,
                "L_model_input_provider": None,
                "provider_input_measurement_status": "PENDING_SCORED_PROVIDER_RESPONSE",
            }
        )

    if [row["nominal_anchor"] for row in rows] != [32768, 131072, 524288]:
        raise CommonPayloadError("unexpected anchor schedule")

    return {
        "schema_version": "0.1",
        "status": "ACCB_LAYER_B_COMMON_PAYLOAD_READY",
        "frozen_architecture_sha": payload.FROZEN_ARCHITECTURE_SHA,
        "execution_admission_sha": schedule["execution_admission_sha"],
        "snapshot_verification": snapshot,
        "payload_schedule": str(schedule_path),
        "same_payload_per_anchor_for_all_models": True,
        "tokenizer_required_at_execution": False,
        "primary_cross_model_input_axis": schedule["primary_cross_model_input_axis"],
        "payload_identity_field": schedule["payload_identity_field"],
        "provider_input_tokens_role": schedule["provider_input_tokens_role"],
        "primary_input_length_measurement": schedule["primary_input_length_measurement"],
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
        "provider_api_secrets_used": False,
        "anchors": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture-root", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, default=SCHEDULE_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.architecture_root, args.schedule)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "anchors": len(report["anchors"]),
                "tokenizer_required_at_execution": False,
                "provider_generation_requests": 0,
                "paid_spend_authorized_rub": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
