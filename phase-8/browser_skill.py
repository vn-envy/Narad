"""
Matsya browser automation skill — Playwright headless Chromium.

Fetches JavaScript-rendered pages that Tavily/requests cannot reach:
  - Single-page applications (React, Vue, Angular)
  - Pages behind JS-rendered paywalls
  - Sites that require JS execution to load content

Falls back gracefully with a clear error if Playwright is not installed.
No LLM involved — pure browser rendering + text extraction.
"""
from __future__ import annotations

import asyncio
import re


async def browse_url(url: str, extract: str = "text") -> dict:
    """Fetch a JavaScript-rendered page and extract its content.

    Use this when web_search returns no useful content for a specific URL,
    or when the page is known to be a JS-heavy SPA.

    Args:
        url:     The full URL to fetch (must start with http:// or https://)
        extract: What to extract from the page:
                 "text"       — full visible text content (default, most useful)
                 "structured" — headings + paragraphs as a JSON list
                 "links"      — all href links with their anchor text

    Returns a dict with status, content (or structured data), and page metadata.
    Falls back gracefully if Playwright is not installed.
    """
    if not url.startswith(("http://", "https://")):
        return {
            "status":  "error",
            "message": f"URL must start with http:// or https://. Got: {url!r}",
            "content": "",
        }

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status":  "error",
            "message": (
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            ),
            "content": "",
        }

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            # Give JS frameworks time to render
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass  # networkidle can timeout on live-data pages — text is still available

            title = await page.title()

            if extract == "links":
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(el => ({href: el.href, text: el.innerText.trim()}))"
                )
                # Deduplicate and filter empty/JS links
                seen: set[str] = set()
                clean: list[dict] = []
                for link in links:
                    href = link.get("href", "")
                    text = link.get("text", "").strip()
                    if href and href not in seen and not href.startswith("javascript:") and text:
                        seen.add(href)
                        clean.append({"href": href, "text": text[:120]})
                await browser.close()
                return {
                    "status":     "ok",
                    "url":        url,
                    "title":      title,
                    "extract":    "links",
                    "link_count": len(clean),
                    "links":      clean[:200],
                }

            if extract == "structured":
                # Pull headings and paragraphs in document order
                elements = await page.eval_on_selector_all(
                    "h1, h2, h3, h4, p",
                    "els => els.map(el => ({tag: el.tagName.toLowerCase(), text: el.innerText.trim()}))"
                )
                structured = [
                    {"type": el["tag"], "text": el["text"]}
                    for el in elements
                    if el.get("text") and len(el["text"]) > 5
                ]
                await browser.close()
                return {
                    "status":     "ok",
                    "url":        url,
                    "title":      title,
                    "extract":    "structured",
                    "elements":   structured[:500],
                }

            # Default: full visible text
            text = await page.inner_text("body")
            # Collapse excessive whitespace
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            await browser.close()

            return {
                "status":  "ok",
                "url":     url,
                "title":   title,
                "extract": "text",
                "content": text[:15_000],  # cap to avoid token overflow
                "truncated": len(text) > 15_000,
            }

    except Exception as exc:
        return {
            "status":  "error",
            "url":     url,
            "message": f"Browser fetch failed: {exc}",
            "content": "",
        }


def browse_url_sync(url: str, extract: str = "text") -> dict:
    """Synchronous wrapper around browse_url for FunctionTool registration.

    Google ADK FunctionTools must be synchronous. This wrapper runs the
    async browse_url coroutine in a new event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an existing event loop (e.g., FastAPI) — use nest_asyncio
            try:
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(browse_url(url, extract))
            except ImportError:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, browse_url(url, extract))
                    return future.result(timeout=45)
        else:
            return loop.run_until_complete(browse_url(url, extract))
    except Exception as exc:
        return {
            "status":  "error",
            "url":     url,
            "message": f"Event loop error: {exc}",
            "content": "",
        }
