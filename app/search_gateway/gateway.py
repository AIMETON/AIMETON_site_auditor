from __future__ import annotations

from app.research_execution import active_settings, check_execution

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.search_gateway.cache import MemorySearchCache, SearchCache
from app.search_gateway.models import (
    AttemptState,
    FallbackReason,
    GatewayState,
    ProviderAttempt,
    ProviderHealth,
    ProviderReadiness,
    SearchDiagnostics,
    SearchItem,
    SearchPolicy,
    SearchRequest,
    SearchResponse,
    SearchStrategy,
)
from app.search_gateway.providers import ProviderError, SearchProvider


TRACKING_QUERY_KEYS = {"fbclid", "gclid", "yclid", "_openstat"}
HARD_UPSTREAM_FAILURES = {
    FallbackReason.RATE_LIMITED,
    FallbackReason.PROVIDER_BLOCKED,
    FallbackReason.CAPTCHA,
}
IMPLEMENTED_STRATEGIES = frozenset(SearchStrategy)


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def request_fingerprint(request: SearchRequest) -> str:
    payload = {
        "query": " ".join(request.query.split()).casefold(),
        "limit": request.limit,
        "language": request.language.casefold(),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def policy_cache_suffix(policy: SearchPolicy) -> str:
    payload = {
        "strategy": str(policy.strategy),
        "provider_order": list(policy.provider_order),
        "allowed_providers": sorted(policy.allowed_providers) if policy.allowed_providers is not None else None,
        "target_results": policy.target_results,
        "max_providers_per_query": policy.max_providers_per_query,
        "allow_paid_fallback": policy.allow_paid_fallback,
        "allow_paid_fanout": policy.allow_paid_fanout,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None


@dataclass
class _ProviderObservation:
    calls: int = 0
    transport_successes: int = 0
    total_results: int = 0
    total_latency_ms: int = 0


@dataclass
class _Execution:
    provider: str
    attempt: ProviderAttempt
    results: list[SearchItem]
    called: bool


class SearchGateway:
    def __init__(
        self,
        providers: list[SearchProvider],
        *,
        cache: SearchCache | None = None,
        failure_threshold: int = 3,
        recovery_seconds: float = 60.0,
        global_quotas: dict[str, int] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._providers = {provider.name: provider for provider in providers}
        self._cache = cache or MemorySearchCache()
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._circuits = {name: _Circuit() for name in self._providers}
        self._observations = {name: _ProviderObservation() for name in self._providers}
        self._global_quotas = dict(global_quotas or {})
        self._global_usage = {name: 0 for name in self._providers}
        self._mission_costs: dict[str, dict[str, Decimal]] = {}
        self._inflight: dict[str, asyncio.Task[SearchResponse]] = {}
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def _cache_get(self, key: str) -> list[SearchItem] | None:
        try:
            return await self._cache.get(key)
        except Exception:
            return None

    async def _cache_set(
        self, key: str, results: list[SearchItem], ttl_seconds: int
    ) -> None:
        try:
            await self._cache.set(key, results, ttl_seconds)
        except Exception:
            return

    def _circuit_state(self, provider: str) -> str:
        circuit = self._circuits[provider]
        if circuit.opened_at is None:
            return "closed"
        if time.monotonic() - circuit.opened_at >= self._recovery_seconds:
            return "half_open"
        return "open"

    async def _record_success(self, provider: str) -> None:
        async with self._lock:
            self._circuits[provider] = _Circuit()

    async def _record_failure(self, provider: str, reason: FallbackReason = FallbackReason.PROVIDER_ERROR) -> None:
        async with self._lock:
            circuit = self._circuits[provider]
            circuit.failures += 1
            if reason in HARD_UPSTREAM_FAILURES or circuit.failures >= self._failure_threshold:
                circuit.opened_at = time.monotonic()

    async def _record_observation(
        self,
        provider: str,
        *,
        transport_success: bool,
        result_count: int,
        latency_ms: int,
    ) -> None:
        async with self._lock:
            observation = self._observations[provider]
            observation.calls += 1
            observation.transport_successes += int(transport_success)
            observation.total_results += max(0, result_count)
            observation.total_latency_ms += max(0, latency_ms)

    async def _reserve_cost(
        self,
        request: SearchRequest,
        provider: SearchProvider,
        policy: SearchPolicy,
        *,
        secondary: bool,
        secondary_paid_allowed: bool,
    ) -> FallbackReason | None:
        if provider.paid and secondary and not secondary_paid_allowed:
            return FallbackReason.POLICY_BLOCKED
        if provider.paid and provider.cost_amount <= 0:
            return FallbackReason.PRICING_UNKNOWN
        async with self._lock:
            quota = self._global_quotas.get(provider.name)
            if quota is not None and self._global_usage[provider.name] >= quota:
                return FallbackReason.QUOTA_BLOCKED
            costs = self._mission_costs.setdefault(request.mission_id, {})
            current = costs.get(provider.cost_currency, Decimal("0"))
            maximum = policy.max_cost_by_currency.get(provider.cost_currency)
            if provider.cost_amount > 0 and (maximum is None or current + provider.cost_amount > maximum):
                return FallbackReason.BUDGET_BLOCKED
            self._global_usage[provider.name] += 1
            costs[provider.cost_currency] = current + provider.cost_amount
        return None

    @staticmethod
    def _dedupe(results: list[SearchItem], limit: int) -> list[SearchItem]:
        unique: dict[str, SearchItem] = {}
        for item in results:
            normalized = canonical_url(str(item.url))
            if not normalized or normalized in unique:
                continue
            providers = item.corroborated_by or [item.provider]
            unique[normalized] = item.model_copy(
                update={"url": normalized, "corroborated_by": list(dict.fromkeys(providers))}
            )
            if len(unique) >= limit:
                break
        return list(unique.values())

    @staticmethod
    def _consensus_merge(results: list[SearchItem], limit: int) -> list[SearchItem]:
        """Merge by domain and rank corroborated domains before one-provider hits."""
        records: dict[str, tuple[int, SearchItem, list[str]]] = {}
        for position, item in enumerate(results):
            normalized = canonical_url(str(item.url))
            host = (urlsplit(normalized).hostname or "").lower().removeprefix("www.")
            if not host:
                continue
            existing = records.get(host)
            if existing is None:
                providers = list(dict.fromkeys([*(item.corroborated_by or []), item.provider]))
                records[host] = (
                    position,
                    item.model_copy(update={"url": normalized, "corroborated_by": providers}),
                    providers,
                )
                continue
            first_position, retained, providers = existing
            for provider in [*(item.corroborated_by or []), item.provider]:
                if provider not in providers:
                    providers.append(provider)
            records[host] = (
                first_position,
                retained.model_copy(update={"corroborated_by": providers}),
                providers,
            )
        ranked = sorted(records.values(), key=lambda row: (-len(row[2]), row[0]))
        return [item for _, item, _ in ranked[:limit]]

    @staticmethod
    def _totals(attempts: list[ProviderAttempt]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for attempt in attempts:
            if attempt.state in {AttemptState.SUCCEEDED, AttemptState.EMPTY, AttemptState.FAILED}:
                totals[attempt.cost_currency] = totals.get(attempt.cost_currency, Decimal("0")) + attempt.cost_amount
        return totals

    def _provider_names(self, policy: SearchPolicy) -> list[str]:
        return list(policy.provider_order[: policy.max_providers_per_query])

    def _routable_names(self, request: SearchRequest, policy: SearchPolicy) -> list[str]:
        result: list[str] = []
        mission_costs = self._mission_costs.get(request.mission_id, {})
        for name in self._provider_names(policy):
            if policy.allowed_providers is not None and name not in policy.allowed_providers:
                continue
            provider = self._providers.get(name)
            if provider is None or not provider.configured:
                continue
            if not provider.execution_allowed:
                continue
            if self._circuit_state(name) == "open":
                continue
            quota = self._global_quotas.get(name)
            if quota is not None and self._global_usage[name] >= quota:
                continue
            if provider.paid:
                if provider.cost_amount <= 0:
                    continue
                maximum = policy.max_cost_by_currency.get(provider.cost_currency)
                already_spent = mission_costs.get(provider.cost_currency, Decimal("0"))
                if maximum is None or already_spent + provider.cost_amount > maximum:
                    continue
            result.append(name)
        return result

    def _adaptive_order(self, policy: SearchPolicy) -> list[str]:
        order = self._provider_names(policy)
        positions = {name: index for index, name in enumerate(order)}

        def score(name: str) -> tuple[float, float, float, int, int]:
            provider = self._providers.get(name)
            observation = self._observations.get(name, _ProviderObservation())
            if observation.calls:
                success_rate = observation.transport_successes / observation.calls
                yield_rate = observation.total_results / observation.calls
                latency = observation.total_latency_ms / observation.calls
            else:
                success_rate = 0.5
                yield_rate = 0.0
                latency = float("inf")
            paid_penalty = int(bool(provider and provider.paid))
            return (-success_rate, -yield_rate, latency, paid_penalty, positions[name])

        return sorted(order, key=score)

    async def _execute_provider(
        self,
        provider_name: str,
        request: SearchRequest,
        policy: SearchPolicy,
        fingerprint: str,
        *,
        secondary: bool,
        secondary_paid_allowed: bool,
    ) -> _Execution:
        provider = self._providers.get(provider_name)
        policy_blocked = policy.allowed_providers is not None and provider_name not in policy.allowed_providers
        if provider is None or policy_blocked or not provider.configured or not provider.execution_allowed:
            if policy_blocked:
                reason = FallbackReason.POLICY_BLOCKED
            elif provider is not None and provider.configured and not provider.execution_allowed:
                reason = provider.execution_block_reason or FallbackReason.POLICY_BLOCKED
            else:
                reason = FallbackReason.NOT_CONFIGURED
            return _Execution(
                provider=provider_name,
                called=False,
                results=[],
                attempt=ProviderAttempt(
                    provider=provider_name,
                    state=AttemptState.SKIPPED,
                    request_fingerprint=fingerprint,
                    latency_ms=0,
                    result_count=0,
                    reason=reason,
                ),
            )
        if self._circuit_state(provider_name) == "open":
            return _Execution(
                provider=provider_name,
                called=False,
                results=[],
                attempt=ProviderAttempt(
                    provider=provider_name,
                    state=AttemptState.SKIPPED,
                    request_fingerprint=fingerprint,
                    latency_ms=0,
                    result_count=0,
                    reason=FallbackReason.CIRCUIT_OPEN,
                    cost_currency=provider.cost_currency,
                ),
            )

        blocked = await self._reserve_cost(
            request,
            provider,
            policy,
            secondary=secondary,
            secondary_paid_allowed=secondary_paid_allowed,
        )
        if blocked:
            return _Execution(
                provider=provider_name,
                called=False,
                results=[],
                attempt=ProviderAttempt(
                    provider=provider_name,
                    state=AttemptState.SKIPPED,
                    request_fingerprint=fingerprint,
                    latency_ms=0,
                    result_count=0,
                    reason=blocked,
                    cost_currency=provider.cost_currency,
                ),
            )

        started = time.perf_counter()
        error_reason: FallbackReason | None = None
        degradation = None
        results: list[SearchItem] = []
        calls_made = 0
        settings = active_settings()
        retries = settings.retry_count if settings else policy.retries
        timeout = settings.request_timeout_seconds if settings else policy.timeout_seconds
        for retry in range(retries + 1):
            check_execution()
            if retry > 0:
                retry_blocked = await self._reserve_cost(
                    request,
                    provider,
                    policy,
                    secondary=secondary,
                    secondary_paid_allowed=secondary_paid_allowed,
                )
                if retry_blocked:
                    error_reason = retry_blocked
                    break
                backoff_seconds = settings.retry_backoff_seconds if settings else min(
                    policy.retry_backoff_max_seconds,
                    policy.retry_backoff_base_seconds * (2 ** (retry - 1)),
                )
                if backoff_seconds > 0:
                    await self._sleep(backoff_seconds)
            try:
                calls_made += 1
                async with asyncio.timeout(timeout if settings else None):
                    results = await provider.search(request, timeout_seconds=timeout)
                degradation = provider.consume_degradation(request)
                error_reason = None
                break
            except ProviderError as exc:
                error_reason = exc.reason
                if not exc.retryable or retry >= retries:
                    break
            except TimeoutError:
                error_reason = FallbackReason.TIMEOUT
                if retry >= retries:
                    break
            except Exception:
                error_reason = FallbackReason.PROVIDER_ERROR
                break

        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        charged = provider.cost_amount * calls_made
        if error_reason is not None:
            await self._record_failure(provider_name, error_reason)
            await self._record_observation(
                provider_name,
                transport_success=False,
                result_count=0,
                latency_ms=latency_ms,
            )
            return _Execution(
                provider=provider_name,
                called=True,
                results=[],
                attempt=ProviderAttempt(
                    provider=provider_name,
                    state=AttemptState.FAILED,
                    request_fingerprint=fingerprint,
                    latency_ms=latency_ms,
                    result_count=0,
                    reason=error_reason,
                    cost_amount=charged,
                    cost_currency=provider.cost_currency,
                ),
            )

        await self._record_success(provider_name)
        results = self._dedupe(results, request.limit)
        await self._record_observation(
            provider_name,
            transport_success=True,
            result_count=len(results),
            latency_ms=latency_ms,
        )
        return _Execution(
            provider=provider_name,
            called=True,
            results=results,
            attempt=ProviderAttempt(
                provider=provider_name,
                state=AttemptState.SUCCEEDED if results else AttemptState.EMPTY,
                request_fingerprint=fingerprint,
                latency_ms=latency_ms,
                result_count=len(results),
                reason=(
                    degradation.reason
                    if degradation is not None
                    else (None if results else FallbackReason.EMPTY_RESULTS)
                ),
                degraded_upstreams=list(degradation.upstreams) if degradation is not None else [],
                cost_amount=charged,
                cost_currency=provider.cost_currency,
            ),
        )

    @staticmethod
    def _diagnostic_state(attempts: list[ProviderAttempt], *, has_results: bool) -> GatewayState:
        if not any(attempt.state in {AttemptState.SUCCEEDED, AttemptState.EMPTY, AttemptState.FAILED} for attempt in attempts):
            return GatewayState.UNAVAILABLE
        if (
            not has_results
            or any(attempt.state == AttemptState.FAILED for attempt in attempts)
            or any(attempt.degraded_upstreams for attempt in attempts)
        ):
            return GatewayState.DEGRADED
        return GatewayState.SUCCESS

    def _response(
        self,
        *,
        results: list[SearchItem],
        attempts: list[ProviderAttempt],
        selected_providers: list[str],
        fallback_used: bool,
        state: GatewayState | None = None,
    ) -> SearchResponse:
        diagnostic_state = self._diagnostic_state(attempts, has_results=bool(results))
        effective_state = state or diagnostic_state
        if state is GatewayState.SUCCESS and diagnostic_state is not GatewayState.SUCCESS:
            effective_state = diagnostic_state
        return SearchResponse(
            results=results,
            diagnostics=SearchDiagnostics(
                state=effective_state,
                selected_provider="+".join(selected_providers) or None,
                fallback_used=fallback_used,
                attempts=attempts,
                total_cost_by_currency=self._totals(attempts),
            ),
        )

    async def _parallel_executions(
        self,
        names: list[str],
        request: SearchRequest,
        policy: SearchPolicy,
        fingerprint: str,
        *,
        all_secondary: bool = False,
    ) -> list[_Execution]:
        tasks = [
            self._execute_provider(
                name,
                request,
                policy,
                fingerprint,
                secondary=all_secondary or index > 0,
                secondary_paid_allowed=policy.allow_paid_fanout,
            )
            for index, name in enumerate(names)
        ]
        return list(await asyncio.gather(*tasks)) if tasks else []

    async def _clear_inflight(
        self,
        cache_key: str,
        task: asyncio.Task[SearchResponse],
    ) -> None:
        async with self._lock:
            if self._inflight.get(cache_key) is task:
                self._inflight.pop(cache_key, None)

    async def search(self, request: SearchRequest, policy: SearchPolicy | None = None) -> SearchResponse:
        policy = policy or SearchPolicy()
        if active_settings():
            # A controlled run owns cancellation/accounting. Do not shield or share
            # its provider task with an unrelated mission's consent and deadline.
            check_execution()
            return await self._search_impl(request, policy)
        if policy.strategy is SearchStrategy.SHADOW_COMPARE:
            return await self._search_impl(request, policy)

        fingerprint = request_fingerprint(request)
        cache_key = f"{fingerprint}:{policy_cache_suffix(policy)}"
        async with self._lock:
            task = self._inflight.get(cache_key)
            if task is not None and task.done():
                self._inflight.pop(cache_key, None)
                task = None
            coalesced = task is not None
            if task is None:
                task = asyncio.create_task(self._search_impl(request, policy))
                self._inflight[cache_key] = task
                task.add_done_callback(
                    lambda finished, key=cache_key: asyncio.create_task(
                        self._clear_inflight(key, finished)
                    )
                )

        response = await asyncio.shield(task)
        if not coalesced or response.diagnostics.cache_hit:
            return response

        shared_attempts = [
            attempt.model_copy(update={"cost_amount": Decimal("0")})
            for attempt in response.diagnostics.attempts
        ]
        diagnostics = response.diagnostics.model_copy(
            update={
                "coalesced": True,
                "attempts": shared_attempts,
                "total_cost_by_currency": {},
            }
        )
        return response.model_copy(update={"diagnostics": diagnostics})

    async def _search_impl(
        self, request: SearchRequest, policy: SearchPolicy | None = None
    ) -> SearchResponse:
        policy = policy or SearchPolicy()
        strategy = policy.strategy
        if strategy not in IMPLEMENTED_STRATEGIES:
            raise ValueError(f"search_strategy_not_implemented:{strategy}")

        fingerprint = request_fingerprint(request)
        cache_key = f"{fingerprint}:{policy_cache_suffix(policy)}"
        use_cache = strategy is not SearchStrategy.SHADOW_COMPARE
        if use_cache:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                attempt = ProviderAttempt(
                    provider="cache",
                    state=AttemptState.CACHE_HIT,
                    request_fingerprint=fingerprint,
                    latency_ms=0,
                    result_count=len(cached),
                )
                return SearchResponse(
                    results=cached,
                    diagnostics=SearchDiagnostics(
                        state=GatewayState.SUCCESS,
                        selected_provider="cache",
                        cache_hit=True,
                        attempts=[attempt],
                    ),
                )

        if strategy is SearchStrategy.EXHAUSTIVE_COVERAGE:
            final_limit = min(100, request.limit * policy.max_providers_per_query)
        else:
            final_limit = min(100, max(request.limit, policy.target_results))

        if strategy is SearchStrategy.SPLIT_QUERY_ROUTING:
            routable = self._routable_names(request, policy)
            if not routable:
                names = self._provider_names(policy)
                attempts = [
                    (
                        await self._execute_provider(
                            name,
                            request,
                            policy,
                            fingerprint,
                            secondary=False,
                            secondary_paid_allowed=policy.allow_paid_fallback,
                        )
                    ).attempt
                    for name in names[:1]
                ]
                return self._response(results=[], attempts=attempts, selected_providers=[], fallback_used=False)
            slot_seed = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            selected_name = routable[int(slot_seed[:8], 16) % len(routable)]
            execution = await self._execute_provider(
                selected_name,
                request,
                policy,
                fingerprint,
                secondary=False,
                secondary_paid_allowed=policy.allow_paid_fallback,
            )
            response = self._response(
                results=execution.results,
                attempts=[execution.attempt],
                selected_providers=[selected_name] if execution.called else [],
                fallback_used=False,
            )
            if use_cache and response.results:
                await self._cache_set(cache_key, response.results, policy.cache_ttl_seconds)
            return response

        if strategy in {SearchStrategy.PARALLEL_UNION, SearchStrategy.CONSENSUS_UNION}:
            names = self._provider_names(policy)
            executions = await self._parallel_executions(names, request, policy, fingerprint)
            attempts = [execution.attempt for execution in executions]
            selected = [execution.provider for execution in executions if execution.results]
            flattened = [item for execution in executions for item in execution.results]
            results = (
                self._consensus_merge(flattened, final_limit)
                if strategy is SearchStrategy.CONSENSUS_UNION
                else self._dedupe(flattened, final_limit)
            )
            response = self._response(
                results=results,
                attempts=attempts,
                selected_providers=selected,
                fallback_used=sum(execution.called for execution in executions) > 1,
            )
            if response.results:
                await self._cache_set(cache_key, response.results, policy.cache_ttl_seconds)
            return response

        if strategy is SearchStrategy.SHADOW_COMPARE:
            names = self._provider_names(policy)
            attempts: list[ProviderAttempt] = []
            primary_execution: _Execution | None = None
            primary_index = -1
            for index, name in enumerate(names):
                execution = await self._execute_provider(
                    name,
                    request,
                    policy,
                    fingerprint,
                    secondary=False,
                    secondary_paid_allowed=policy.allow_paid_fallback,
                )
                attempts.append(execution.attempt)
                if execution.called:
                    primary_execution = execution
                    primary_index = index
                    break
            if primary_execution is None:
                return self._response(results=[], attempts=attempts, selected_providers=[], fallback_used=False)
            shadow_names = names[primary_index + 1 :]
            shadow_executions = await self._parallel_executions(
                shadow_names,
                request,
                policy,
                fingerprint,
                all_secondary=True,
            )
            attempts.extend(execution.attempt for execution in shadow_executions)
            state = (
                GatewayState.SUCCESS
                if primary_execution.results and primary_execution.attempt.state == AttemptState.SUCCEEDED
                else GatewayState.DEGRADED
            )
            return self._response(
                results=primary_execution.results,
                attempts=attempts,
                selected_providers=[primary_execution.provider],
                fallback_used=False,
                state=state,
            )

        names = (
            self._adaptive_order(policy)
            if strategy is SearchStrategy.ADAPTIVE_COST_QUALITY
            else self._provider_names(policy)
        )
        attempts: list[ProviderAttempt] = []
        accumulated: list[SearchItem] = []
        successful_providers: list[str] = []
        executed = 0

        for name in names:
            if strategy is SearchStrategy.PRIMARY_ONLY and executed >= 1:
                break
            secondary = executed > 0
            secondary_paid_allowed = (
                policy.allow_paid_fallback
                if strategy in {SearchStrategy.PRIMARY_ONLY, SearchStrategy.FALLBACK_FIRST_NONEMPTY}
                else policy.allow_paid_fanout
            )
            execution = await self._execute_provider(
                name,
                request,
                policy,
                fingerprint,
                secondary=secondary,
                secondary_paid_allowed=secondary_paid_allowed,
            )
            attempts.append(execution.attempt)
            if execution.called:
                executed += 1

            if strategy in {SearchStrategy.PRIMARY_ONLY, SearchStrategy.FALLBACK_FIRST_NONEMPTY}:
                if execution.results:
                    response = self._response(
                        results=execution.results,
                        attempts=attempts,
                        selected_providers=[execution.provider],
                        fallback_used=executed > 1,
                        state=GatewayState.DEGRADED if executed > 1 else GatewayState.SUCCESS,
                    )
                    await self._cache_set(cache_key, response.results, policy.cache_ttl_seconds)
                    return response
                if strategy is SearchStrategy.PRIMARY_ONLY and execution.called:
                    break
                continue

            if execution.results:
                successful_providers.append(execution.provider)
                accumulated = self._dedupe([*accumulated, *execution.results], final_limit)
                if strategy in {SearchStrategy.CASCADE_UNTIL_TARGET, SearchStrategy.ADAPTIVE_COST_QUALITY} and len(accumulated) >= policy.target_results:
                    break

        if strategy in {
            SearchStrategy.CASCADE_UNTIL_TARGET,
            SearchStrategy.SEQUENTIAL_UNION,
            SearchStrategy.ADAPTIVE_COST_QUALITY,
            SearchStrategy.EXHAUSTIVE_COVERAGE,
        } and accumulated:
            response = self._response(
                results=accumulated,
                attempts=attempts,
                selected_providers=successful_providers,
                fallback_used=executed > 1,
            )
            await self._cache_set(cache_key, response.results, policy.cache_ttl_seconds)
            return response

        return self._response(
            results=[],
            attempts=attempts,
            selected_providers=[],
            fallback_used=executed > 1,
        )

    def health(self, policy: SearchPolicy | None = None) -> list[ProviderHealth]:
        policy = policy or SearchPolicy()
        health: list[ProviderHealth] = []
        for name, provider in self._providers.items():
            quota = self._global_quotas.get(name)
            remaining = None if quota is None else max(0, quota - self._global_usage[name])
            upstream_cooldowns = provider.upstream_cooldowns()
            upstream_open = provider.upstream_circuit_open()
            circuit_state = "open" if upstream_open else self._circuit_state(name)
            maximum = policy.max_cost_by_currency.get(provider.cost_currency)
            policy_blocked = policy.allowed_providers is not None and name not in policy.allowed_providers
            if policy_blocked:
                state = ProviderReadiness.POLICY_BLOCKED
            elif not provider.configured:
                state = ProviderReadiness.NOT_CONFIGURED
            elif not provider.execution_allowed:
                state = (
                    ProviderReadiness.CONTRACT_BLOCKED
                    if provider.execution_block_reason == FallbackReason.CONTRACT_BLOCKED
                    else ProviderReadiness.POLICY_BLOCKED
                )
            elif circuit_state == "open":
                state = ProviderReadiness.CIRCUIT_OPEN
            elif remaining == 0:
                state = ProviderReadiness.QUOTA_BLOCKED
            elif provider.paid and provider.cost_amount <= 0:
                state = ProviderReadiness.PRICING_UNKNOWN
            elif provider.paid and (maximum is None or maximum < provider.cost_amount):
                state = ProviderReadiness.BUDGET_BLOCKED
            else:
                state = ProviderReadiness.ACTIVE
            health.append(
                ProviderHealth(
                    provider=name,
                    state=state,
                    ready=state == ProviderReadiness.ACTIVE,
                    configured=provider.configured,
                    paid=provider.paid,
                    circuit_state=circuit_state,
                    quota_remaining=remaining,
                    upstream_cooldowns=upstream_cooldowns or None,
                    scheduling=provider.scheduling_policy(),
                )
            )
        return health
