from decimal import Decimal
from pathlib import Path

import pytest

from app.models import HuntRequest
from app.search_gateway.models import SearchPolicy, SearchStrategy
from app.search_strategy_settings import (
    PaidPolicy,
    SearchStrategyId,
    SearchStrategySettingsRepository,
)


def test_default_tariff_profiles_are_persistable_and_start_is_active(tmp_path: Path) -> None:
    repository = SearchStrategySettingsRepository(tmp_path / "runtime.sqlite3")
    record = repository.get()

    assert record.settings.global_settings.active_tariff == "start"
    assert set(record.settings.tariffs) == {"free", "start", "pro", "max"}
    assert record.settings.tariffs["free"].strategy is SearchStrategyId.PRIMARY_ONLY
    assert record.settings.tariffs["pro"].strategy is SearchStrategyId.CASCADE_UNTIL_TARGET
    assert record.settings.tariffs["max"].strategy is SearchStrategyId.SEQUENTIAL_UNION


def test_active_tariff_controls_hunter_runtime_limits(tmp_path: Path) -> None:
    repository = SearchStrategySettingsRepository(tmp_path / "runtime.sqlite3")
    settings = repository.get().settings.model_copy(deep=True)
    settings.global_settings.active_tariff = "pro"
    repository.save(settings, actor_id=7, reason="Use Pro search profile")

    effective = repository.get().settings.apply_hunt_request(
        HuntRequest(region="Красноярск", industries=["стоматология"], max_queries=1, output_limit=1)
    )

    assert effective.max_queries == 30
    assert effective.results_per_query == 15
    assert effective.max_candidates == 200
    assert effective.output_limit == 50
    assert effective.deep_audit_score == 55


def test_free_tariff_forces_self_hosted_provider_only(tmp_path: Path) -> None:
    settings = SearchStrategySettingsRepository(tmp_path / "runtime.sqlite3").get().settings
    settings.global_settings.active_tariff = "free"

    base = SearchPolicy(
        provider_order=("yandex", "searxng", "tavily"),
        allowed_providers=frozenset({"yandex", "searxng", "tavily"}),
        allow_paid_fallback=True,
        allow_paid_fanout=True,
        max_cost_by_currency={"RUB": Decimal("100"), "USD": Decimal("10")},
    )
    policy = settings.apply_search_policy(base)

    assert policy.strategy is SearchStrategy.PRIMARY_ONLY
    assert policy.provider_order == ("searxng",)
    assert policy.allowed_providers == frozenset({"searxng"})
    assert policy.allow_paid_fallback is False
    assert policy.allow_paid_fanout is False


def test_paid_fanout_requires_explicit_budget() -> None:
    from app.search_strategy_settings import TariffSearchProfile

    profile = TariffSearchProfile(
        id="paid",
        label="Paid",
        strategy=SearchStrategyId.SEQUENTIAL_UNION,
        provider_order=["searxng", "yandex"],
        paid_policy=PaidPolicy.ALLOW_WITH_BUDGET,
        paid_fanout_policy=PaidPolicy.ALLOW_WITH_BUDGET,
        max_cost_rub=Decimal("0"),
    )
    with pytest.raises(ValueError, match="nonzero_budget"):
        profile.validate_relationships()


def test_advanced_tariff_safe_strategy_can_be_activated() -> None:
    from app.search_strategy_settings import TariffSearchProfile

    profile = TariffSearchProfile(
        id="future",
        label="Future",
        strategy=SearchStrategyId.PARALLEL_UNION,
        provider_order=["searxng", "yandex"],
    )
    profile.validate_relationships()


def test_shadow_compare_is_owner_debug_only_not_tariff_strategy() -> None:
    from app.search_strategy_settings import TariffSearchProfile

    profile = TariffSearchProfile(
        id="debug",
        label="Debug",
        strategy=SearchStrategyId.SHADOW_COMPARE,
        provider_order=["searxng", "yandex"],
    )
    with pytest.raises(ValueError, match="owner_debug_only"):
        profile.validate_relationships()


def test_tariff_can_inherit_global_strategy(tmp_path: Path) -> None:
    settings = SearchStrategySettingsRepository(tmp_path / "runtime.sqlite3").get().settings
    settings.global_settings.default_strategy = SearchStrategyId.CONSENSUS_UNION
    settings.tariffs["start"].strategy = None

    policy = settings.apply_search_policy(
        SearchPolicy(
            provider_order=("searxng", "yandex", "tavily"),
            allowed_providers=frozenset({"searxng", "yandex", "tavily"}),
        )
    )

    assert policy.strategy is SearchStrategy.CONSENSUS_UNION


def test_disabled_tariff_cannot_become_active(tmp_path: Path) -> None:
    settings = SearchStrategySettingsRepository(tmp_path / "runtime.sqlite3").get().settings
    settings.tariffs["pro"].enabled = False
    settings.global_settings.active_tariff = "pro"

    with pytest.raises(ValueError, match="active_tariff_disabled"):
        settings.validate_relationships()


def test_bootstrap_default_is_persisted_once_and_does_not_overwrite_admin(tmp_path: Path) -> None:
    repository = SearchStrategySettingsRepository(tmp_path / "runtime.sqlite3")
    initial = repository.ensure_bootstrap_default()
    assert initial.updated_at is not None
    assert initial.updated_by is None
    assert initial.reason == "system_bootstrap_default"

    settings = initial.settings.model_copy(deep=True)
    settings.global_settings.active_tariff = "pro"
    saved = repository.save(settings, actor_id=7, reason="Administrator selected Pro")
    repeated = repository.ensure_bootstrap_default()
    assert repeated == saved
