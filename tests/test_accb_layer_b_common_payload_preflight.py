from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import accb_layer_b_common_payload_preflight as common

SCHEDULE = Path("docs/research/ACCB_LAYER_B_COMMON_PAYLOAD_SCHEDULE_v0.2.json")
WORKFLOW = Path(".github/workflows/accb-layer-b-common-payload-preflight.yml")


def test_common_payload_schedule_is_model_neutral_and_tokenizer_free() -> None:
    p = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    assert p["status"] == "FROZEN_EXECUTION_PAYLOAD_SCHEDULE"
    assert p["execution_admission_sha"] == "67c8ea3e84405884136119d7252fe7424ccf1631"
    assert p["tokenizer_required_at_execution"] is False
    assert p["provider_generation_requests_authorized_by_schedule"] == 0
    assert p["paid_spend_authorized_rub"] == 0
    assert [x["nominal_anchor"] for x in p["anchors"]] == [32768, 131072, 524288]
    assert [x["logical_context_tokens"] for x in p["anchors"]] == [9835, 39893, 160004]
    assert max(x["expected_request_text_bytes"] for x in p["anchors"]) < 2_500_000


def test_common_payload_preflight_has_no_tokenizer_or_provider_dependency() -> None:
    script = Path("scripts/accb_layer_b_common_payload_preflight.py").read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "transformers" not in script
    assert "AutoTokenizer" not in script
    assert "tiktoken" not in script
    assert "ROUTERAI_API_KEY" not in workflow
    assert "OPENROUTER_API_KEY" not in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "tokenizer_required_at_execution" in workflow
    assert "provider_generation_requests" in workflow
    assert "paid_spend_authorized_rub" in workflow


def test_common_payload_materialization_matches_frozen_schedule() -> None:
    architecture_root = Path(
        "docs/research/accb_layer_b_snapshot/"
        "b47b937873ef980601b5c741af9b327fb18365bc"
    )
    report = common.build_report(architecture_root)
    assert report["status"] == "ACCB_LAYER_B_COMMON_PAYLOAD_READY"
    assert report["same_payload_per_anchor_for_all_models"] is True
    assert report["tokenizer_required_at_execution"] is False
    assert report["provider_generation_requests"] == 0
    assert report["paid_spend_authorized_rub"] == 0
    assert [x["request_text_bytes"] for x in report["anchors"]] == [143934, 575367, 2297725]
    assert all(x["L_payload_local_estimate"] is None for x in report["anchors"])
    assert all(x["L_model_input_provider"] is None for x in report["anchors"])


def test_corrected_output_contract_exposes_exact_scenario_identity_and_payload_hashes() -> None:
    import hashlib

    architecture_root = Path(
        "docs/research/accb_layer_b_snapshot/"
        "b47b937873ef980601b5c741af9b327fb18365bc"
    )
    frozen = common.payload.load_frozen_artifacts(architecture_root)
    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    observed = []
    for spec in schedule["anchors"]:
        system_text, user_text, _, _, _ = common.payload.build_payload(
            frozen.scenario,
            frozen.trace_schema,
            nominal_anchor=int(spec["nominal_anchor"]),
            logical_context_tokens=int(spec["logical_context_tokens"]),
        )
        request_text = common.payload.local_count_text(system_text, user_text)
        assert 'SCENARIO_ID="ACCB-DEV-004"' in request_text
        observed.append(
            (
                int(spec["nominal_anchor"]),
                len(request_text.encode("utf-8")),
                hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
            )
        )

    assert observed == [
        (32768, 143934, "e508100bf400d4248a97a7c4aa8a64d7e22db14d2325f98508ae60ab1218c32e"),
        (131072, 575367, "eb3bf2c81c12320ce992fd8aba0028e891673f7054d3bd2286ad58a486f811cc"),
        (524288, 2297725, "048bc9e5d99f29498bac8b15314ac08eb0c18bcc326a7cf78a8c710053e82e38"),
    ]
