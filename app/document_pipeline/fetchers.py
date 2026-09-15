from __future__ import annotations

from app.research_execution import research_timed

import codecs
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from charset_normalizer import from_bytes

from app.document_pipeline.models import RedirectHop
from app.scraper import (
    BROWSER_HEADERS,
    FetchError,
    _fetch_via_browser,
    _validate_public_url,
)


ALLOWED_HTML_TYPES = {"text/html", "application/xhtml+xml"}
SUPPORTED_HTTP_CONTENT_ENCODINGS = "gzip, deflate"
CHARSET_RE = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)", re.I)
META_CHARSET_RE = re.compile(
    br"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
    re.I,
)
META_HTTP_EQUIV_RE = re.compile(
    br"<meta[^>]+http-equiv\s*=\s*[\"']?\s*content-type[^>]+"
    br"content\s*=\s*[\"'][^\"']*charset\s*=\s*([a-zA-Z0-9._-]+)",
    re.I,
)
META_HTTP_EQUIV_CONTENT_FIRST_RE = re.compile(
    br"<meta[^>]+content\s*=\s*[\"'][^\"']*charset\s*=\s*"
    br"([a-zA-Z0-9._-]+)[^>]+http-equiv\s*=\s*[\"']?\s*content-type",
    re.I,
)


def _url_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{netloc}"


def _allowlist_key(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        return f"{host}:{port}"
    return host


def _decode_candidate(content: bytes, encoding: str) -> str | None:
    try:
        decoded = content.decode(encoding, errors="strict")
    except (LookupError, UnicodeDecodeError):
        return None
    if "\ufffd" in decoded:
        return None
    controls = sum(
        1
        for char in decoded
        if ord(char) < 32 and char not in {"\t", "\n", "\r", "\f"}
    )
    if decoded and controls / len(decoded) > 0.01:
        return None
    return decoded


def decode_html_bytes(content: bytes, content_type: str) -> tuple[str, str, str]:
    """Decode HTML deterministically: BOM → HTTP → meta → detector → error."""
    bom_candidates = (
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_BE, "utf-16"),
        (codecs.BOM_UTF16_LE, "utf-16"),
    )
    for bom, encoding in bom_candidates:
        if content.startswith(bom):
            decoded = _decode_candidate(content, encoding)
            if decoded is not None:
                return decoded, encoding, "bom"

    header_match = CHARSET_RE.search(content_type)
    if header_match:
        encoding = header_match.group(1)
        decoded = _decode_candidate(content, encoding)
        if decoded is not None:
            return decoded, encoding.lower(), "http"

    prefix = content[:16_384]
    meta_match = (
        META_CHARSET_RE.search(prefix)
        or META_HTTP_EQUIV_RE.search(prefix)
        or META_HTTP_EQUIV_CONTENT_FIRST_RE.search(prefix)
    )
    if meta_match:
        encoding = meta_match.group(1).decode("ascii")
        decoded = _decode_candidate(content, encoding)
        if decoded is not None:
            return decoded, encoding.lower(), "meta"

    detected = from_bytes(content).best()
    if detected is not None and detected.encoding:
        decoded = _decode_candidate(content, detected.encoding)
        if decoded is not None:
            return decoded, detected.encoding.lower(), "detector"

    raise FetchError(
        "Не удалось достоверно определить кодировку HTML "
        "(reason_code=encoding_undetermined)"
    )


@dataclass(frozen=True)
class RawDocument:
    final_url: str
    title: str
    html: str
    media_type: str
    path: str
    raw_content: bytes | None = None
    detected_encoding: str | None = None
    encoding_source: str | None = None
    redirect_history: tuple[RedirectHop, ...] = ()


class DynamicFetcher(ABC):
    name: str

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def fetch(self, url: str, *, timeout_seconds: float) -> RawDocument: ...


class StaticHttpFetcher:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @research_timed("request")
    async def fetch(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        max_redirects: int,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> RawDocument:
        headers = {
            **BROWSER_HEADERS,
            # Do not advertise Brotli unless the runtime has an explicit decoder.
            # httpx always supports gzip/deflate; constraining negotiation prevents
            # a `Content-Encoding: br` body from reaching the charset decoder as
            # opaque compressed bytes.
            "Accept-Encoding": SUPPORTED_HTTP_CONTENT_ENCODINGS,
        }
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout_seconds,
            headers=headers,
            transport=self._transport,
        ) as client:
            current = url
            redirect_history: list[RedirectHop] = []
            for _ in range(max_redirects + 1):
                _validate_public_url(current)
                if allowed_hosts and _allowlist_key(current) not in allowed_hosts:
                    raise FetchError("Redirect нарушает domain allowlist")
                try:
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError("Сайт вернул некорректное перенаправление")
                            target = str(httpx.URL(current).join(location))
                            if (
                                allowed_hosts
                                and _allowlist_key(target) not in allowed_hosts
                            ):
                                raise FetchError("Redirect нарушает domain allowlist")
                            redirect_history.append(
                                RedirectHop(
                                    status_code=response.status_code,
                                    from_origin=_origin(current),
                                    to_origin=_origin(target),
                                    from_url_digest=_url_digest(current),
                                    to_url_digest=_url_digest(target),
                                )
                            )
                            current = target
                            continue
                        if response.status_code >= 400:
                            raise FetchError(f"Сайт вернул ошибку HTTP {response.status_code}")

                        content_type = response.headers.get("content-type", "")
                        media_type = content_type.split(";", 1)[0].lower()
                        if media_type not in ALLOWED_HTML_TYPES:
                            raise FetchError("Документ не является разрешённым HTML")

                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise FetchError("Документ превышает допустимый размер")
                            chunks.append(chunk)
                        raw_content = b"".join(chunks)
                        html, encoding, encoding_source = decode_html_bytes(
                            raw_content,
                            content_type,
                        )
                        return RawDocument(
                            final_url=str(response.url),
                            title="",
                            html=html,
                            media_type=media_type,
                            path="static",
                            raw_content=raw_content,
                            detected_encoding=encoding,
                            encoding_source=encoding_source,
                            redirect_history=tuple(redirect_history),
                        )
                except httpx.TimeoutException as exc:
                    raise FetchError("Истекло время загрузки документа") from exc
                except httpx.HTTPError as exc:
                    raise FetchError("Ошибка загрузки документа") from exc
        raise FetchError("Слишком много перенаправлений")


class Crawl4AIHttpWorker(DynamicFetcher):
    name = "crawl4ai"

    def __init__(
        self,
        base_url: str | None,
        *,
        api_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._api_token = (api_token or "").strip()
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    @research_timed("request")
    async def fetch(self, url: str, *, timeout_seconds: float) -> RawDocument:
        if not self.configured:
            raise FetchError("Crawl4AI worker не настроен")
        _validate_public_url(url)
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/crawl",
                    headers=(
                        {"Authorization": f"Bearer {self._api_token}"}
                        if self._api_token
                        else None
                    ),
                    json={
                        "urls": [url],
                        "browser_config": {
                            "type": "BrowserConfig",
                            "params": {"headless": True},
                        },
                        "crawler_config": {
                            "type": "CrawlerRunConfig",
                            "params": {
                                "stream": False,
                                "cache_mode": "enabled",
                            },
                        },
                    },
                )
                response.raise_for_status()
                payload: Any = response.json()
        except httpx.TimeoutException as exc:
            raise FetchError("Истекло время Crawl4AI worker") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise FetchError("Crawl4AI worker недоступен") from exc

        result: Any = payload
        if isinstance(payload, dict):
            result = (
                payload.get("results")
                or payload.get("result")
                or payload.get("data")
                or payload
            )
        if isinstance(result, list):
            result = result[0] if result else {}
        if isinstance(result, dict) and isinstance(result.get("result"), dict):
            result = result["result"]
        if not isinstance(result, dict) or result.get("success") is False:
            raise FetchError("Crawl4AI worker не смог извлечь документ")
        html = result.get("cleaned_html") or result.get("html")
        final_url = result.get("url") or url
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        if not isinstance(html, str) or not html.strip():
            raise FetchError("Crawl4AI worker не вернул HTML")
        _validate_public_url(str(final_url))
        return RawDocument(
            final_url=str(final_url),
            title=str(result.get("title") or metadata.get("title") or ""),
            html=html,
            media_type="text/html",
            path=self.name,
            raw_content=html.encode("utf-8"),
            detected_encoding="utf-8",
            encoding_source="renderer",
        )


class PlaywrightFallback(DynamicFetcher):
    name = "browser"

    @property
    def configured(self) -> bool:
        return True

    @research_timed("request")
    async def fetch(self, url: str, *, timeout_seconds: float) -> RawDocument:
        del timeout_seconds
        final_url, title, html = await _fetch_via_browser(url)
        return RawDocument(
            final_url=final_url,
            title=title,
            html=html,
            media_type="text/html",
            path=self.name,
            raw_content=html.encode("utf-8"),
            detected_encoding="utf-8",
            encoding_source="renderer",
        )
