from __future__ import annotations

import hmac

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import User
from app.auth_api import CSRF_COOKIE, CSRF_HEADER, require_admin
from app.hunter_professional_provenance import (
    fingerprint_model_payload,
    summarize_gateway_policy,
)
from app.hunter_search_policy_authority import (
    HunterSearchPolicyAuthority,
    resolve_hunter_search_policy,
)
from app.search_gateway import SearchRequest, search_policy_from_env
from app.search_gateway.traced_policy import resolve_traced_gateway_policy
from app.search_observer_quality_policy import QualityFirstPromotionPolicy
from app.search_provider_lifecycle import observe_provider_lifecycle
from app.search_quality_policy_settings import (
    SearchQualityPolicyRecord,
    get_search_quality_policy_repository,
)
from app.search_strategy_settings import (
    SearchStrategySettings,
    SearchStrategySettingsRecord,
    get_search_strategy_settings_repository,
    strategy_catalog,
)


router = APIRouter(prefix="/api/admin/search-strategies", tags=["admin-search-strategies"])


class SearchStrategySettingsUpdate(BaseModel):
    settings: SearchStrategySettings
    reason: str = Field(min_length=1, max_length=500)


class SearchQualityPolicyUpdate(BaseModel):
    policy: QualityFirstPromotionPolicy
    reason: str = Field(min_length=1, max_length=500)


def _require_csrf(cookie_token: str | None, header_token: str | None) -> None:
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail={"reason": "csrf_failed"})


def _execution_policy_observation(record: SearchStrategySettingsRecord) -> dict:
    """Describe canonical and effective Hunter policies without executing search.

    Persisted ADMIN settings are resolved once at the canonical Hunter authority
    boundary. ``TracedSearchGateway`` is provenance-only, so its effective policy
    must be identical to the resolver-selected policy.
    """

    actual = search_policy_from_env()
    projected = record.settings.apply_search_policy(actual)
    actual_summary = summarize_gateway_policy(actual)
    projected_summary = summarize_gateway_policy(projected)
    runtime_resolution = resolve_hunter_search_policy(
        base_policy=actual,
        settings_record=record,
    )
    selected_summary = summarize_gateway_policy(runtime_resolution.policy)

    gateway_resolution = resolve_traced_gateway_policy(
        request=SearchRequest(
            query="policy-observation",
            limit=1,
            mission_id="hunt-policy-observation",
            correlation_id="policy-observation",
        ),
        incoming_policy=runtime_resolution.policy,
    )
    gateway_effective_summary = summarize_gateway_policy(gateway_resolution.effective_policy)

    admin_candidate_summary = None
    admin_candidate_fingerprint = None
    admin_candidate_matches_projection = None
    admin_candidate_available = record.updated_at is not None
    if admin_candidate_available:
        admin_candidate = resolve_hunter_search_policy(
            authority=HunterSearchPolicyAuthority.ADMIN,
            base_policy=actual,
            settings_record=record,
        )
        admin_candidate_summary = summarize_gateway_policy(admin_candidate.policy)
        admin_candidate_fingerprint = admin_candidate.selected_policy_fingerprint
        admin_candidate_matches_projection = (
            admin_candidate_fingerprint == projected_summary["fingerprint"]
        )

    provider_lifecycle = [
        item.model_dump(mode="json")
        for item in observe_provider_lifecycle(
            runtime_policy=gateway_resolution.effective_policy,
            settings_record=record,
        )
    ]
    quality_record = get_search_quality_policy_repository().get()
    return {
        "runtime_authority": runtime_resolution.authority.value,
        "selected_gateway_policy": selected_summary,
        "selected_policy_fingerprint": runtime_resolution.selected_policy_fingerprint,
        "gateway_effective_policy": gateway_effective_summary,
        "gateway_effective_policy_fingerprint": gateway_resolution.effective_policy_fingerprint,
        "legacy_hunter_admin_projection_applied": gateway_resolution.legacy_hunter_admin_projection_applied,
        "gateway_policy_changed_after_authority_resolution": gateway_resolution.policy_changed,
        "actual_gateway_policy": actual_summary,
        "projected_admin_gateway_policy": projected_summary,
        "admin_candidate_available": admin_candidate_available,
        "admin_candidate_gateway_policy": admin_candidate_summary,
        "admin_candidate_policy_fingerprint": admin_candidate_fingerprint,
        "admin_candidate_matches_projection": admin_candidate_matches_projection,
        "provider_lifecycle": provider_lifecycle,
        "configured_admin_policy_fingerprint": fingerprint_model_payload(record.settings),
        "quality_policy_fingerprint": fingerprint_model_payload(quality_record.policy),
        "policy_equivalent": actual_summary["fingerprint"] == projected_summary["fingerprint"],
        "runtime_callsite_uses_admin_projection": (
            runtime_resolution.authority is HunterSearchPolicyAuthority.ADMIN
        ),
        "routing_changed_by_observation": False,
    }


def _safe_execution_policy_observation(record: SearchStrategySettingsRecord) -> dict:
    """Keep settings/catalog available if optional runtime diagnostics are degraded.

    The admin settings endpoint is an operational control-plane surface. A failure
    in zero-cost observation must not make the editable persisted configuration
    unreadable. The typed degraded projection intentionally exposes no exception
    text, secrets, provider payloads, or runtime paths.
    """

    try:
        return _execution_policy_observation(record)
    except Exception:
        return {
            "observation_state": "degraded",
            "observation_reason": "runtime_observation_unavailable",
            "routing_changed_by_observation": False,
        }


@router.get("")
def read_search_strategy_settings(_admin: User = Depends(require_admin)) -> dict:
    record = get_search_strategy_settings_repository().get()
    return {
        "record": record.model_dump(mode="json"),
        "catalog": [item.model_dump(mode="json") for item in strategy_catalog()],
        "execution_policy_observation": _safe_execution_policy_observation(record),
    }


@router.put("", response_model=SearchStrategySettingsRecord)
def update_search_strategy_settings(
    payload: SearchStrategySettingsUpdate,
    admin: User = Depends(require_admin),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> SearchStrategySettingsRecord:
    _require_csrf(csrf_cookie, csrf_header)
    try:
        return get_search_strategy_settings_repository().save(
            payload.settings,
            actor_id=admin.id,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc


@router.get("/quality-policy", response_model=SearchQualityPolicyRecord)
def read_search_quality_policy(_admin: User = Depends(require_admin)) -> SearchQualityPolicyRecord:
    return get_search_quality_policy_repository().get()


@router.put("/quality-policy", response_model=SearchQualityPolicyRecord)
def update_search_quality_policy(
    payload: SearchQualityPolicyUpdate,
    admin: User = Depends(require_admin),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> SearchQualityPolicyRecord:
    _require_csrf(csrf_cookie, csrf_header)
    try:
        return get_search_quality_policy_repository().save(
            payload.policy,
            actor_id=admin.id,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc
