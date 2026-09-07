from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

OWNER = "Dimar4713"

ROUTES: dict[str, tuple[int, str, dict[str, str]]] = {
    "deploy-stage": (337, "deploy-stage.yml", {"commit_sha": "{sha}"}),
    "validate-baseline-self-hosted": (767, "baseline-self-hosted-dispatch.yml", {"expected_sha": "{sha}", "evidence_issue": "767"}),
    "census-accb-routerai-readonly": (816, "accb-routerai-readonly-census.yml", {"expected_sha": "{sha}", "evidence_issue": "816"}),
    "preflight-accb-openrouter-proxy": (820, "accb-openrouter-proxy-preflight.yml", {"expected_sha": "{sha}", "evidence_issue": "820"}),
    "preflight-accb-openrouter-large-body": (820, "accb-openrouter-proxy-large-body-preflight.yml", {"expected_sha": "{sha}", "evidence_issue": "820"}),
    "probe-accb-openrouter-sol": (820, "accb-openrouter-sol-microprobe.yml", {"expected_sha": "{sha}", "evidence_issue": "820", "owner_spend_authorized": "true", "max_budget_rub": "1"}),
    "run-accb-openrouter-sol-32k": (820, "accb-openrouter-sol-32k-reference.yml", {"expected_sha": "{sha}", "evidence_issue": "820", "owner_spend_authorized": "true", "max_budget_rub": "100"}),
    "run-accb-openrouter-sol-triad": (820, "accb-openrouter-sol-32k-provider-triad.yml", {"expected_sha": "{sha}", "evidence_issue": "820", "owner_spend_authorized": "true", "max_budget_rub": "300"}),
    "accept-admin-trace-stage": (293, "accept-admin-trace-stage.yml", {"expected_sha": "{sha}"}),
    "accept-aimeton-self-audit-stage": (293, "accept-aimeton-self-audit-stage.yml", {"expected_sha": "{sha}"}),
    "audit-competitor-services-realty-stage": (293, "audit-competitor-services-realty-stage.yml", {"expected_sha": "{sha}"}),
    "audit-selectel-stage": (293, "audit-competitor-services-realty-stage.yml", {"expected_sha": "{sha}", "target_url": "https://selectel.ru/services/cloud/vps-vds/", "audit_label": "Selectel VPS/VDS"}),
    "accept-routerai-synthesis-stage": (700, "accept-routerai-synthesis-stage.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true", "owner_spend_authorized": "true"}),
    "accept-checkpoint-stage": (88, "accept-checkpoint-stage.yml", {"expected_sha": "{sha}"}),
    "accept-mobile-ui-stage": (223, "accept-mobile-ui-stage.yml", {"expected_sha": "{sha}"}),
    "accept-service-catalog-stage": (274, "accept-service-catalog-stage.yml", {"expected_sha": "{sha}"}),
    "accept-hunter-runtime-stage": (501, "accept-hunter-runtime-stage.yml", {"expected_sha": "{sha}"}),
    "accept-logging-pressure-stage": (293, "accept-logging-pressure-stage.yml", {"expected_sha": "{sha}"}),
    "accept-live-analysis-stage": (270, "accept-live-analysis-stage.yml", {"expected_sha": "{sha}"}),
    "accept-interface-audit-stage": (291, "interface-audit-stage-acceptance.yml", {"expected_sha": "{sha}"}),
    "accept-ui-stage": (262, "ui-stage-visual-audit.yml", {"expected_sha": "{sha}"}),
    "accept-user-workspace-stage": (192, "stage-user-workspace-acceptance.yml", {"expected_sha": "{sha}"}),
    "accept-admin-workspace-stage": (197, "stage-admin-workspace-acceptance.yml", {"expected_sha": "{sha}"}),
    "accept-mission-stage-v2": (177, "stage-mission-ownership-acceptance-v2.yml", {"expected_sha": "{sha}"}),
    "accept-integrated-stage-continuity": (203, "stage-integrated-continuity.yml", {"expected_sha": "{sha}"}),
    "accept-integrated-stage-core": (203, "stage-integrated-acceptance.yml", {"expected_sha": "{sha}"}),
    "audit-server-architecture": (36, "server-architecture-audit.yml", {"expected_sha": "{sha}"}),
    "accept-integrated-stage-companies-bootstrap": (203, "stage-real-company-bootstrap-acceptance.yml", {"expected_sha": "{sha}"}),
    "audit-auth-persistence": (159, "server-auth-persistence-audit.yml", {"expected_sha": "{sha}"}),
    "diagnose-mission-stage": (177, "stage-mission-diagnostics.yml", {"expected_sha": "{sha}"}),
    "reconcile-stage-data-mount": (177, "stage-data-mount-reconcile.yml", {"expected_sha": "{sha}"}),
    "repair-admin-stage": (164, "repair-stage-admin.yml", {"expected_sha": "{sha}"}),
    "accept-auth-stage": (164, "stage-auth-acceptance.yml", {"expected_sha": "{sha}"}),
    "benchmark-searxng-concurrency-stage": (441, "benchmark-searxng-concurrency-stage.yml", {"expected_sha": "{sha}", "allow_live_calls": "true"}),
    "accept-hunter-real-e2e-stage": (441, "accept-hunter-real-e2e-stage.yml", {"expected_sha": "{sha}", "region": "Красноярск", "industry": "Стоматология", "expected_providers": "searxng,yandex", "minimum_returned": "10", "minimum_direct_returned": "10", "allow_paid_calls": "true"}),
    "benchmark-search-observer-shadow-stage": (544, "benchmark-search-observer-shadow-stage.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true"}),
    "benchmark-search-observer-model-arena-stage": (544, "search-observer-model-arena-stage.yml", {"expected_sha": "{sha}", "replay_run_id": "31547223530", "replay_sha": "b07d81330b8889dd9fbf5122e7c4b38bc444221d", "profiles": "", "allow_paid_llm_calls": "true"}),
    "validate-search-observer-second-wave-zero-cost": (573, "validate-search-observer-second-wave-zero-cost.yml", {"expected_sha": "{sha}"}),
    "accept-search-observer-steering-disabled-stage": (753, "accept-search-observer-steering-disabled-stage.yml", {"expected_sha": "{sha}"}),
    "validate-search-observer-second-wave-live-stage": (578, "validate-search-observer-second-wave-live-stage.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true", "owner_spend_authorized": "true", "max_budget_rub": "100"}),
    "validate-search-observer-heterogeneous-batch-stage": (589, "validate-search-observer-heterogeneous-batch-stage.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true", "owner_spend_authorized": "true", "max_batch_budget_rub": "1"}),
    "inventory-search-gap-trace-stage": (632, "search-gap-trace-inventory-stage.yml", {"expected_sha": "{sha}"}),
    "probe-verifier-routerai-stage": (783, "verifier-routerai-capability-probe.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true", "owner_spend_authorized": "true", "max_budget_rub": "100", "evidence_issue": "783", "profile": "routerai-gpt4o-mini"}),
    "probe-verifier-qwen35-stage": (783, "verifier-routerai-capability-probe.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true", "owner_spend_authorized": "true", "max_budget_rub": "100", "evidence_issue": "783", "profile": "routerai-qwen35-9b"}),
    "calibrate-verifier-golden5-stage": (783, "verifier-golden5-live-calibration.yml", {"expected_sha": "{sha}", "allow_paid_calls": "true", "owner_spend_authorized": "true", "max_budget_rub": "100", "evidence_issue": "783", "model": "openai/gpt-4o-mini"}),
    "accept-burst-parallel-stage": (783, "burst-runner-parallel-acceptance.yml", {"expected_sha": "{sha}", "evidence_issue": "783"}),
}


def api(method: str, path: str, *, payload: dict | None = None) -> tuple[int, bytes]:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    url = f"https://api.github.com/repos/{repo}/{path.lstrip('/')}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"GitHub API {method} {path} failed: {exc.code}: {detail}") from exc


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    actor = ((event.get("comment") or {}).get("user") or {}).get("login")
    body = str((event.get("comment") or {}).get("body") or "").strip()
    issue_number = int((event.get("issue") or {}).get("number") or 0)

    if actor != OWNER or not body.startswith("/"):
        print("ignored: unauthorized_or_non_command")
        return 0

    match = re.fullmatch(r"/([a-z0-9-]+)\s+([0-9a-f]{40})", body)
    if not match or match.group(1) not in ROUTES:
        print("ignored: unsupported_or_invalid_command")
        return 0

    command, sha = match.groups()
    authorized_issue, workflow_id, template_inputs = ROUTES[command]
    if issue_number != authorized_issue:
        raise RuntimeError(f"Command {command} is not authorised on issue {issue_number}")

    api("GET", f"commits/{sha}")
    inputs = {key: value.replace("{sha}", sha) for key, value in template_inputs.items()}
    status, _ = api(
        "POST",
        f"actions/workflows/{workflow_id}/dispatches",
        payload={"ref": "main", "inputs": inputs},
    )
    if status not in {200, 204}:
        raise RuntimeError(f"Unexpected dispatch status: {status}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## AIMETON command dispatch ledger\n\n")
            handle.write(f"- command: `{command}`\n")
            handle.write(f"- target workflow: `{workflow_id}`\n")
            handle.write(f"- issue: `{issue_number}`\n")
            handle.write(f"- actor: `{actor}`\n")
            handle.write(f"- exact SHA: `{sha}`\n")
            handle.write("- marketplace actions: `disabled`\n")
            handle.write("- result: `dispatched`\n")
    print(f"Dispatched {workflow_id} for exact SHA {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())