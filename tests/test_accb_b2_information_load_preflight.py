from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import accb_b2_information_load_preflight as preflight


def test_b2_materialization_gate_is_exact_semantic_and_zero_spend() -> None:
    report = preflight.build_report()
    assert report["status"] == "ACCB_B2_INFORMATION_LOAD_READY"
    assert report["planned_cells"] == 25
    assert report["network_calls_performed"] == 0
    assert report["provider_generation_requests"] == 0
    assert report["paid_spend_authorized_rub"] == 0
    assert report["provider_api_secrets_used"] is False
    assert report["tokenizer_required_at_execution"] is False
    assert report["legacy_global_8192_output_cap_allowed"] is False
    assert [x["request_text_bytes"] for x in report["tiers"]] == [
        32768, 65536, 143934, 575367, 2297725
    ]
    assert [x["tier_entity_count"] for x in report["tiers"]] == [16,24,32,48,64]
    assert all(x["semantic_evidence_ratio"] >= 0.98 for x in report["tiers"])
    assert all(x["terminal_padding_bytes"] <= 256 for x in report["tiers"])
    assert all(
        x["semantic_consequence"]["all_authoritative_events_consequential"]
        for x in report["tiers"]
    )


def test_b2_execution_path_contains_no_legacy_8192_generation_ceiling() -> None:
    candidates = [
        ROOT / "scripts" / "accb_b2_information_load_preflight.py",
        ROOT / "docs" / "research" / "accb_b2_snapshot" /
        preflight.ARCH_SHA / "generate_accb_b2_information_load.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in candidates)
    assert "MAX_OUTPUT_TOKENS = 8192" not in text
    assert '"max_tokens": 8192' not in text
