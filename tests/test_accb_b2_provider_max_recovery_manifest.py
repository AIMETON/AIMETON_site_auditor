import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "research" / "ACCB_B2_PROVIDER_MAX_RECOVERY_MANIFEST_v0.1.json"


def test_provider_max_recovery_manifest_is_exact_source_run_subset() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["schema_version"] == "0.1"
    assert data["source_run_id"] == 34196764456
    assert data["source_site_auditor_sha"] == "16fa5b73d8deb8ad3ab6a795a50018117e02c097"
    assert data["source_evidence_comment_id"] == 5583590858
    assert data["provider_max_policy_sha"] == "f66e0aac76848879c212365b9b040f5ad5b7d605"
    assert data["excluded_scored_cells"] == 21
    assert data["retries"] == 0
    assert data["fallbacks"] is False
    assert data["compute_policy"] == "selected_endpoint_advertised_maximum"

    cells = {(row["model"], row["tier_id"]) for row in data["cells"]}
    assert cells == {
        ("z-ai/glm-5.2", "b2-562k"),
        ("z-ai/glm-5.2", "b2-2191k"),
        ("moonshotai/kimi-k3", "b2-140k"),
        ("moonshotai/kimi-k3", "b2-562k"),
    }
    assert len(data["cells"]) == 4
    assert all(row["reason"] for row in data["cells"])
