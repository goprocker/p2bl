"""read_webpage(url) — open a page with Playwright and extract readable text."""

import logging

from bs4 import BeautifulSoup

from app.tools import browser_tool

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 8_000

# Elements that are never part of the readable content
_STRIP_TAGS = ["script", "style", "noscript", "nav", "footer", "header",
               "aside", "form", "iframe", "svg", "button"]


async def read_webpage(url: str) -> str:
    """Return title, URL and the main readable text of a web page."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    page = await browser_tool.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        # Give JS-heavy pages a moment to render their content
        await page.wait_for_timeout(1_500)
        title = await page.title()
        html = await page.content()
    finally:
        await page.close()

    text = _extract_text(html)
    if not text:
        return f"Opened '{title}' ({url}) but could not extract readable text."

    truncated = ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        truncated = f"\n\n[Content truncated at {MAX_TEXT_CHARS} characters]"

    return f"Title: {title}\nURL: {url}\n\n{text}{truncated}"


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    # Prefer semantic containers; fall back to the whole body
    container = soup.find("article") or soup.find("main") or soup.body or soup
    lines = (line.strip() for line in container.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)
