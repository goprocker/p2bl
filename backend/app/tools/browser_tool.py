"""Web search + shared headless browser.

Search transports are tried in order until one returns results. Search
engines actively detect and block automated access (CAPTCHAs, stalled
connections), and which transport survives varies by network and by day,
so the chain is:

  1. ddgs library     (DuckDuckGo over plain HTTP — no headless-browser
                       fingerprint, so it usually survives engine blocking)
  2. DuckDuckGo HTML  (Playwright; server-rendered, but blocks some IPs)
  3. Mojeek           (Playwright; independent index, CAPTCHAs under load)

The shared headless Chromium instance is launched lazily on first use and
reused for every search / page read (read_webpage) until the app shuts down.
"""

import asyncio
import logging
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from ddgs import DDGS
from playwright.async_api import Browser, Playwright, async_playwright

logger = logging.getLogger(__name__)

_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None

PAGE_TIMEOUT_MS = 30_000

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Each engine: url template, container selector, and a JS mapper returning
# {title, href, snippet} per result node.
_ENGINES = [
    {
        "name": "duckduckgo",
        "url": "https://html.duckduckgo.com/html/?q={query}",
        "container": ".result",
        "mapper": """(nodes) => nodes.slice(0, 10).map(n => ({
            title: n.querySelector('.result__a')?.innerText ?? '',
            href: n.querySelector('.result__a')?.getAttribute('href') ?? '',
            snippet: n.querySelector('.result__snippet')?.innerText ?? '',
        }))""",
    },
    {
        "name": "mojeek",
        "url": "https://www.mojeek.com/search?q={query}",
        "container": "ul.results-standard li",
        "mapper": """(nodes) => nodes.slice(0, 10).map(n => ({
            title: n.querySelector('a.title')?.innerText ?? '',
            href: n.querySelector('a.title')?.getAttribute('href') ?? '',
            snippet: (n.querySelector('p.s') || n.querySelector('.desc') || n.querySelector('p'))?.innerText ?? '',
        }))""",
    },
]


async def get_browser() -> Browser:
    """Lazily start (and cache) one shared headless Chromium instance."""
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        logger.info("Launched headless Chromium for browser tools")
    return _browser


async def shutdown() -> None:
    """Close the shared browser on app shutdown."""
    global _playwright, _browser
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


async def new_page():
    browser = await get_browser()
    page = await browser.new_page(
        user_agent=_USER_AGENT,
        locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    return page


async def browser_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results as clean text with sources."""
    # Transport 1: plain-HTTP DuckDuckGo (no browser fingerprint to block)
    try:
        results = await _ddgs_search(query, max_results)
    except Exception as exc:  # noqa: BLE001 — fall through to Playwright engines
        logger.warning("ddgs search failed: %s", exc)
        results = []
    if results:
        return _format_results(query, "duckduckgo", results)

    # Transport 2: Playwright-scraped engines
    for engine in _ENGINES:
        try:
            results = await _search_engine(engine, query, max_results)
        except Exception as exc:  # noqa: BLE001 — try the next engine
            logger.warning("Search engine %s failed: %s", engine["name"], exc)
            continue
        if results:
            return _format_results(query, engine["name"], results)
        logger.info("Engine %s returned no results (possibly blocked), trying next", engine["name"])

    return (
        f"No search results found for: {query}. "
        "The search engines may be blocking automated access from this network."
    )


def _format_results(query: str, engine_name: str, results: list[dict]) -> str:
    lines = [f"Search results for '{query}' (via {engine_name}):", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   Source: {r['url']}")
    return "\n".join(lines)


async def _ddgs_search(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo over plain HTTP via the ddgs library (sync → thread)."""

    def _run() -> list[dict]:
        with DDGS() as ddgs:
            return [
                {
                    "title": (r.get("title") or "").strip(),
                    "url": r.get("href") or "",
                    "snippet": " ".join((r.get("body") or "").split()),
                }
                for r in ddgs.text(query, max_results=max_results)
            ]

    results = await asyncio.to_thread(_run)
    return [r for r in results if r["title"] and r["url"].startswith("http")]


async def _search_engine(engine: dict, query: str, max_results: int) -> list[dict]:
    page = await new_page()
    try:
        await page.goto(engine["url"].format(query=quote_plus(query)),
                        wait_until="domcontentloaded")
        raw = await page.eval_on_selector_all(engine["container"], engine["mapper"])
    finally:
        await page.close()

    results = []
    for item in raw:
        url = _clean_ddg_url(item["href"])
        if item["title"] and url.startswith("http"):
            results.append({"title": item["title"].strip(), "url": url,
                            "snippet": " ".join(item["snippet"].split())})
        if len(results) >= max_results:
            break
    return results


def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps result links in a redirect (/l/?uddg=<real-url>)."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        real = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(real)
    return href
