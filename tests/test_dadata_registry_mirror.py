import httpx
import pytest

from app.entity_resolution.dadata import (
    DaDataRegistryMirrorProvider,
    RegistryMirrorState,
)


def _payload(*, inn: str = "7707083893", ogrn: str = "1027700132195") -> dict:
    return {
        "suggestions": [
            {
                "value": "ПАО СБЕРБАНК",
                "data": {
                    "hid": "party-hid-1",
                    "inn": inn,
                    "kpp": "773601001",
                    "ogrn": ogrn,
                    "type": "LEGAL",
                    "branch_type": "MAIN",
                    "name": {
                        "full_with_opf": "ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО СБЕРБАНК",
                        "short_with_opf": "ПАО СБЕРБАНК",
                    },
                    "state": {
                        "status": "ACTIVE",
                        "actuality_date": 1_785_283_200_000,
                    },
                },
            }
        ]
    }


def test_exact_inn_match_is_registry_mirror_verified_but_not_authoritative():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token test-token"
        assert "test-token" not in str(request.url)
        return httpx.Response(200, json=_payload())

    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        secret="test-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.lookup("7707083893")

    assert result.state == RegistryMirrorState.VERIFIED
    assert result.authority_verified is False
    assert result.gaps == ["official_registry_verification"]
    assert result.records[0].authority_verified is False
    assert result.records[0].source_kind == "registry_mirror"
    assert result.records[0].response_digest.startswith("sha256:")


def test_cache_prevents_second_provider_call():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_payload())

    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    first = provider.lookup("7707083893")
    second = provider.lookup("7707083893")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert calls == 1


def test_missing_token_is_fail_closed_and_does_not_call_network():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called without a token")

    provider = DaDataRegistryMirrorProvider(
        api_token="",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.lookup("7707083893")

    assert result.state == RegistryMirrorState.UNAVAILABLE
    assert result.authority_verified is False
    assert "dadata_api_token_missing" in result.gaps


def test_empty_result_remains_unresolved():
    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"suggestions": []})
            )
        ),
    )

    result = provider.lookup("7707083893")

    assert result.state == RegistryMirrorState.UNRESOLVED
    assert result.records == []
    assert result.authority_verified is False


def test_multiple_distinct_exact_records_require_review():
    payload = _payload()
    second = _payload(ogrn="1027700132196")["suggestions"][0]
    payload["suggestions"].append(second)
    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=payload)
            )
        ),
    )

    result = provider.lookup("7707083893")

    assert result.state == RegistryMirrorState.CONFLICTING
    assert result.conflicts == ["multiple_registry_mirror_records"]
    assert result.authority_verified is False


def test_health_never_exposes_secret_values():
    provider = DaDataRegistryMirrorProvider(
        api_token="api-value",
        secret="secret-value",
    )

    health = provider.health().model_dump(mode="json")

    assert health["state"] == "active"
    assert health["api_token_configured"] is True
    assert health["secret_configured"] is True
    assert health["secrets_exposed"] is False
    assert "api-value" not in str(health)
    assert "secret-value" not in str(health)


def test_http_status_failure_keeps_only_safe_status_class():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "secret provider body"})

    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match=r"^dadata_registry_mirror_http_403$") as exc_info:
        provider.lookup("7707083893")

    assert "test-token" not in str(exc_info.value)
    assert "secret provider body" not in str(exc_info.value)


def test_invalid_json_has_typed_failure_without_response_body():
    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, text="not-json")
            )
        ),
    )

    with pytest.raises(RuntimeError, match=r"^dadata_registry_mirror_invalid_json$"):
        provider.lookup("7707083893")



def test_name_suggest_is_candidate_only_and_uses_suggest_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.content.decode("utf-8")
        assert request.headers["Authorization"] == "Token test-token"
        return httpx.Response(200, json=_payload())

    provider = DaDataRegistryMirrorProvider(
        api_token="test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.suggest("Сбербанк Москва", count=8)

    assert result.state == RegistryMirrorState.UNRESOLVED
    assert result.authority_verified is False
    assert "name_candidate_only" in result.gaps
    assert captured["url"].endswith("/suggest/party")
    assert '"count":8' in captured["payload"]
    assert result.records
    assert str(result.records[0].source_url).endswith("/suggest/party")
    assert result.records[0].authority_verified is False


def test_name_suggest_without_token_is_fail_closed():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called without a token")

    provider = DaDataRegistryMirrorProvider(
        api_token="",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.suggest("Тестовая компания")

    assert result.state == RegistryMirrorState.UNAVAILABLE
    assert result.records == []
    assert "dadata_api_token_missing" in result.gaps
    assert "name_candidate_only" in result.gaps
