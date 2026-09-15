from __future__ import annotations

import asyncio
import re
import ipaddress
import shutil
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.html_text_coverage import uncovered_text_runs

MAX_BYTES = 1_500_000
MAX_RENDERED_BYTES = 3_000_000
MIN_TEXT_LEN = 80
MAX_REDIRECTS = 8
FALLBACK_STATUSES = {401, 403, 418, 429}
BLOCKED_MESSAGE = (
    "Сайт ограничивает автоматический доступ. "
    "Глубокий анализ страницы не выполнен. "
    "Используйте режим разведки компании для анализа доступных внешних источников."
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


class FetchError(RuntimeError):
    pass


def normalize_url(raw: str) -> str:
    """Normalize a user-supplied web address before security validation."""
    url = (raw or "").strip().replace("\\", "/")
    url = re.sub(r"^(https?):/+", r"\1://", url, flags=re.IGNORECASE)
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("Разрешены только корректные HTTP/HTTPS URL")
    return url


def _resolved_addresses(hostname: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, port)
    except socket.gaierror as exc:
        raise FetchError("Не удалось разрешить доменное имя") from exc
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def _is_forbidden_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_public(hostname: str, port: int) -> bool:
    try:
        addresses = _resolved_addresses(hostname, port)
    except FetchError:
        return False
    return bool(addresses) and all(not _is_forbidden_address(ip) for ip in addresses)


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("Разрешены только корректные HTTP/HTTPS URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolved_addresses(parsed.hostname, port)
    if not addresses or any(_is_forbidden_address(ip) for ip in addresses):
        raise FetchError("Доступ к локальным и служебным адресам запрещён")


def extract_visible_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    chunks = []
    for el in soup.select("h1, h2, h3, p, li, a, button"):
        text = " ".join(el.get_text(" ", strip=True).split())
        if len(text) >= 3:
            chunks.append(text)
    chunks.extend(text for _, text in uncovered_text_runs(
        soup.body or soup,
        frozenset({"head", "title", "h1", "h2", "h3", "p", "li", "a", "button"}),
    ))
    deduped = list(dict.fromkeys(chunks))
    return title, "\n".join(deduped)


def _ensure_rendered_size(html: str) -> None:
    if len(html.encode("utf-8")) > MAX_RENDERED_BYTES:
        raise FetchError("Отрендеренная страница превышает допустимый размер")


async def _fetch_via_httpx(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, str, str]:
    """Return status, final URL and HTML while validating every redirect hop."""
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=20,
        headers=BROWSER_HEADERS,
        transport=transport,
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await asyncio.to_thread(_validate_public_url, current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("Сайт вернул некорректное перенаправление")
                    current = str(httpx.URL(current).join(location))
                    continue

                status = response.status_code
                if status >= 400:
                    return status, str(response.url), ""

                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    raise FetchError("Главная страница не является HTML-документом")

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError("Страница превышает допустимый размер")
                    chunks.append(chunk)

                html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                return status, str(response.url), html

    raise FetchError("Слишком много перенаправлений")


async def _fetch_via_browser(url: str) -> tuple[str, str, str]:
    """Render a public page in Chromium without bypassing authorization or CAPTCHA."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise FetchError(BLOCKED_MESSAGE) from exc

    browser = None
    context = None
    try:
        async with async_playwright() as playwright:
            launch_kwargs: dict[str, object] = {"headless": True}
            system_chromium = shutil.which("chromium") or shutil.which("chromium-browser")
            if system_chromium:
                launch_kwargs["executable_path"] = system_chromium

            browser = await playwright.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=BROWSER_HEADERS["User-Agent"],
                locale="ru-RU",
                java_script_enabled=True,
                extra_http_headers={
                    "Accept-Language": BROWSER_HEADERS["Accept-Language"],
                },
            )
            page = await context.new_page()

            async def guard_route(route) -> None:
                request_url = urlparse(route.request.url)
                if request_url.scheme in {"data", "blob"}:
                    await route.continue_()
                    return
                if request_url.scheme not in {"http", "https"} or not request_url.hostname:
                    await route.abort()
                    return
                port = request_url.port or (443 if request_url.scheme == "https" else 80)
                if await asyncio.to_thread(_host_is_public, request_url.hostname, port):
                    await route.continue_()
                else:
                    await route.abort()

            await page.route("**/*", guard_route)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            final_url = page.url
            await asyncio.to_thread(_validate_public_url, final_url)

            if response is not None and response.status in FALLBACK_STATUSES:
                raise FetchError(BLOCKED_MESSAGE)

            await page.wait_for_timeout(1_500)
            title = await page.title()
            html = await page.content()
            _ensure_rendered_size(html)
            return final_url, title, html
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(BLOCKED_MESSAGE) from exc
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def fetch_site(url: str) -> dict[str, str]:
    normalized_url = normalize_url(url)
    await asyncio.to_thread(_validate_public_url, normalized_url)

    status, final_url, html = 0, normalized_url, ""
    httpx_failed = False
    try:
        status, final_url, html = await _fetch_via_httpx(normalized_url)
    except FetchError:
        raise
    except httpx.HTTPError:
        httpx_failed = True

    title, text = ("", "")
    if html:
        title, text = await asyncio.to_thread(extract_visible_text, html)

    needs_fallback = (
        httpx_failed
        or status in FALLBACK_STATUSES
        or len(text) < MIN_TEXT_LEN
    )

    if not httpx_failed and status >= 400 and status not in FALLBACK_STATUSES:
        raise FetchError(f"Сайт вернул ошибку HTTP {status}")

    if not needs_fallback:
        return {"final_url": final_url, "title": title, "text": text}

    final_url, title, html = await _fetch_via_browser(normalized_url)
    _, text = await asyncio.to_thread(extract_visible_text, html)
    if len(text) < MIN_TEXT_LEN:
        raise FetchError(BLOCKED_MESSAGE)
    return {"final_url": final_url, "title": title, "text": text}
