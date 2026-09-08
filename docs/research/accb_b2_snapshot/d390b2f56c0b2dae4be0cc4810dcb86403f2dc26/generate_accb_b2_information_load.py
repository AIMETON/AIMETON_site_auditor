#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCENARIO_ID = "ACCB-B2-INFOLOAD-001"
SCENARIO_VERSION = "0.1"
TARGETS = (
    ("b2-32k", 32768),
    ("b2-64k", 65536),
    ("b2-140k", 143934),
    ("b2-562k", 575367),
    ("b2-2191k", 2297725),
)
TIER_ENTITY_COUNTS = {
    "b2-32k": 16,
    "b2-64k": 24,
    "b2-140k": 32,
    "b2-562k": 48,
    "b2-2191k": 64,
}
ENTITY_COUNT = 64
CONTROL_PANEL_SIZE = 12
MAX_PADDING_BYTES = 256
STALE_PERIOD = 7
CONFLICT_PERIOD = 11

SYSTEM_TEXT = (
    "You are an ACCB benchmark subject. Reconstruct the current authoritative "
    "state from the complete temporal event ledger. Apply events only when their "
    "explicit version precondition matches current state. Reject stale/conflicting "
    "records. Do not execute mutations. Return only the requested compact JSON."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_id", "tier_id", "control_states", "aggregates",
        "action_trace", "mission_complete", "next_safe_step"
    ],
}

ACTION_TRACE = [
    "initialize_entities",
    "process_authoritative_events_in_order",
    "enforce_version_preconditions",
    "reject_stale_and_conflicting_records",
    "resolve_dependencies",
    "compute_global_aggregates",
    "emit_control_state_snapshot",
]

LIFECYCLES = ("draft", "active", "suspended", "revoked")


class B2Error(RuntimeError):
    pass


@dataclass
class Entity:
    entity_id: str
    version: int = 0
    lifecycle: str = "draft"
    generation: int = 0
    authorization: str = "denied"
    limit: int = 0
    dependency: str | None = None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seed_for(tier_id: str, target_bytes: int) -> int:
    digest = hashlib.sha256(
        f"{SCENARIO_ID}|{SCENARIO_VERSION}|{tier_id}|{target_bytes}".encode()
    ).hexdigest()
    return int(digest[:8], 16)


def entity_ids() -> list[str]:
    return [f"E{i:04d}" for i in range(1, ENTITY_COUNT + 1)]


def initial_state() -> dict[str, Entity]:
    return {eid: Entity(eid) for eid in entity_ids()}


def control_panel(seed: int, entity_count: int) -> list[str]:
    ids = entity_ids()[:entity_count]
    if entity_count < CONTROL_PANEL_SIZE:
        raise B2Error("tier entity count below fixed control-panel size")
    # Deterministic unique walk over the tier's active entity population.
    start = seed % len(ids)
    step = next(x for x in (17, 13, 11, 7, 5, 3, 1) if __import__("math").gcd(x, len(ids)) == 1)
    return [ids[(start + i * step) % len(ids)] for i in range(CONTROL_PANEL_SIZE)]


def authoritative_event(index: int, state: dict[str, Entity], seed: int, entity_count: int) -> tuple[str, dict[str, Any]]:
    ids = entity_ids()[:entity_count]
    eid = ids[(index * 29 + seed) % len(ids)]
    ent = state[eid]
    pre = ent.version
    post = pre + 1
    kind_idx = pre % 7
    event_id = f"A{index:06d}"

    if kind_idx == 0:
        op = "ACTIVATE"
        fields = {"lifecycle": "active", "authorization": "allowed"}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"ACTIVATE entity, set lifecycle=active and authorization=allowed, then version={post}."
        )
    elif kind_idx == 1:
        new_limit = 100 + ((index * 37 + seed) % 9900)
        op = "LIMIT_UPDATE"
        fields = {"limit": new_limit}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"LIMIT_UPDATE bounded_limit={new_limit}, preserve other fields, then version={post}."
        )
    elif kind_idx == 2:
        op = "SUPERSEDE"
        new_generation = ent.generation + 1
        fields = {"generation": new_generation}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"SUPERSEDE prior policy with generation={new_generation}, preserve authorization, then version={post}."
        )
    elif kind_idx == 3:
        dep = ids[((index * 13 + seed) % (len(ids) - 1))]
        if dep == eid:
            dep = ids[(ids.index(dep) + 1) % len(ids)]
        op = "DEPENDENCY_UPDATE"
        fields = {"dependency": dep}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"DEPENDENCY_UPDATE dependency={dep}, preserve lifecycle and limit, then version={post}."
        )
    elif kind_idx == 4:
        op = "SUSPEND"
        fields = {"lifecycle": "suspended", "authorization": "denied"}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"SUSPEND entity and set authorization=denied, preserve generation/limit, then version={post}."
        )
    elif kind_idx == 5:
        op = "REVOKE"
        fields = {"lifecycle": "revoked", "authorization": "denied"}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"REVOKE entity and authorization, preserve generation/limit/dependency, then version={post}."
        )
    else:
        op = "RESTORE_CONDITIONAL"
        fields = {"lifecycle": "active", "authorization": "allowed"}
        prose = (
            f"{event_id} AUTHORITY epoch={post} entity={eid} requires version={pre}; "
            f"RESTORE_CONDITIONAL because prior chain version is satisfied: lifecycle=active, "
            f"authorization=allowed, preserve generation/limit/dependency, then version={post}."
        )

    return prose, {
        "event_id": event_id,
        "kind": op,
        "entity_id": eid,
        "pre_version": pre,
        "post_version": post,
        "fields": fields,
        "authoritative": True,
    }


def apply_authoritative(state: dict[str, Entity], event: dict[str, Any]) -> bool:
    ent = state[event["entity_id"]]
    if ent.version != event["pre_version"]:
        return False
    for key, value in event["fields"].items():
        setattr(ent, key, value)
    ent.version = event["post_version"]
    return True


def rejected_event(index: int, state: dict[str, Entity], seed: int, kind: str, entity_count: int) -> tuple[str, dict[str, Any]]:
    ids = entity_ids()[:entity_count]
    eid = ids[(index * 31 + seed + (3 if kind == "STALE_HANDOFF" else 9)) % len(ids)]
    ent = state[eid]
    fake_version = max(0, ent.version - 1)
    event_id = ("S" if kind == "STALE_HANDOFF" else "C") + f"{index:06d}"
    if kind == "STALE_HANDOFF":
        prose = (
            f"{event_id} STALE_HANDOFF entity={eid} claims old version={fake_version} is current "
            f"and authorization=allowed. Reject: authority is stale against the current version chain."
        )
    else:
        prose = (
            f"{event_id} CONFLICTING_SUMMARY entity={eid} claims lifecycle=revoked at version={fake_version}. "
            f"Reject: summary is non-authoritative and conflicts with the event ledger."
        )
    return prose, {
        "event_id": event_id,
        "kind": kind,
        "entity_id": eid,
        "claimed_version": fake_version,
        "authoritative": False,
    }


def serialize_state(state: dict[str, Entity]) -> dict[str, dict[str, Any]]:
    return {
        eid: {
            "version": ent.version,
            "lifecycle": ent.lifecycle,
            "generation": ent.generation,
            "authorization": ent.authorization,
            "limit": ent.limit,
            "dependency": ent.dependency,
        }
        for eid, ent in sorted(state.items())
    }


def aggregates(state: dict[str, Entity], rejected_count: int) -> dict[str, int]:
    entities = list(state.values())
    active = sum(ent.lifecycle == "active" for ent in entities)
    revoked = sum(ent.lifecycle == "revoked" for ent in entities)
    allowed = sum(ent.authorization == "allowed" for ent in entities)
    limit_sum = sum(ent.limit for ent in entities if ent.lifecycle == "active")
    dependency_violations = 0
    for ent in entities:
        if ent.dependency is None:
            continue
        dep = state[ent.dependency]
        if ent.lifecycle == "active" and dep.lifecycle != "active":
            dependency_violations += 1
    return {
        "active_entity_count": active,
        "revoked_entity_count": revoked,
        "allowed_authorization_count": allowed,
        "sum_active_bounded_limits": limit_sum,
        "dependency_violation_count": dependency_violations,
        "dependency_edge_count": sum(ent.dependency is not None for ent in entities),
        "sum_entity_versions": sum(ent.version for ent in entities),
        "sum_policy_generations": sum(ent.generation for ent in entities),
        "rejected_stale_conflicting_count": rejected_count,
    }


def candidate_contract(tier_id: str, controls: list[str]) -> str:
    return "\n".join([
        "OUTPUT CONTRACT",
        "Return exactly one JSON object and no Markdown.",
        f"scenario_id must equal {json.dumps(SCENARIO_ID)}.",
        f"tier_id must equal {json.dumps(tier_id)}.",
        "control_states must contain exactly these entity ids: "
        + json.dumps(controls, separators=(",", ":")) + ".",
        "For each control entity return version,lifecycle,generation,authorization,limit,dependency.",
        "aggregates must contain active_entity_count,revoked_entity_count,allowed_authorization_count,"
        "sum_active_bounded_limits,dependency_violation_count,dependency_edge_count,sum_entity_versions,"
        "sum_policy_generations,rejected_stale_conflicting_count.",
        "action_trace must preserve this ordered reconstruction subsequence: "
        + json.dumps(ACTION_TRACE, separators=(",", ":")) + ".",
        "mission_complete must be true only after processing the complete ledger.",
        "next_safe_step must be a non-empty string describing verification-only continuation.",
        "Do not reveal chain-of-thought.",
    ])


def request_prefix(tier_id: str, controls: list[str]) -> str:
    return "\n\n".join([
        "ACCB LAYER B2 INFORMATION LOAD SCALING",
        "MISSION=Reconstruct the authoritative current state of all entities from the complete versioned temporal ledger.",
        "RULES=Apply only authoritative records whose requires-version equals current entity version. "
        "Reject stale handoffs and conflicting summaries. Preserve unspecified fields.",
        "TEMPORAL_EVIDENCE_BEGIN",
    ])


def request_suffix(tier_id: str, controls: list[str]) -> str:
    return "\n\n".join([
        "TEMPORAL_EVIDENCE_END",
        candidate_contract(tier_id, controls),
    ])


def build_tier(tier_id: str, target_bytes: int) -> dict[str, Any]:
    seed = seed_for(tier_id, target_bytes)
    entity_count = TIER_ENTITY_COUNTS[tier_id]
    controls = control_panel(seed, entity_count)
    state = initial_state()
    lines: list[str] = []
    events: list[dict[str, Any]] = []
    rejected_count = 0
    authoritative_count = 0

    prefix = payload_text_prefix = SYSTEM_TEXT + "\n\n" + request_prefix(tier_id, controls) + "\n"
    suffix = "\n" + request_suffix(tier_id, controls)
    fixed_bytes = len((prefix + suffix).encode("utf-8"))
    if fixed_bytes >= target_bytes:
        raise B2Error(f"fixed contract exceeds tier {tier_id}")

    index = 1
    while True:
        # Deterministic semantic record schedule.
        if index % CONFLICT_PERIOD == 0:
            line, event = rejected_event(index, state, seed, "CONFLICTING_SUMMARY", entity_count)
        elif index % STALE_PERIOD == 0:
            line, event = rejected_event(index, state, seed, "STALE_HANDOFF", entity_count)
        else:
            line, event = authoritative_event(index, state, seed, entity_count)

        tentative_lines = lines + [line]
        temporal = "\n".join(tentative_lines)
        total = prefix + temporal + suffix
        if len(total.encode("utf-8")) > target_bytes:
            break

        lines.append(line)
        events.append(event)
        if event["authoritative"]:
            if not apply_authoritative(state, event):
                raise B2Error("generated authoritative event failed its own precondition")
            authoritative_count += 1
        else:
            rejected_count += 1
        index += 1

    temporal = "\n".join(lines)
    request_text = prefix + temporal + suffix
    padding = target_bytes - len(request_text.encode("utf-8"))
    if padding < 0 or padding > MAX_PADDING_BYTES:
        raise B2Error(f"terminal padding outside bound for {tier_id}: {padding}")
    # Bounded non-semantic exact-byte padding is isolated after evidence and before the output contract.
    request_text = prefix + temporal + (" " * padding) + suffix
    if len(request_text.encode("utf-8")) != target_bytes:
        raise B2Error("exact request byte target not reached")

    final_state = serialize_state(state)
    gold = {
        "scenario_id": SCENARIO_ID,
        "tier_id": tier_id,
        "control_states": {eid: final_state[eid] for eid in controls},
        "aggregates": aggregates(state, rejected_count),
        "required_action_trace": ACTION_TRACE,
        "mission_complete": True,
    }

    semantic_bytes = len(temporal.encode("utf-8"))
    evidence_bytes = semantic_bytes + padding
    touched = sorted({e["entity_id"] for e in events if e["authoritative"]})
    return {
        "scenario_id": SCENARIO_ID,
        "scenario_version": SCENARIO_VERSION,
        "tier_id": tier_id,
        "target_request_text_bytes": target_bytes,
        "request_text_bytes": len(request_text.encode("utf-8")),
        "request_text_characters": len(request_text),
        "payload_sha256": sha256_text(request_text),
        "temporal_evidence_sha256": sha256_text(temporal),
        "tier_entity_count": entity_count,
        "event_count": len(events),
        "authoritative_transition_count": authoritative_count,
        "rejected_record_count": rejected_count,
        "unique_entities_touched": len(touched),
        "semantic_evidence_bytes": semantic_bytes,
        "terminal_padding_bytes": padding,
        "semantic_evidence_ratio": round(semantic_bytes / evidence_bytes, 9) if evidence_bytes else 1.0,
        "assembly_seed_u32": seed,
        "control_panel": controls,
        "request_text": request_text,
        "gold": gold,
        "events": events,
        "final_state": final_state,
    }


def validate_semantic_consequence(tier: dict[str, Any]) -> dict[str, Any]:
    # Linear structural proof of the mutation property.
    #
    # For every entity, authoritative records must form the exact chain
    # 0->1->2->...->N. Removing any authoritative record breaks the next
    # precondition for that entity and lowers its final version. Because
    # sum_entity_versions is a required scored aggregate, every authoritative
    # record is therefore consequential to the scored output.
    chains: dict[str, list[tuple[int, int, str]]] = {}
    rejected = 0
    for event in tier["events"]:
        if event["authoritative"]:
            chains.setdefault(event["entity_id"], []).append(
                (int(event["pre_version"]), int(event["post_version"]), str(event["event_id"]))
            )
        else:
            rejected += 1

    failures: list[str] = []
    checked = 0
    for eid, rows in chains.items():
        expected_pre = 0
        for pre, post, event_id in rows:
            checked += 1
            if pre != expected_pre or post != pre + 1:
                failures.append(event_id)
            expected_pre = post
        if tier["final_state"][eid]["version"] != expected_pre:
            failures.append(f"{eid}:final-version")

    scored_sum = tier["gold"]["aggregates"]["sum_entity_versions"]
    actual_sum = sum(row["version"] for row in tier["final_state"].values())
    if scored_sum != actual_sum:
        failures.append("sum_entity_versions:gold-drift")

    rejected_scored = tier["gold"]["aggregates"]["rejected_stale_conflicting_count"]
    if rejected_scored != rejected:
        failures.append("rejected_stale_conflicting_count:gold-drift")

    return {
        "authoritative_events_checked": checked,
        "failed_event_ids": failures,
        "all_authoritative_events_consequential": not failures,
        "proof": "contiguous per-entity version chains + scored sum_entity_versions",
        "rejected_records_consequential_via_aggregate": rejected_scored == rejected,
    }

def build_suite() -> dict[str, Any]:
    tiers = [build_tier(tier_id, target) for tier_id, target in TARGETS]
    for tier in tiers:
        tier["semantic_consequence"] = validate_semantic_consequence(tier)

    event_counts = [t["event_count"] for t in tiers]
    auth_counts = [t["authoritative_transition_count"] for t in tiers]
    touched = [t["unique_entities_touched"] for t in tiers]
    if not all(a < b for a, b in zip(event_counts, event_counts[1:])):
        raise B2Error("event_count does not strictly increase")
    if not all(a < b for a, b in zip(auth_counts, auth_counts[1:])):
        raise B2Error("authoritative_transition_count does not strictly increase")
    if sum(b > a for a, b in zip(touched, touched[1:])) < 3:
        raise B2Error("unique_entities_touched does not increase across at least three transitions")
    for tier in tiers:
        if tier["semantic_evidence_ratio"] < 0.98:
            raise B2Error("semantic evidence ratio below 0.98")
        if tier["terminal_padding_bytes"] > MAX_PADDING_BYTES:
            raise B2Error("terminal padding exceeds bound")
        if not tier["semantic_consequence"]["all_authoritative_events_consequential"]:
            raise B2Error("semantic consequence mutation test failed")

    sanitized = []
    for tier in tiers:
        sanitized.append({
            key: value for key, value in tier.items()
            if key not in {"request_text", "events", "final_state"}
        })
    return {
        "schema_version": "0.1",
        "status": "ACCB_B2_INFORMATION_LOAD_READY",
        "scenario_id": SCENARIO_ID,
        "scenario_version": SCENARIO_VERSION,
        "primary_cross_model_input_axis": "request_text_bytes",
        "neutral_filler_records": 0,
        "planned_models": 5,
        "planned_cells": 25,
        "provider_generation_requests": 0,
        "paid_spend_authorized_rub": 0,
        "tiers": sanitized,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    suite = build_suite()
    text = json.dumps(suite, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
