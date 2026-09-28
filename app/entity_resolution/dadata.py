from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.sef.models import Digest, Identifier


DADATA_FIND_PARTY_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
)
DADATA_SUGGEST_PARTY_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"
)


class DaDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistryMirrorState(StrEnum):
    VERIFIED = "registry_mirror_verified"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"
    UNAVAILABLE = "unavailable"


class DaDataPartyRecord(DaDataModel):
    id: Identifier
    provider: str = "dadata"
    source_kind: str = "registry_mirror"
    source_url: AnyHttpUrl = DADATA_FIND_PARTY_URL
    accessed_at: datetime
    response_digest: Digest
    query: str = Field(min_length=1, max_length=300)
    legal_name: str = Field(min_length=1, max_length=500)
    short_name: str | None = Field(default=None, max_length=500)
    inn: str | None = Field(default=None, pattern=r"^(?:\d{10}|\d{12})$")
    kpp: str | None = Field(default=None, pattern=r"^\d{9}$")
    ogrn: str | None = Field(default=None, pattern=r"^(?:\d{13}|\d{15})$")
    entity_type: str | None = None
    branch_type: str | None = None
    status: str | None = None
    actuality_date: int | None = None
    raw_hid: str | None = None
    lifecycle_state: str = "evidence"
    authority_verified: bool = False


class DaDataLookupResult(DaDataModel):
    schema_version: str = "0.1.0"
    state: RegistryMirrorState
    provider: str = "dadata"
    query: str
    records: list[DaDataPartyRecord] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=lambda: ["official_registry_verification"])
    conflicts: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    authority_verified: bool = False


class DaDataProviderHealth(DaDataModel):
    provider: str = "dadata"
    state: str
    api_token_configured: bool
    secret_configured: bool
    endpoint: AnyHttpUrl = DADATA_FIND_PARTY_URL
    secrets_exposed: bool = False


@dataclass
class _CacheEntry:
    expires_at: float
    result: DaDataLookupResult


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{_digest(parts).removeprefix('sha256:')[:24]}"


def _clean_query(value: str) -> str:
    query = value.strip()
    if not query:
        raise ValueError("DaData query must not be empty")
    if len(query) > 300:
        raise ValueError("DaData query exceeds 300 characters")
    return query


class DaDataRegistryMirrorProvider:
    """Non-authoritative registry mirror adapter.

    DaData can support preliminary entity resolution, but it must never create
    `authority_verified=true`. Official FNS evidence remains a separate gate.
    """

    def __init__(
        self,
        *,
        api_token: str | None = None,
        secret: str | None = None,
        client: httpx.Client | None = None,
        cache_ttl_seconds: int = 86_400,
    ) -> None:
        self._api_token = (api_token or os.getenv("DADATA_API", "")).strip()
        self._secret = (secret or os.getenv("DADATA_SECRET", "")).strip()
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}

    def health(self) -> DaDataProviderHealth:
        return DaDataProviderHealth(
            state="active" if self._api_token else "not_configured",
            api_token_configured=bool(self._api_token),
            secret_configured=bool(self._secret),
        )

    def lookup(self, query: str) -> DaDataLookupResult:
        normalized_query = _clean_query(query)
        cached = self._cache.get(normalized_query)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return cached.result.model_copy(update={"cache_hit": True})
        if not self._api_token:
            return DaDataLookupResult(
                state=RegistryMirrorState.UNAVAILABLE,
                query=normalized_query,
                gaps=["dadata_api_token_missing", "official_registry_verification"],
            )

        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=15.0)
        try:
            response = client.post(
                DADATA_FIND_PARTY_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Token {self._api_token}",
                },
                json={"query": normalized_query},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"dadata_registry_mirror_http_{exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError("dadata_registry_mirror_transport_failed") from exc
        except ValueError as exc:
            raise RuntimeError("dadata_registry_mirror_invalid_json") from exc
        finally:
            if owns_client:
                client.close()

        suggestions = payload.get("suggestions", []) if isinstance(payload, dict) else []
        records = [
            self._record(normalized_query, suggestion)
            for suggestion in suggestions
            if isinstance(suggestion, dict)
        ]
        records = [record for record in records if record is not None]
        state, conflicts = self._classify(normalized_query, records)
        result = DaDataLookupResult(
            state=state,
            query=normalized_query,
            records=records,
            conflicts=conflicts,
        )
        self._cache[normalized_query] = _CacheEntry(
            expires_at=now + self._cache_ttl_seconds,
            result=result,
        )
        return result

    def suggest(self, query: str, *, count: int = 10) -> DaDataLookupResult:
        """Discover preliminary party candidates by name/address without asserting identity."""
        normalized_query = _clean_query(query)
        bounded_count = max(1, min(20, int(count)))
        cache_key = f"suggest:{bounded_count}:{normalized_query.casefold()}"
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return cached.result.model_copy(update={"cache_hit": True})
        if not self._api_token:
            return DaDataLookupResult(
                state=RegistryMirrorState.UNAVAILABLE,
                query=normalized_query,
                gaps=[
                    "dadata_api_token_missing",
                    "name_candidate_only",
                    "official_registry_verification",
                ],
            )

        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=15.0)
        try:
            response = client.post(
                DADATA_SUGGEST_PARTY_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Token {self._api_token}",
                },
                json={"query": normalized_query, "count": bounded_count},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"dadata_registry_mirror_http_{exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError("dadata_registry_mirror_transport_failed") from exc
        except ValueError as exc:
            raise RuntimeError("dadata_registry_mirror_invalid_json") from exc
        finally:
            if owns_client:
                client.close()

        suggestions = payload.get("suggestions", []) if isinstance(payload, dict) else []
        records = [
            self._record(
                normalized_query,
                suggestion,
                source_url=DADATA_SUGGEST_PARTY_URL,
            )
            for suggestion in suggestions
            if isinstance(suggestion, dict)
        ]
        records = [record for record in records if record is not None]
        result = DaDataLookupResult(
            state=RegistryMirrorState.UNRESOLVED,
            query=normalized_query,
            records=records,
            gaps=["name_candidate_only", "official_registry_verification"],
        )
        self._cache[cache_key] = _CacheEntry(
            expires_at=now + self._cache_ttl_seconds,
            result=result,
        )
        return result

    @staticmethod
    def _record(
        query: str,
        suggestion: dict[str, Any],
        *,
        source_url: str = DADATA_FIND_PARTY_URL,
    ) -> DaDataPartyRecord | None:
        data = suggestion.get("data")
        if not isinstance(data, dict):
            return None
        name = data.get("name") if isinstance(data.get("name"), dict) else {}
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        legal_name = name.get("full_with_opf") or suggestion.get("value")
        if not isinstance(legal_name, str) or not legal_name.strip():
            return None
        raw = {"value": suggestion.get("value"), "data": data}
        response_digest = _digest(raw)
        return DaDataPartyRecord(
            id=_stable_id("dadata_party", data.get("hid"), data.get("inn"), data.get("ogrn")),
            accessed_at=datetime.now(UTC),
            response_digest=response_digest,
            query=query,
            source_url=source_url,
            legal_name=legal_name.strip(),
            short_name=name.get("short_with_opf"),
            inn=data.get("inn"),
            kpp=data.get("kpp"),
            ogrn=data.get("ogrn"),
            entity_type=data.get("type"),
            branch_type=data.get("branch_type"),
            status=state.get("status"),
            actuality_date=state.get("actuality_date"),
            raw_hid=data.get("hid"),
        )

    @staticmethod
    def _classify(
        query: str,
        records: list[DaDataPartyRecord],
    ) -> tuple[RegistryMirrorState, list[str]]:
        if not records:
            return RegistryMirrorState.UNRESOLVED, []
        digits = "".join(character for character in query if character.isdigit())
        exact = [record for record in records if digits in {record.inn, record.ogrn}]
        if len(exact) == 1:
            return RegistryMirrorState.VERIFIED, []
        if len(exact) > 1:
            identities = {(item.inn, item.ogrn, item.kpp) for item in exact}
            if len(identities) > 1:
                return RegistryMirrorState.CONFLICTING, ["multiple_registry_mirror_records"]
            return RegistryMirrorState.VERIFIED, []
        return RegistryMirrorState.UNRESOLVED, []


_provider: DaDataRegistryMirrorProvider | None = None


def get_dadata_registry_mirror_provider() -> DaDataRegistryMirrorProvider:
    global _provider
    if _provider is None:
        _provider = DaDataRegistryMirrorProvider()
    return _provider
