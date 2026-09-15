from __future__ import annotations

from app.research_execution import active_settings

import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.search_gateway.gateway import HARD_UPSTREAM_FAILURES, SearchGateway, request_fingerprint
from app.search_gateway.models import (
    AttemptState,
    FallbackReason,
    GatewayState,
    SearchItem,
    SearchPolicy,
    SearchRequest,
    SearchResponse,
)
from app.search_gateway.trace_bridge import persist_provider_waterfall
from app.search_gateway.traced_policy import resolve_traced_gateway_policy
from app.trace_context import current_trace_identity
from app.trace_ledger import TraceEventCreate, TraceState
from app.trace_write_metrics import InstrumentedSQLiteTraceLedger


_TRACE_TITLE_LIMIT = 500
_TRACE_SNIPPET_LIMIT = 1200
_TRACE_URL_LIMIT = 1500


def _diagnostic_url(item: SearchItem) -> str:
    parts = urlsplit(str(item.url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:_TRACE_URL_LIMIT]


def _provider_budget_seconds(policy: SearchPolicy) -> float:
    settings = active_settings()
    if settings:
        return settings.request_timeout_seconds * (settings.retry_count + 1) + settings.retry_backoff_seconds * settings.retry_count
    backoff = sum(
        min(
            policy.retry_backoff_max_seconds,
            policy.retry_backoff_base_seconds * (2 ** (retry - 1)),
        )
        for retry in range(1, policy.retries + 1)
    )
    return (policy.timeout_seconds * (policy.retries + 1)) + backoff


def _attempt_trace_state(state: AttemptState) -> TraceState:
    if state in {AttemptState.SUCCEEDED, AttemptState.CACHE_HIT}:
        return TraceState.SUCCEEDED
    if state == AttemptState.EMPTY:
        return TraceState.DEGRADED
    if state == AttemptState.SKIPPED:
        return TraceState.SKIPPED
    return TraceState.FAILED


class TracedSearchGateway(SearchGateway):
    """Search gateway with fail-open durable diagnostics."""

    def __init__(self, *args, trace_db_path: str | Path | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        configured = trace_db_path or os.getenv(
            "AIMETON_TRACE_DB",
            os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3"),
        )
        self._trace_ledger = InstrumentedSQLiteTraceLedger(configured)

    async def _record_failure(
        self,
        provider: str,
        reason: FallbackReason = FallbackReason.PROVIDER_ERROR,
    ) -> None:
        provider_impl = self._providers.get(provider)
        if provider_impl is not None and reason in HARD_UPSTREAM_FAILURES:
            cooldowns = provider_impl.upstream_cooldowns()
            if cooldowns and not provider_impl.upstream_circuit_open():
                # A multi-upstream provider such as SearXNG has already isolated
                # the failed engines. Keep the provider routable while at least
                # one configured upstream remains eligible instead of opening a
                # coarse provider-wide circuit on the first CAPTCHA/403/429.
                return
        await super()._record_failure(provider, reason)

    async def _execute_provider(
        self,
        provider_name: str,
        request: SearchRequest,
        policy: SearchPolicy,
        fingerprint: str,
        *,
        secondary: bool,
        secondary_paid_allowed: bool,
    ):
        """Expose in-flight provider state before the provider returns.

        The existing provider waterfall is persisted after a search call
        finishes. These two live events fill the observability gap while a
        provider is still in flight, without storing query text or raw payloads.
        """
        bound = current_trace_identity()
        mission_id = bound.mission_id if bound else request.mission_id
        attempt_id = bound.attempt_id if bound else request.correlation_id
        normalized_fingerprint = fingerprint.removeprefix("sha256:")
        query_index = int(hashlib.sha256(normalized_fingerprint.encode("ascii")).hexdigest()[:8], 16)
        runtime_version = os.getenv("AIMETON_RUNTIME_VERSION") or None
        provider_budget_seconds = _provider_budget_seconds(policy)
        live_key = f"{mission_id}:{attempt_id}:query:{query_index}:{provider_name}:live"
        started = time.perf_counter()

        try:
            self._trace_ledger.append(
                TraceEventCreate(
                    mission_id=mission_id,
                    attempt_id=attempt_id,
                    component="search_gateway",
                    operation="provider_live_started",
                    state=TraceState.STARTED,
                    reason_code="provider_call_inflight",
                    summary=f"Provider {provider_name} call is in flight",
                    provider=provider_name,
                    metadata={
                        "query_index": query_index,
                        "secondary": secondary,
                        "timeout_seconds": active_settings().request_timeout_seconds if active_settings() else policy.timeout_seconds,
                        "retry_limit": active_settings().retry_count if active_settings() else policy.retries,
                        "provider_budget_seconds": provider_budget_seconds,
                    },
                    event_key=f"{live_key}:started",
                    runtime_version=runtime_version,
                )
            )
        except Exception:
            pass

        try:
            execution = await super()._execute_provider(
                provider_name,
                request,
                policy,
                fingerprint,
                secondary=secondary,
                secondary_paid_allowed=secondary_paid_allowed,
            )
        except Exception:
            try:
                self._trace_ledger.append(
                    TraceEventCreate(
                        mission_id=mission_id,
                        attempt_id=attempt_id,
                        component="search_gateway",
                        operation="provider_live_finished",
                        state=TraceState.FAILED,
                        reason_code="provider_call_exception",
                        summary=f"Provider {provider_name} call ended with an exception",
                        provider=provider_name,
                        duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                        metadata={
                            "query_index": query_index,
                            "secondary": secondary,
                            "provider_budget_seconds": provider_budget_seconds,
                        },
                        event_key=f"{live_key}:finished",
                        runtime_version=runtime_version,
                    )
                )
            except Exception:
                pass
            raise

        try:
            self._trace_ledger.append(
                TraceEventCreate(
                    mission_id=mission_id,
                    attempt_id=attempt_id,
                    component="search_gateway",
                    operation="provider_live_finished",
                    state=_attempt_trace_state(execution.attempt.state),
                    reason_code=str(execution.attempt.reason or execution.attempt.state.value),
                    summary=f"Provider {provider_name} live call finished",
                    provider=provider_name,
                    duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                    counters={"results_received": len(execution.results)},
                    metadata={
                        "query_index": query_index,
                        "secondary": secondary,
                        "provider_budget_seconds": provider_budget_seconds,
                    },
                    event_key=f"{live_key}:finished",
                    runtime_version=runtime_version,
                )
            )
        except Exception:
            pass
        return execution

    async def search(
        self,
        request: SearchRequest,
        policy: SearchPolicy | None = None,
    ) -> SearchResponse:
        incoming_policy = policy or SearchPolicy()
        policy_resolution = resolve_traced_gateway_policy(
            request=request,
            incoming_policy=incoming_policy,
        )
        effective_policy = policy_resolution.effective_policy

        fingerprint = request_fingerprint(request).removeprefix("sha256:")
        query_index = int(hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:8], 16)
        bound = current_trace_identity()
        mission_id = bound.mission_id if bound else request.mission_id
        attempt_id = bound.attempt_id if bound else request.correlation_id
        runtime_version = os.getenv("AIMETON_RUNTIME_VERSION") or None

        try:
            self._trace_ledger.append(
                TraceEventCreate(
                    mission_id=mission_id,
                    attempt_id=attempt_id,
                    component="search_gateway",
                    operation="query_planned",
                    state=TraceState.STARTED,
                    reason_code="query_planned",
                    summary="Bounded search query prepared for provider gateway",
                    counters={"requested_limit": request.limit},
                    metadata={
                        "query_index": query_index,
                        "query_text": " ".join(request.query.split())[:500],
                        "language": request.language[:32],
                        "search_strategy": str(effective_policy.strategy),
                        "provider_order": list(effective_policy.provider_order),
                        "allowed_providers": sorted(effective_policy.allowed_providers) if effective_policy.allowed_providers is not None else None,
                        "allow_paid_fallback": effective_policy.allow_paid_fallback,
                        "allow_paid_fanout": effective_policy.allow_paid_fanout,
                        "target_results": effective_policy.target_results,
                        "max_providers_per_query": effective_policy.max_providers_per_query,
                        "incoming_policy_fingerprint": policy_resolution.incoming_policy_fingerprint,
                        "gateway_effective_policy_fingerprint": policy_resolution.effective_policy_fingerprint,
                        "legacy_hunter_admin_projection_applied": policy_resolution.legacy_hunter_admin_projection_applied,
                        "gateway_policy_changed": policy_resolution.policy_changed,
                    },
                    event_key=f"{mission_id}:{attempt_id}:query:{query_index}:planned",
                    runtime_version=runtime_version,
                )
            )
        except Exception:
            pass

        response = await super().search(request, effective_policy)
        try:
            persist_provider_waterfall(
                self._trace_ledger,
                response.diagnostics,
                mission_id=mission_id,
                attempt_id=attempt_id,
                query_index=query_index,
                runtime_version=runtime_version,
            )
        except Exception:
            pass

        try:
            query_state = (
                TraceState.SUCCEEDED
                if response.diagnostics.state == GatewayState.SUCCESS
                else TraceState.DEGRADED
            )
            self._trace_ledger.append(
                TraceEventCreate(
                    mission_id=mission_id,
                    attempt_id=attempt_id,
                    component="search_gateway",
                    operation="query_finished",
                    state=query_state,
                    reason_code=f"query_{response.diagnostics.state.value}",
                    summary="Bounded search query finished",
                    provider=response.diagnostics.selected_provider,
                    counters={"results_received": len(response.results)},
                    metadata={
                        "query_index": query_index,
                        "fallback_used": response.diagnostics.fallback_used,
                        "cache_hit": response.diagnostics.cache_hit,
                    },
                    event_key=f"{mission_id}:{attempt_id}:query:{query_index}:finished",
                    runtime_version=runtime_version,
                )
            )
        except Exception:
            pass

        for rank, item in enumerate(response.results, start=1):
            try:
                self._trace_ledger.append(
                    TraceEventCreate(
                        mission_id=mission_id,
                        attempt_id=attempt_id,
                        component="search_gateway",
                        operation="result_item",
                        state=TraceState.SUCCEEDED,
                        reason_code="normalized_search_result",
                        summary=f"Normalized search result #{rank} retained for admin diagnostics",
                        provider=item.provider,
                        counters={"result_rank": rank},
                        metadata={
                            "query_index": query_index,
                            "result_rank": rank,
                            "result_url": _diagnostic_url(item),
                            "result_title": item.title[:_TRACE_TITLE_LIMIT],
                            "result_snippet": item.snippet[:_TRACE_SNIPPET_LIMIT],
                            "published_at": item.published_at,
                            "corroborated_by": item.corroborated_by,
                            "corroboration_count": len(item.corroborated_by),
                        },
                        event_key=f"{mission_id}:{attempt_id}:query:{query_index}:result:{rank}",
                        runtime_version=runtime_version,
                    )
                )
            except Exception:
                pass
        return response
