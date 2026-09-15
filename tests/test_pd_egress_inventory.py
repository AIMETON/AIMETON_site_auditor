from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "docs" / "compliance" / "pd-egress-inventory.json"


def _inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _https_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value.startswith("https://"):
                values.add(value.rstrip("/"))
    return values


def test_pd_egress_inventory_has_unique_ids_and_required_metadata() -> None:
    inventory = _inventory()
    assert inventory["schema_version"] == 1
    destinations = inventory["destinations"]
    ids = [item["id"] for item in destinations]
    assert len(ids) == len(set(ids))

    for item in destinations:
        assert item["kind"]
        assert item["payload_classes"]
        assert isinstance(item["personal_data_possible"], bool)
        assert item["review_state"]
        assert item["service_operator_country"]
        assert item["downstream_country"]
        assert item["notes"]
        if item["kind"].startswith("static_literal"):
            assert isinstance(item["endpoint"], str)
            assert item["endpoint"].startswith("https://")
        if item["kind"] == "env_base_url":
            assert item["endpoint"] is None
            assert item["env_var"]


def test_all_https_literals_in_audited_egress_sources_are_inventory_registered() -> None:
    inventory = _inventory()
    observed: set[str] = set()
    for relative_path in inventory["audited_source_files"]:
        path = ROOT / relative_path
        assert path.is_file(), f"audited egress source is missing: {relative_path}"
        observed.update(_https_literals(path))

    registered = {
        item["endpoint"].rstrip("/")
        for item in inventory["destinations"]
        if isinstance(item.get("endpoint"), str)
    }

    assert observed == registered, (
        "Audited source files and pd-egress-inventory.json disagree. "
        f"Unregistered literals: {sorted(observed - registered)}; "
        f"stale registry entries: {sorted(registered - observed)}"
    )


def test_dynamic_searxng_egress_is_explicitly_tracked() -> None:
    inventory = _inventory()
    searxng = next(item for item in inventory["destinations"] if item["id"] == "searxng")
    assert searxng["kind"] == "env_base_url"
    assert searxng["env_var"] == "SEARXNG_BASE_URL"
    assert searxng["review_state"] == "runtime_evidence_required"
    assert searxng["personal_data_possible"] is True
