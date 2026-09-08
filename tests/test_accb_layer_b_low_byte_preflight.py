from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import accb_layer_b_low_byte_preflight as low


ARCH = Path(
    "docs/research/accb_layer_b_snapshot/"
    "b47b937873ef980601b5c741af9b327fb18365bc"
)


def test_low_byte_extension_materializes_exact_targets_without_provider_calls() -> None:
    report = low.build_report(ARCH)
    assert report["status"] == "ACCB_LAYER_B_LOW_BYTE_PREFLIGHT_READY"
    assert report["planned_cells"] == 10
    assert report["provider_generation_requests"] == 0
    assert report["paid_spend_authorized_rub"] == 0
    assert report["tokenizer_required_at_execution"] is False
    assert [row["request_text_bytes"] for row in report["tiers"]] == [32768, 65536]
    assert [row["tier_id"] for row in report["tiers"]] == ["low-32kib", "low-64kib"]


def test_low_byte_payloads_are_deterministic_and_preserve_fact_positions() -> None:
    first = low.build_report(ARCH)
    second = low.build_report(ARCH)
    assert first == second
    for row in first["tiers"]:
        assert len(row["payload_sha256"]) == 64
        assert len(row["context_sha256"]) == 64
        assert 0 <= row["padding_bytes"] <= low.MAX_PADDING_BYTES
        assert row["max_abs_critical_position_error"] <= 0.01
        assert row["measured_filler_distractor_density"] is not None


def test_low_byte_seed_namespace_does_not_collide_with_legacy_anchor_labels() -> None:
    report = low.build_report(ARCH)
    seed_anchors = {row["assembly_seed_anchor"] for row in report["tiers"]}
    assert seed_anchors == {1000032768, 1000065536}
    assert not seed_anchors.intersection({32768, 131072, 524288})
