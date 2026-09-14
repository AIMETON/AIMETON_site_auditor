from __future__ import annotations

import asyncio
import socket
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.scraper as scraper
from app.models import AnalyzeRequest
from app.scraper import (
    BLOCKED_MESSAGE,
    MAX_RENDERED_BYTES,
    FetchError,
    _ensure_rendered_size,
    _fetch_via_httpx,
    fetch_site,
    normalize_url,
)

GOOD_HTML = (
    "<html><head><title>Test Site</title></head><body>"
    + "".join(
        f"<p>Полезный абзац номер {i} с достаточным количеством текста.</p>"
        for i in range(10)
    )
    + "</body></html>"
)


def test_normalize_valid_url():
    assert normalize_url("https://example.com/page") == "https://example.com/page"


def test_normalize_backslashes():
    assert normalize_url("https:\\example.com\page") == "https://example.com/page"


def test_normalize_missing_scheme():
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_rejects_bad_scheme():
    with pytest.raises(FetchError):
        normalize_url("ftp://example.com")


def test_analyze_request_accepts_url_before_normalization():
    request = AnalyzeRequest(url="http:\\krasnoyarsk.lemanapro.ru")
    assert request.url.startswith("http:")


@pytest.mark.asyncio
async def test_httpx_success_does_not_use_browser():
    with (
        patch("app.scraper._validate_public_url"),
        patch(
            "app.scraper._fetch_via_httpx",
            new=AsyncMock(return_value=(200, "https://example.com/", GOOD_HTML)),
        ),
        patch("app.scraper._fetch_via_browser", new=AsyncMock()) as browser,
    ):
        result = await fetch_site("https://example.com")

    assert result["title"] == "Test Site"
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_dns_validation_and_html_parsing_do_not_block_event_loop(monkeypatch):
    original_extract = scraper.extract_visible_text

    def slow_validate(_url: str) -> None:
        time.sleep(0.15)

    def slow_extract(html: str):
        time.sleep(0.15)
        return original_extract(html)

    monkeypatch.setattr(scraper, "_validate_public_url", slow_validate)
    monkeypatch.setattr(scraper, "extract_visible_text", slow_extract)
    monkeypatch.setattr(
        scraper,
        "_fetch_via_httpx",
        AsyncMock(return_value=(200, "https://example.com/", GOOD_HTML)),
    )

    started = time.monotonic()
    task = asyncio.create_task(fetch_site("https://example.com"))
    await asyncio.sleep(0.02)

    assert time.monotonic() - started < 0.1
    result = await task
    assert result["title"] == "Test Site"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 418, 429])
async def test_blocked_status_triggers_browser_fallback(status):
    with (
        patch("app.scraper._validate_public_url"),
        patch(
            "app.scraper._fetch_via_httpx",
            new=AsyncMock(return_value=(status, "https://example.com/", "")),
        ),
        patch(
            "app.scraper._fetch_via_browser",
            new=AsyncMock(return_value=("https://example.com/", "Test Site", GOOD_HTML)),
        ) as browser,
    ):
        result = await fetch_site("https://example.com")

    browser.assert_awaited_once()
    assert result["title"] == "Test Site"
    assert len(result["text"]) >= 80


@pytest.mark.asyncio
async def test_both_paths_blocked_return_controlled_error():
    with (
        patch("app.scraper._validate_public_url"),
        patch(
            "app.scraper._fetch_via_httpx",
            new=AsyncMock(return_value=(403, "https://example.com/", "")),
        ),
        patch(
            "app.scraper._fetch_via_browser",
            new=AsyncMock(side_effect=FetchError(BLOCKED_MESSAGE)),
        ),
    ):
        with pytest.raises(FetchError) as exc:
            await fetch_site("https://example.com")

    assert str(exc.value) == BLOCKED_MESSAGE
    assert "developer.mozilla.org" not in str(exc.value)


@pytest.mark.asyncio
async def test_real_redirect_chain_to_private_ip_is_blocked(monkeypatch):
    def fake_getaddrinfo(host: str, port: int, *args, **kwargs):
        address = "93.184.216.34" if host == "public.example" else host
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "public.example"
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    with pytest.raises(FetchError) as exc:
        await _fetch_via_httpx("https://public.example/", transport=transport)

    assert "локальным" in str(exc.value)


@pytest.mark.asyncio
async def test_thin_page_triggers_browser_fallback():
    thin_html = "<html><title>x</title><p>мало</p></html>"
    with (
        patch("app.scraper._validate_public_url"),
        patch(
            "app.scraper._fetch_via_httpx",
            new=AsyncMock(return_value=(200, "https://example.com/", thin_html)),
        ),
        patch(
            "app.scraper._fetch_via_browser",
            new=AsyncMock(return_value=("https://example.com/", "Test Site", GOOD_HTML)),
        ) as browser,
    ):
        result = await fetch_site("https://example.com")

    browser.assert_awaited_once()
    assert result["title"] == "Test Site"


def test_rendered_html_size_is_limited():
    oversized = "x" * (MAX_RENDERED_BYTES + 1)
    with pytest.raises(FetchError) as exc:
        _ensure_rendered_size(oversized)
    assert "размер" in str(exc.value)
