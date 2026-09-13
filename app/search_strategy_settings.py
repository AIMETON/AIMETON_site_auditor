from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
import os
from pathlib import Path
import sqlite3
from threading import RLock

from pydantic import BaseModel, Field

from app.models import HuntRequest
from app.search_gateway.models import SearchPolicy, SearchStrategy


SETTINGS_KEY = "search.strategy.settings.v1"
KNOWN_PROVIDERS = ("searxng", "yandex", "tavily")
PAID_PROVIDERS = frozenset({"yandex", "tavily"})


class SearchStrategyId(StrEnum):
    PRIMARY_ONLY = "primary_only"
    FALLBACK_FIRST_NONEMPTY = "fallback_first_nonempty"
    CASCADE_UNTIL_TARGET = "cascade_until_target"
    SEQUENTIAL_UNION = "sequential_union"
    PARALLEL_UNION = "parallel_union"
    CONSENSUS_UNION = "consensus_union"
    SPLIT_QUERY_ROUTING = "split_query_routing"
    ADAPTIVE_COST_QUALITY = "adaptive_cost_quality"
    EXHAUSTIVE_COVERAGE = "exhaustive_coverage"
    SHADOW_COMPARE = "shadow_compare"


class PaidPolicy(StrEnum):
    INHERIT = "inherit"
    DENY = "deny"
    ALLOW_WITH_BUDGET = "allow_with_budget"


IMPLEMENTED_STRATEGIES = frozenset(SearchStrategyId)


class StrategyDescriptor(BaseModel):
    id: SearchStrategyId
    label: str
    description: str
    coverage: str
    cost_profile: str
    implemented: bool
    tariff_safe: bool = True


STRATEGY_CATALOG = (
    StrategyDescriptor(id=SearchStrategyId.PRIMARY_ONLY, label="Один движок", description="Использовать только первый доступный разрешённый provider.", coverage="низкий/предсказуемый", cost_profile="минимальный", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.FALLBACK_FIRST_NONEMPTY, label="Резервирование", description="Идти по providers последовательно и остановиться на первом непустом ответе.", coverage="средний", cost_profile="низкий", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.CASCADE_UNTIL_TARGET, label="Каскад до цели", description="Последовательно объединять выдачи, пока не набрано целевое число уникальных результатов или не исчерпаны разрешённые providers.", coverage="высокий", cost_profile="управляемый", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.SEQUENTIAL_UNION, label="Последовательное объединение", description="Вызвать все разрешённые providers по очереди и объединить/дедуплицировать их результаты.", coverage="высокий", cost_profile="средний/высокий", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.PARALLEL_UNION, label="Параллельное объединение", description="Параллельно вызвать разрешённые providers и объединить результаты для меньшей задержки.", coverage="высокий", cost_profile="средний/высокий", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.CONSENSUS_UNION, label="Консенсус providers", description="Объединить выдачи и поднять домены, подтверждённые несколькими независимыми providers.", coverage="высокий + подтверждение", cost_profile="высокий", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.SPLIT_QUERY_ROUTING, label="Разделение query-вариантов", description="Детерминированно распределять разные LLM query-варианты между готовыми providers вместо полного fan-out каждого запроса.", coverage="высокий/экономный", cost_profile="управляемый", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.ADAPTIVE_COST_QUALITY, label="Адаптивный качество/стоимость", description="Переупорядочивать providers по фактической успешности, yield, latency и cost-приоритету; каскадировать до целевого результата.", coverage="адаптивный", cost_profile="оптимизируемый", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.EXHAUSTIVE_COVERAGE, label="Максимальный охват", description="Использовать все разрешённые providers в пределах max_providers, hard budget, quota и circuit guards.", coverage="максимальный", cost_profile="максимальный", implemented=True),
    StrategyDescriptor(id=SearchStrategyId.SHADOW_COMPARE, label="Shadow-сравнение", description="Вернуть ответ primary provider, а вторичные providers выполнить только для диагностического benchmark без влияния на выдачу.", coverage="диагностический", cost_profile="дополнительный", implemented=True, tariff_safe=False),
)


class TariffSearchProfile(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    strategy: SearchStrategyId | None = None
    provider_order: list[str] = Field(default_factory=lambda: list(KNOWN_PROVIDERS), min_length=1, max_length=3)
    paid_policy: PaidPolicy = PaidPolicy.INHERIT
    paid_fanout_policy: PaidPolicy = PaidPolicy.INHERIT
    max_cost_rub: Decimal = Field(default=Decimal("0"), ge=0)
    max_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    target_results: int = Field(default=25, ge=1, le=100)
    max_providers_per_query: int = Field(default=1, ge=1, le=3)
    max_queries: int = Field(default=20, ge=1, le=100)
    results_per_query: int = Field(default=10, ge=1, le=30)
    max_candidates: int = Field(default=100, ge=1, le=500)
    output_limit: int = Field(default=25, ge=1, le=100)
    minimum_pre_score: int = Field(default=35, ge=0, le=100)
    deep_audit_score: int = Field(default=60, ge=0, le=100)
    concurrency: int = Field(default=4, ge=1, le=12)

    def validate_relationships(self) -> None:
        if self.strategy is not None and self.strategy not in IMPLEMENTED_STRATEGIES:
            raise ValueError(f"strategy_not_implemented:{self.strategy}")
        if self.strategy is SearchStrategyId.SHADOW_COMPARE:
            raise ValueError("shadow_compare_is_owner_debug_only")
        if len(set(self.provider_order)) != len(self.provider_order):
            raise ValueError("provider_order_contains_duplicates")
        if any(provider not in KNOWN_PROVIDERS for provider in self.provider_order):
            raise ValueError("unknown_provider")
        if self.deep_audit_score < self.minimum_pre_score:
            raise ValueError("deep_audit_score must be >= minimum_pre_score")
        if self.output_limit > self.max_candidates:
            raise ValueError("output_limit must be <= max_candidates")
        if self.max_providers_per_query > len(self.provider_order):
            raise ValueError("max_providers_per_query exceeds provider_order")
        if self.paid_policy is PaidPolicy.ALLOW_WITH_BUDGET and self.max_cost_rub <= 0 and self.max_cost_usd <= 0:
            raise ValueError("paid_providers_require_nonzero_budget")
        if self.paid_fanout_policy is PaidPolicy.ALLOW_WITH_BUDGET and self.max_cost_rub <= 0 and self.max_cost_usd <= 0:
            raise ValueError("paid_fanout_requires_nonzero_budget")


def default_tariff_profiles() -> dict[str, TariffSearchProfile]:
    return {
        "free": TariffSearchProfile(
            id="free", label="Free", strategy=SearchStrategyId.PRIMARY_ONLY,
            provider_order=["searxng"], paid_policy=PaidPolicy.DENY, paid_fanout_policy=PaidPolicy.DENY,
            target_results=15, max_providers_per_query=1, max_queries=8, results_per_query=5,
            max_candidates=40, output_limit=15, minimum_pre_score=35, deep_audit_score=65, concurrency=2,
        ),
        "start": TariffSearchProfile(
            id="start", label="Start", strategy=SearchStrategyId.FALLBACK_FIRST_NONEMPTY,
            provider_order=["searxng", "yandex", "tavily"], target_results=25, max_providers_per_query=3,
            max_queries=20, results_per_query=10, max_candidates=100, output_limit=25,
            minimum_pre_score=35, deep_audit_score=60, concurrency=4,
        ),
        "pro": TariffSearchProfile(
            id="pro", label="Pro", strategy=SearchStrategyId.CASCADE_UNTIL_TARGET,
            provider_order=["searxng", "yandex", "tavily"], target_results=40, max_providers_per_query=3,
            max_queries=30, results_per_query=15, max_candidates=200, output_limit=50,
            minimum_pre_score=30, deep_audit_score=55, concurrency=6,
        ),
        "max": TariffSearchProfile(
            id="max", label="Max", strategy=SearchStrategyId.SEQUENTIAL_UNION,
            provider_order=["searxng", "yandex", "tavily"], target_results=75, max_providers_per_query=3,
            max_queries=50, results_per_query=20, max_candidates=400, output_limit=100,
            minimum_pre_score=25, deep_audit_score=50, concurrency=8,
        ),
    }


class GlobalSearchSettings(BaseModel):
    active_tariff: str = "start"
    default_strategy: SearchStrategyId = SearchStrategyId.FALLBACK_FIRST_NONEMPTY
    enabled_providers: list[str] = Field(default_factory=lambda: list(KNOWN_PROVIDERS), min_length=1, max_length=3)
    canonical_provider_order: list[str] = Field(default_factory=lambda: ["searxng", "yandex", "tavily"], min_length=1, max_length=3)
    paid_policy: PaidPolicy = PaidPolicy.INHERIT
    paid_fanout_policy: PaidPolicy = PaidPolicy.INHERIT
    hard_max_cost_rub: Decimal = Field(default=Decimal("0"), ge=0)
    hard_max_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    emergency_strategy_override: SearchStrategyId | None = None

    def validate_relationships(self, tariffs: dict[str, TariffSearchProfile]) -> None:
        if self.active_tariff not in tariffs:
            raise ValueError("active_tariff_not_found")
        if not tariffs[self.active_tariff].enabled:
            raise ValueError("active_tariff_disabled")
        if self.default_strategy not in IMPLEMENTED_STRATEGIES:
            raise ValueError("default_strategy_not_implemented")
        if self.emergency_strategy_override is not None and self.emergency_strategy_override not in IMPLEMENTED_STRATEGIES:
            raise ValueError("emergency_strategy_not_implemented")
        if len(set(self.enabled_providers)) != len(self.enabled_providers):
            raise ValueError("enabled_providers_contains_duplicates")
        if len(set(self.canonical_provider_order)) != len(self.canonical_provider_order):
            raise ValueError("canonical_provider_order_contains_duplicates")
        if any(provider not in KNOWN_PROVIDERS for provider in self.enabled_providers + self.canonical_provider_order):
            raise ValueError("unknown_provider")
        if self.paid_policy is PaidPolicy.ALLOW_WITH_BUDGET and self.hard_max_cost_rub <= 0 and self.hard_max_cost_usd <= 0:
            raise ValueError("global_paid_search_requires_nonzero_budget")
        if self.paid_fanout_policy is PaidPolicy.ALLOW_WITH_BUDGET and self.hard_max_cost_rub <= 0 and self.hard_max_cost_usd <= 0:
            raise ValueError("global_paid_fanout_requires_nonzero_budget")


class SearchStrategySettings(BaseModel):
    global_settings: GlobalSearchSettings = Field(default_factory=GlobalSearchSettings)
    tariffs: dict[str, TariffSearchProfile] = Field(default_factory=default_tariff_profiles)

    def validate_relationships(self) -> None:
        if not self.tariffs:
            raise ValueError("at_least_one_tariff_required")
        for key, profile in self.tariffs.items():
            if key != profile.id:
                raise ValueError("tariff_key_must_match_id")
            profile.validate_relationships()
        self.global_settings.validate_relationships(self.tariffs)

    def active_profile(self) -> TariffSearchProfile:
        return self.tariffs[self.global_settings.active_tariff]

    def apply_hunt_request(self, request: HuntRequest) -> HuntRequest:
        profile = self.active_profile()
        return request.model_copy(update={
            "max_queries": profile.max_queries,
            "results_per_query": profile.results_per_query,
            "max_candidates": profile.max_candidates,
            "minimum_pre_score": profile.minimum_pre_score,
            "deep_audit_score": profile.deep_audit_score,
            "output_limit": profile.output_limit,
            "concurrency": profile.concurrency,
        })

    @staticmethod
    def _paid_value(policy: PaidPolicy, inherited: bool) -> bool:
        if policy is PaidPolicy.INHERIT:
            return inherited
        return policy is PaidPolicy.ALLOW_WITH_BUDGET

    @staticmethod
    def _cap_budget(base: dict[str, Decimal], profile: TariffSearchProfile, global_settings: GlobalSearchSettings) -> dict[str, Decimal]:
        result = dict(base)
        requested = {"RUB": profile.max_cost_rub, "USD": profile.max_cost_usd}
        hard = {"RUB": global_settings.hard_max_cost_rub, "USD": global_settings.hard_max_cost_usd}
        for currency in ("RUB", "USD"):
            values = [value for value in (result.get(currency), requested[currency], hard[currency]) if value is not None and value > 0]
            if values:
                result[currency] = min(values)
        return result

    def apply_search_policy(self, base: SearchPolicy) -> SearchPolicy:
        global_settings = self.global_settings
        profile = self.active_profile()
        order = tuple(
            provider for provider in profile.provider_order
            if provider in global_settings.enabled_providers
        )
        if not order:
            order = tuple(provider for provider in global_settings.canonical_provider_order if provider in global_settings.enabled_providers)
        strategy_id = global_settings.emergency_strategy_override or profile.strategy or global_settings.default_strategy
        allow_paid = self._paid_value(global_settings.paid_policy, base.allow_paid_fallback)
        allow_paid = self._paid_value(profile.paid_policy, allow_paid)
        allow_fanout = self._paid_value(global_settings.paid_fanout_policy, base.allow_paid_fanout)
        allow_fanout = self._paid_value(profile.paid_fanout_policy, allow_fanout)
        if not allow_paid:
            allowed = frozenset(provider for provider in order if provider not in PAID_PROVIDERS)
        else:
            allowed = frozenset(order)
        return base.model_copy(update={
            "provider_order": order,
            "allowed_providers": allowed,
            "strategy": SearchStrategy(str(strategy_id)),
            "target_results": profile.target_results,
            "max_providers_per_query": profile.max_providers_per_query,
            "allow_paid_fallback": allow_paid,
            "allow_paid_fanout": allow_paid and allow_fanout,
            "max_cost_by_currency": self._cap_budget(base.max_cost_by_currency, profile, global_settings),
        })


class SearchStrategySettingsRecord(BaseModel):
    settings: SearchStrategySettings = Field(default_factory=SearchStrategySettings)
    updated_at: str | None = None
    updated_by: int | None = None
    reason: str | None = None


class SearchStrategySettingsRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runtime_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get(self) -> SearchStrategySettingsRecord:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM runtime_meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
        if row is None:
            return SearchStrategySettingsRecord()
        try:
            record = SearchStrategySettingsRecord.model_validate_json(row["value"])
            record.settings.validate_relationships()
            return record
        except Exception:
            return SearchStrategySettingsRecord()

    def ensure_bootstrap_default(self) -> SearchStrategySettingsRecord:
        """Persist the product default once without overwriting an admin decision.

        A clean runtime database must have a canonical persisted policy before
        Hunter serves requests. INSERT OR IGNORE keeps concurrent starts atomic.
        Invalid existing state remains fail-closed and is never replaced here.
        """
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM runtime_meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
            if row is None:
                record = SearchStrategySettingsRecord(
                    settings=SearchStrategySettings(),
                    updated_at=datetime.now(UTC).isoformat(),
                    updated_by=None,
                    reason="system_bootstrap_default",
                )
                db.execute(
                    "INSERT OR IGNORE INTO runtime_meta(key, value) VALUES(?, ?)",
                    (SETTINGS_KEY, record.model_dump_json()),
                )
        return self.get()

    def save(self, settings: SearchStrategySettings, *, actor_id: int, reason: str) -> SearchStrategySettingsRecord:
        settings.validate_relationships()
        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise ValueError("reason_required")
        record = SearchStrategySettingsRecord(
            settings=settings,
            updated_at=datetime.now(UTC).isoformat(),
            updated_by=actor_id,
            reason=normalized_reason[:500],
        )
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO runtime_meta(key, value) VALUES(?, ?)", (SETTINGS_KEY, record.model_dump_json()))
        return record


def get_search_strategy_settings_repository() -> SearchStrategySettingsRepository:
    return SearchStrategySettingsRepository()


def strategy_catalog() -> list[StrategyDescriptor]:
    return list(STRATEGY_CATALOG)
