#!/usr/bin/env python3
"""Validate the immutable runner projection checked into Site Auditor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECTION_PATH = ROOT / "contracts" / "generated" / "runner-contract-projection.json"


class ProjectionError(ValueError):
    """Raised when generated projection integrity or membership is invalid."""


def projection_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_projection(path: Path = PROJECTION_PATH) -> dict[str, Any]:
    try:
        projection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"cannot load generated projection: {exc}") from exc
    if projection.get("schema_version") != "1.0-generated":
        raise ProjectionError("unsupported generated projection schema")
    generation = projection.get("generation")
    if not isinstance(generation, dict) or generation.get("version") != 2:
        raise ProjectionError("projection generation version is invalid")
    if generation.get("infrastructure_repository") != "AIMETON/aimeton-infrastructure":
        raise ProjectionError("projection canonical repository is invalid")
    if not str(generation.get("generated_at", "")).endswith("Z"):
        raise ProjectionError("projection generation timestamp must be UTC")
    if not re.fullmatch(r"[0-9a-f]{40}", str(generation.get("infrastructure_source_sha", ""))):
        raise ProjectionError("projection infrastructure source SHA is invalid")
    sources = generation.get("sources")
    if not isinstance(sources, list) or len(sources) != 3:
        raise ProjectionError("projection canonical source provenance is incomplete")
    if any(
        not isinstance(source, dict)
        or not source.get("path")
        or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("git_blob_sha", "")))
        for source in sources
    ):
        raise ProjectionError("projection source provenance is invalid")
    payload = projection.get("canonical_payload")
    if not isinstance(payload, dict):
        raise ProjectionError("canonical projection payload is missing")
    expected = str(generation.get("canonical_projection_sha256", ""))
    if projection_sha256(payload) != expected:
        raise ProjectionError("generated projection payload digest mismatch")
    return projection


def get_contract(projection: dict[str, Any], contract_name: str) -> dict[str, Any]:
    payload = projection["canonical_payload"]
    contract = payload.get("contract")
    if not isinstance(contract, dict) or contract.get("name") != contract_name:
        raise ProjectionError(f"unknown projected runner contract: {contract_name}")
    repository = payload.get("repository")
    names: set[str] = set()
    keys: set[str] = set()
    for runner in contract.get("eligible_runners", []):
        if runner.get("repository") != repository:
            raise ProjectionError("projected runner repository ownership mismatch")
        name = str(runner.get("runner_name") or "")
        key = str(runner.get("inventory_key") or "")
        if not name or name in names:
            raise ProjectionError("projected runner identity is empty or duplicated")
        if not key or key in keys:
            raise ProjectionError("projected inventory key is empty or duplicated")
        if not set(contract.get("selector", [])).issubset(runner.get("labels", [])):
            raise ProjectionError("projected runner does not satisfy contract selector")
        names.add(name)
        keys.add(key)
    return contract


def validate_projection_integrity(
    projection: dict[str, Any], expected_projection_sha256: str | None
) -> dict[str, Any]:
    actual = projection["generation"]["canonical_projection_sha256"]
    if not expected_projection_sha256:
        raise ProjectionError("expected immutable projection digest is required")
    if expected_projection_sha256 != actual:
        raise ProjectionError(
            f"immutable projection digest mismatch: expected={expected_projection_sha256} observed={actual}"
        )
    return projection["canonical_payload"]


def validate_control_plane_projection(
    projection: dict[str, Any], control_plane_path: Path | None
) -> None:
    if control_plane_path is None:
        raise ProjectionError("trusted control-plane projection is required")
    control_plane_projection = load_projection(control_plane_path)
    if control_plane_projection != projection:
        raise ProjectionError("product projection differs from trusted control-plane projection")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="site-auditor-stage")
    parser.add_argument("--list-source", choices=("persistent", "burst"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--control-plane-projection",
        type=Path,
        default=(Path(os.environ["AIMETON_CONTROL_PLANE_PROJECTION_PATH"])
                 if os.environ.get("AIMETON_CONTROL_PLANE_PROJECTION_PATH") else None),
    )
    args = parser.parse_args()
    projection = load_projection()
    integrity_verified = False
    if not args.offline:
        validate_projection_integrity(
            projection, os.environ.get("AIMETON_EXPECTED_PROJECTION_SHA256")
        )
        validate_control_plane_projection(projection, args.control_plane_projection)
        integrity_verified = True
    contract = get_contract(projection, args.contract)
    if args.list_source:
        output: Any = [
            runner["runner_name"]
            for runner in contract["eligible_runners"]
            if runner["source"] == args.list_source
        ]
    else:
        output = {
            "contract": args.contract,
            "projection_sha256": projection["generation"]["canonical_projection_sha256"],
            "projection_integrity_verified": integrity_verified,
            "canonical_projection_match_verified": not args.offline,
            "canonical_freshness_authority": "trusted server projection branch",
            "eligible_runners": contract["eligible_runners"],
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProjectionError as exc:
        raise SystemExit(f"runner projection validation failed: {exc}")
