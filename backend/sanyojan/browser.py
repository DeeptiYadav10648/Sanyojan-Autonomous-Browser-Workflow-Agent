"""Run a script-free browser over HTML supplied exclusively by the safety layer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .extraction import ExtractionAgent


DEMO_URL = "https://demo.sanyojan.test/products"


async def _close_resources(context: Any, browser: Any, playwright: Any) -> None:
    """Close every resource even if an earlier close fails or cancellation repeats."""
    async def close() -> None:
        try:
            if context is not None:
                await context.close()
        finally:
            try:
                if browser is not None:
                    await browser.close()
            finally:
                if playwright is not None:
                    await playwright.stop()

    cleanup = asyncio.create_task(close())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


class BrowserExecutionAgent:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def run(
        self,
        target_url: str,
        fields: list[str],
        task_type: str,
        emit: Callable[[str, int], Awaitable[None]],
    ) -> dict[str, Any]:
        # Import lazily so validation/reporting and their tests need no browser runtime.
        from playwright.async_api import async_playwright

        if target_url == DEMO_URL:
            html = (Path(__file__).parent / "fixtures" / "products.html").read_bytes()
            source_url = DEMO_URL
        else:
            from .safety import SafetyPolicy

            html, source_url = await SafetyPolicy(self.settings).fetch(target_url)
        if len(html) > self.settings.max_page_bytes:
            raise ValueError("The page exceeds the configured HTML size limit.")

        # HTTP requests omit fragments and use '/' for an empty path. Keep provenance intact.
        parsed = urlsplit(source_url)
        navigation_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
        playwright = browser = context = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=self.settings.headless)
            context = await browser.new_context(
                java_script_enabled=False,
                service_workers="block",
                accept_downloads=False,
            )
            context.set_default_timeout(self.settings.action_timeout_ms)
            page = await context.new_page()
            served = False

            async def route_request(route: Any) -> None:
                nonlocal served
                request = route.request
                if (
                    not served
                    and request.url == navigation_url
                    and request.is_navigation_request()
                    and request.frame == page.main_frame
                    and request.method == "GET"
                ):
                    served = True
                    await route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        headers={
                            "Content-Security-Policy": (
                                "default-src 'none'; script-src 'none'; "
                                "style-src 'unsafe-inline'; connect-src 'none'; "
                                "frame-src 'none'; form-action 'none'; base-uri 'none'"
                            ),
                            "X-Content-Type-Options": "nosniff",
                        },
                        body=html,
                    )
                else:
                    await route.abort("blockedbyclient")

            await context.route("**/*", route_request)
            await page.goto(
                navigation_url,
                wait_until="domcontentloaded",
                timeout=self.settings.action_timeout_ms,
            )
            if not served:
                raise ValueError("The browser could not load the supplied static page.")
            await emit("Loaded the static page with all external requests blocked.", 1)
            page_title = (await page.title()).strip()[:2000]
            await emit("Read the page title.", 2)
            raw_records = await ExtractionAgent().extract(
                page, fields, task_type=task_type, max_records=self.settings.max_records
            )
            await emit(f"Extracted {len(raw_records)} visible records.", 3)
            return {
                "page_title": page_title,
                "raw_records": raw_records,
                "source_url": source_url,
            }
        finally:
            await _close_resources(context, browser, playwright)
