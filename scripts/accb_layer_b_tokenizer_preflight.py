#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Callable

from huggingface_hub import HfApi
from transformers import AutoTokenizer
import tiktoken

import accb_layer_b_payload as payload

MODEL_SPECS: dict[str, dict[str, str]] = {
    "z-ai/glm-5.2": {"kind": "hf", "source": "zai-org/GLM-5.2"},
    "deepseek/deepseek-v4-pro-0813": {
        "kind": "hf",
        "source": "deepseek-ai/DeepSeek-V4-Pro-0813",
    },
    "qwen/qwen3.7-plus": {"kind": "tiktoken", "source": "o200k_base"},
    "moonshotai/kimi-k3": {"kind": "hf", "source": "moonshotai/Kimi-K3"},
    "openai/gpt-5.6-sol": {"kind": "tiktoken", "source": "o200k_base"},
}


class PreflightError(RuntimeError):
    pass


def package_version(name: str) -> str:
    return importlib.metadata.version(name)


def hf_counter(repo_id: str) -> tuple[Callable[[str], int], dict[str, Any]]:
    api = HfApi()
    info = api.model_info(repo_id, revision="main")
    revision = str(info.sha or "").strip()
    if len(revision) != 40:
        raise PreflightError(f"unable to resolve exact Hugging Face revision for {repo_id}")
    tokenizer = AutoTokenizer.from_pretrained(
        repo_id,
        revision=revision,
        trust_remote_code=False,
        use_fast=True,
    )

    def count(text: str) -> int:
        ids = tokenizer.encode(text, add_special_tokens=False)
        return len(ids)

    return count, {
        "kind": "huggingface-autotokenizer",
        "repository": repo_id,
        "revision": revision,
        "tokenizer_class": type(tokenizer).__name__,
        "transformers_version": package_version("transformers"),
        "huggingface_hub_version": package_version("huggingface-hub"),
        "trust_remote_code": False,
        "count_scope": "system_text + two_newlines + user_text; add_special_tokens=false",
    }


def tiktoken_counter(encoding_name: str) -> tuple[Callable[[str], int], dict[str, Any]]:
    encoding = tiktoken.get_encoding(encoding_name)

    def count(text: str) -> int:
        return len(encoding.encode(text))

    return count, {
        "kind": "tiktoken-base-encoding",
        "encoding": encoding_name,
        "tiktoken_version": package_version("tiktoken"),
        "count_scope": "system_text + two_newlines + user_text",
    }


def build_report(architecture_root: Path) -> dict[str, Any]:
    frozen = payload.load_frozen_artifacts(architecture_root)
    report: dict[str, Any] = {
        "schema_version": "0.1",
        "status": "ACCB_LAYER_B_TOKENIZER_PREFLIGHT_FAILED",
        "architecture_source_sha": payload.FROZEN_ARCHITECTURE_SHA,
        "payload_version": payload.PAYLOAD_VERSION,
        "filler_corpus_version": "accb-layer-b-synthetic-filler-v0.1",
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
        "provider_api_secrets_used": False,
        "network_calls": "Hugging Face tokenizer metadata/files and PyPI-installed libraries only; no model provider API",
        "models": [],
    }

    for model, spec in MODEL_SPECS.items():
        kind = spec["kind"]
        if kind == "hf":
            count_tokens, identity = hf_counter(spec["source"])
        elif kind == "tiktoken":
            count_tokens, identity = tiktoken_counter(spec["source"])
        else:
            raise PreflightError(f"unsupported tokenizer kind for {model}: {kind}")

        model_row: dict[str, Any] = {
            "model": model,
            "tokenizer_identity": identity,
            "anchors": [],
        }
        for anchor in payload.FROZEN_ANCHORS:
            fitted = payload.fit_payload_to_local_anchor(
                frozen.scenario,
                frozen.trace_schema,
                nominal_anchor=anchor,
                count_tokens=count_tokens,
                count_scope=str(identity["count_scope"]),
            )
            manifest = payload.sanitized_manifest(fitted)
            if not 0 <= anchor - int(manifest["L_payload_local"]) <= payload.LOCAL_FIT_TOLERANCE:
                raise PreflightError(f"local fit outside tolerance for {model} at {anchor}")
            model_row["anchors"].append(manifest)
        report["models"].append(model_row)

    expected = set(MODEL_SPECS)
    observed = {row["model"] for row in report["models"]}
    if observed != expected:
        raise PreflightError(f"model matrix mismatch: {observed} != {expected}")
    if any(len(row["anchors"]) != 3 for row in report["models"]):
        raise PreflightError("not every model has three fitted anchors")
    report["planned_scored_cells"] = 15
    report["fitted_cells"] = 15
    report["status"] = "ACCB_LAYER_B_TOKENIZER_PREFLIGHT_COMPLETE"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.architecture_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "fitted_cells": report.get("fitted_cells"),
                "provider_generation_requests": 0,
                "paid_spend_authorized_rub": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
