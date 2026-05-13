"""
Matsya interactive browser skill — Playwright headless Chromium with form interaction.

Three functions with a screenshot-first safety model:
  browser_screenshot         — read-only: navigate, screenshot, detect form fields
  browser_fill               — fill form fields; dry_run=True (default) previews only
  browser_upload_and_submit  — fill + upload files + submit (explicit user confirmation required)

The safety model mirrors Vamana:
  - Read-only operations (screenshot) are always safe.
  - Mutating operations (fill + submit) require dry_run=False, which Matsya must
    only set after the user has explicitly confirmed ("yes", "submit", "go ahead").
  - Blocked domains list prevents auto-submit on sensitive sites.
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path

_OUTPUTS_BASE = Path(__file__).parent.parent / "phase-7" / "outputs"

# Domains where submit is blocked regardless of dry_run — safety guardrail
_BLOCKED_SUBMIT_DOMAINS = {
    "bankofamerica.com", "chase.com", "wellsfargo.com", "citibank.com",
    "irs.gov", "ssa.gov", "healthcare.gov", "cms.gov",
}


def _run(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _save_screenshot(screenshot_bytes: bytes, run_id: str, name: str) -> str:
    run_dir = _OUTPUTS_BASE / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / name
    path.write_bytes(screenshot_bytes)
    return str(path)


async def _detect_fields(page) -> list[dict]:
    """Detect fillable form fields on the page."""
    fields = await page.evaluate("""() => {
        const results = [];
        const inputs = document.querySelectorAll('input, textarea, select');
        inputs.forEach(el => {
            const label = el.labels?.[0]?.innerText?.trim()
                || el.getAttribute('placeholder')
                || el.getAttribute('aria-label')
                || el.getAttribute('name')
                || el.id
                || el.type
                || 'unknown';
            results.push({
                label: label,
                type: el.tagName.toLowerCase() === 'select' ? 'select'
                      : el.type || el.tagName.toLowerCase(),
                name: el.getAttribute('name') || '',
                id: el.id || '',
                placeholder: el.getAttribute('placeholder') || '',
            });
        });
        return results;
    }""")
    return fields


async def _browser_screenshot_async(url: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "error",
            "message": "Playwright not installed. Run: pip install playwright && playwright install chromium",
        }

    run_id = uuid.uuid4().hex[:8]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            title = await page.title()
            screenshot_bytes = await page.screenshot(full_page=False)
            fields = await _detect_fields(page)
            screenshot_path = _save_screenshot(screenshot_bytes, run_id, "screenshot.png")
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            return {
                "status":          "ok",
                "run_id":          run_id,
                "page_title":      title,
                "screenshot_path": screenshot_path,
                "screenshot_b64":  screenshot_b64,
                "fields_detected": fields,
                "field_count":     len(fields),
                "message": (
                    f"Page loaded: '{title}'. Detected {len(fields)} form fields. "
                    "Review fields_detected and confirm which values to fill before calling browser_fill."
                ),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        finally:
            await browser.close()


async def _browser_fill_async(url: str, fields: dict, dry_run: bool) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "error",
            "message": "Playwright not installed. Run: pip install playwright && playwright install chromium",
        }

    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    if not dry_run and any(domain.endswith(d) for d in _BLOCKED_SUBMIT_DOMAINS):
        return {
            "status":  "blocked",
            "message": f"Submit blocked for domain {domain} — this site is in the safety blocklist.",
        }

    run_id = uuid.uuid4().hex[:8]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            filled = []
            errors = []

            for selector_or_label, value in fields.items():
                try:
                    # Try CSS selector first, then label text, then placeholder, then name attr
                    el = None
                    for attempt in [
                        lambda: page.locator(selector_or_label).first,
                        lambda: page.get_by_label(selector_or_label).first,
                        lambda: page.get_by_placeholder(selector_or_label).first,
                        lambda: page.locator(f'[name="{selector_or_label}"]').first,
                    ]:
                        try:
                            candidate = attempt()
                            if await candidate.count() > 0:
                                el = candidate
                                break
                        except Exception:
                            continue

                    if el is None:
                        errors.append(f"Field not found: {selector_or_label!r}")
                        continue

                    tag = await el.evaluate("el => el.tagName.toLowerCase()")
                    input_type = await el.evaluate("el => el.type || ''")

                    if tag == "select":
                        await el.select_option(label=str(value))
                    elif input_type in ("checkbox", "radio"):
                        if str(value).lower() in ("true", "1", "yes", "on"):
                            await el.check()
                        else:
                            await el.uncheck()
                    else:
                        await el.fill(str(value))

                    filled.append(selector_or_label)
                except Exception as exc:
                    errors.append(f"{selector_or_label!r}: {exc}")

            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_path = _save_screenshot(screenshot_bytes, run_id, "filled.png")
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

            if dry_run:
                return {
                    "status":          "ok",
                    "dry_run":         True,
                    "run_id":          run_id,
                    "fields_filled":   filled,
                    "errors":          errors,
                    "screenshot_path": screenshot_path,
                    "screenshot_b64":  screenshot_b64,
                    "message": (
                        f"Preview: {len(filled)} fields filled (NOT submitted — dry_run=True). "
                        f"Errors: {errors if errors else 'none'}. "
                        "To submit, call browser_fill with dry_run=False after user confirms."
                    ),
                }

            # Submit — find and click the submit button
            submit_btn = None
            for sel in ["[type=submit]", "button[type=submit]", "input[type=submit]",
                        "button:has-text('Submit')", "button:has-text('Apply')",
                        "button:has-text('Send')"]:
                try:
                    candidate = page.locator(sel).first
                    if await candidate.count() > 0:
                        submit_btn = candidate
                        break
                except Exception:
                    continue

            if submit_btn:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)

            after_screenshot = await page.screenshot(full_page=False)
            after_path = _save_screenshot(after_screenshot, run_id, "after_submit.png")
            after_b64 = base64.b64encode(after_screenshot).decode()

            return {
                "status":                    "ok",
                "dry_run":                   False,
                "run_id":                    run_id,
                "fields_filled":             filled,
                "errors":                    errors,
                "submitted":                 submit_btn is not None,
                "after_screenshot_path":     after_path,
                "after_screenshot_b64":      after_b64,
                "message": (
                    f"Form submitted. {len(filled)} fields filled. "
                    f"Submit button {'found and clicked' if submit_btn else 'NOT found — manual submit may be needed'}. "
                    f"Errors: {errors if errors else 'none'}."
                ),
            }

        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        finally:
            await browser.close()


async def _browser_upload_submit_async(url: str, fields: dict, file_uploads: dict) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "error",
            "message": "Playwright not installed. Run: pip install playwright && playwright install chromium",
        }

    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    if any(domain.endswith(d) for d in _BLOCKED_SUBMIT_DOMAINS):
        return {
            "status":  "blocked",
            "message": f"Submit blocked for domain {domain} — this site is in the safety blocklist.",
        }

    run_id = uuid.uuid4().hex[:8]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Fill text fields
            filled = []
            errors = []
            for selector_or_label, value in fields.items():
                try:
                    el = None
                    for attempt in [
                        lambda s=selector_or_label: page.locator(s).first,
                        lambda s=selector_or_label: page.get_by_label(s).first,
                        lambda s=selector_or_label: page.get_by_placeholder(s).first,
                        lambda s=selector_or_label: page.locator(f'[name="{s}"]').first,
                    ]:
                        try:
                            candidate = attempt()
                            if await candidate.count() > 0:
                                el = candidate
                                break
                        except Exception:
                            continue
                    if el is None:
                        errors.append(f"Field not found: {selector_or_label!r}")
                        continue
                    await el.fill(str(value))
                    filled.append(selector_or_label)
                except Exception as exc:
                    errors.append(f"{selector_or_label!r}: {exc}")

            # Upload files
            uploaded = []
            for selector, file_path in file_uploads.items():
                try:
                    file_input = page.locator(selector).first
                    if await file_input.count() == 0:
                        file_input = page.locator(f'[name="{selector}"]').first
                    await file_input.set_input_files(file_path)
                    uploaded.append(selector)
                except Exception as exc:
                    errors.append(f"Upload {selector!r}: {exc}")

            before_bytes = await page.screenshot(full_page=False)
            before_path = _save_screenshot(before_bytes, run_id, "before_submit.png")

            # Submit
            submit_btn = None
            for sel in ["[type=submit]", "button[type=submit]",
                        "button:has-text('Submit')", "button:has-text('Apply')"]:
                try:
                    candidate = page.locator(sel).first
                    if await candidate.count() > 0:
                        submit_btn = candidate
                        break
                except Exception:
                    continue

            if submit_btn:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)

            after_bytes = await page.screenshot(full_page=False)
            after_path = _save_screenshot(after_bytes, run_id, "after_submit.png")
            after_b64 = base64.b64encode(after_bytes).decode()

            return {
                "status":                "ok",
                "run_id":                run_id,
                "fields_filled":         filled,
                "files_uploaded":        uploaded,
                "errors":                errors,
                "submitted":             submit_btn is not None,
                "after_screenshot_path": after_path,
                "after_screenshot_b64":  after_b64,
                "message": (
                    f"Upload and submit complete. Fields: {len(filled)}, files: {len(uploaded)}. "
                    f"Submit {'succeeded' if submit_btn else 'button not found — may need manual submit'}. "
                    f"Errors: {errors if errors else 'none'}."
                ),
            }

        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        finally:
            await browser.close()


# ── Public sync API (what FunctionTool wraps) ─────────────────────────────────

def browser_screenshot(url: str) -> dict:
    """Navigate to a URL, take a screenshot, and detect form fields.

    Always safe — read-only, no side effects. Call this first before any form interaction.

    Args:
        url: The full URL to visit (must start with http:// or https://)

    Returns:
        status, page_title, fields_detected (list of {label, type, name, id, placeholder}),
        screenshot_b64 (base64 PNG for display), screenshot_path, message.

    Use this to:
    - Inspect any web page or form before filling it
    - Show the user what a job application form looks like
    - Identify field names/labels before calling browser_fill
    """
    return _run(_browser_screenshot_async(url))


def browser_fill(url: str, fields: dict, dry_run: bool = True) -> dict:
    """Fill form fields on a web page. Default is dry_run=True (preview only, no submit).

    SAFETY CONTRACT:
      dry_run=True  (default) — fills fields in browser memory, takes screenshot, does NOT submit.
                                Always call with dry_run=True first to show the user the filled state.
      dry_run=False           — fills AND submits. ONLY use after the user explicitly confirms
                                ("yes", "submit it", "go ahead", "looks good, submit").

    Args:
        url:      The full URL of the form page
        fields:   Dict mapping field selector/label/name/placeholder to value.
                  Examples: {"Email": "user@example.com", "#resume-field": "path/to/file",
                             "[name=cover_letter]": "Dear...", "Full Name": "Jane Smith"}
        dry_run:  True = preview only (default). False = actually submit (requires user confirmation).

    Returns:
        status, dry_run flag, fields_filled, errors, screenshot_b64, message.
    """
    return _run(_browser_fill_async(url, fields, dry_run))


def browser_upload_and_submit(url: str, fields: dict, file_uploads: dict) -> dict:
    """Fill form fields, upload files, and submit a form. REQUIRES explicit user confirmation first.

    NEVER call this without the user explicitly saying to submit (e.g. "yes, apply",
    "submit it", "go ahead and apply"). Always call browser_screenshot and browser_fill
    (dry_run=True) first so the user can review the filled state.

    Args:
        url:          The full URL of the application form
        fields:       Dict mapping field label/selector to value (same as browser_fill)
        file_uploads: Dict mapping file input selector to local file path.
                      Example: {"[name=resume]": "/Users/.../resume.docx",
                                "input[type=file]": "/tmp/cover.pdf"}

    Returns:
        status, fields_filled, files_uploaded, errors, after_screenshot_b64, message.
    """
    return _run(_browser_upload_submit_async(url, fields, file_uploads))
