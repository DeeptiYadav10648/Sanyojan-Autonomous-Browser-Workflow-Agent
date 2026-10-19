"""Offline contract tests for the browser, extraction, validation and report agents.

Run from the repository root: python -B -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from backend.sanyojan.browser import BrowserExecutionAgent, DEMO_URL
from backend.sanyojan.extraction import ExtractionAgent
from backend.sanyojan.reporting import ReportingAgent
from backend.sanyojan.validation import ValidationAgent


def _collection(*nodes):
    return SimpleNamespace(count=AsyncMock(return_value=len(nodes)), nth=lambda index: nodes[index])


def _node(text="", *, visible=True, attrs=None, children=None):
    attrs = attrs or {}
    children = children or {}
    return SimpleNamespace(
        is_visible=AsyncMock(return_value=visible),
        inner_text=AsyncMock(return_value=text),
        get_attribute=AsyncMock(side_effect=attrs.get),
        locator=Mock(side_effect=lambda selector: children.get(selector, _collection())),
    )


def _page(selectors, url=DEMO_URL, title="Catalog"):
    return SimpleNamespace(
        url=url,
        title=AsyncMock(return_value=title),
        locator=Mock(side_effect=lambda selector: selectors.get(selector, _collection())),
    )


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_frontend_report_and_inspection_task_names(self):
        record = _node(children={"[data-field]": _collection(
            _node("Product", attrs={"data-field": "title"})
        )})
        page = _page({"[data-record]": _collection(record), "body": _collection(_node("Visible body"))})
        rows = await ExtractionAgent().extract(page, ["title"], "report")
        self.assertEqual(rows, [{"title": "Product"}])
        rows = await ExtractionAgent().extract(page, ["title", "description"], "inspection")
        self.assertEqual(rows, [{"title": "Catalog", "description": "Visible body"}])

    async def test_demo_skips_hidden_records_and_fields_and_caps_values(self):
        hidden = _node(visible=False)
        title = _node("  Visible\nproduct ", attrs={"data-field": "title"})
        price = _node("$99.00", visible=False, attrs={"data-field": "price"})
        description = _node("x" * 2500, attrs={"data-field": "description"})
        visible = _node(children={"[data-field]": _collection(title, price, description)})
        page = _page({"[data-record]": _collection(hidden, visible, visible)})
        rows = await ExtractionAgent().extract(
            page, ["title", "price", "description", "category"], max_records=1
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Visible product")
        self.assertIsNone(rows[0]["price"])
        self.assertIsNone(rows[0]["category"])
        self.assertEqual(len(rows[0]["description"]), 2000)
        price.inner_text.assert_not_awaited()

    async def test_books_full_title_and_missing_values(self):
        title = _node("Short…", attrs={"title": "The full unabridged book title"})
        record = _node(children={
            "h3 a": _collection(title), ".price_color": _collection(_node("£51.77"))
        })
        page = _page({"article.product_pod": _collection(record)},
                     url="https://books.toscrape.com/catalogue/page-1.html")
        rows = await ExtractionAgent().extract(page, ["title", "price", "category", "description"])
        self.assertEqual(rows, [{"title": "The full unabridged book title", "price": "£51.77",
                                 "category": None, "description": None}])

    async def test_schema_visible_text_and_attributes_but_not_hidden_metadata(self):
        name = _node("", attrs={"itemprop": "name", "title": "Actual product"})
        hidden_price = _node("", visible=False, attrs={"itemprop": "price", "content": "0.00"})
        price = _node("", attrs={"itemprop": "price", "aria-label": "USD 8.50"})
        hidden_description = _node("Secret", visible=False, attrs={"itemprop": "description"})
        record = _node(children={"[itemprop]": _collection(name, hidden_price, price, hidden_description)})
        page = _page({"[itemtype*='schema.org/Product']": _collection(record)})
        rows = await ExtractionAgent().extract(page, ["title", "price", "description"])
        self.assertEqual(rows, [{"title": "Actual product", "price": "USD 8.50", "description": None}])
        hidden_price.get_attribute.assert_awaited_once_with("itemprop")

    async def test_inspection_respects_requested_fields_and_length(self):
        page = _page({"body": _collection(_node("body " * 1000))}, title="  A title  ")
        rows = await ExtractionAgent().extract(page, ["description", "price"], "page_inspection")
        self.assertEqual(set(rows[0]), {"description", "price"})
        self.assertEqual(len(rows[0]["description"]), 2000)
        self.assertIsNone(rows[0]["price"])

    async def test_inspection_does_not_replace_empty_body_text_with_attributes(self):
        page = _page({"body": _collection(_node("", attrs={"aria-label": "Not body text"}))})
        rows = await ExtractionAgent().extract(page, ["title", "description"], "page_inspection")
        self.assertEqual(rows, [{"title": "Catalog", "description": None}])

    async def test_unsupported_or_hidden_only_page_is_helpful(self):
        for selectors in ({}, {"[data-record]": _collection(_node(visible=False))}):
            with self.subTest(selectors=bool(selectors)):
                with self.assertRaisesRegex(ValueError, "No supported visible product"):
                    await ExtractionAgent().extract(_page(selectors), ["title"])


class _BrowserHarness:
    """A driver double that actually invokes the installed network route handler."""

    def __init__(self, final_url="https://example.com/final"):
        self.events = []
        self.routes = []
        self.handler = None
        self.final_url = final_url
        self.page = SimpleNamespace(main_frame=object(), title=AsyncMock(side_effect=self.title))
        self.page.goto = AsyncMock(side_effect=self.navigate)
        self.context = SimpleNamespace(
            new_page=AsyncMock(return_value=self.page),
            route=AsyncMock(side_effect=self.install_route),
            set_default_timeout=Mock(), close=AsyncMock(),
        )
        self.browser = SimpleNamespace(new_context=AsyncMock(return_value=self.context), close=AsyncMock())
        self.playwright = SimpleNamespace(
            chromium=SimpleNamespace(launch=AsyncMock(return_value=self.browser)), stop=AsyncMock()
        )
        self.manager = SimpleNamespace(start=AsyncMock(return_value=self.playwright))
        self.api_module = types.ModuleType("playwright.async_api")
        self.api_module.async_playwright = Mock(return_value=self.manager)
        self.safety_module = types.ModuleType("backend.sanyojan.safety")
        self.fetch = AsyncMock(return_value=(b"<html><title>Fetched</title></html>", final_url))
        self.safety_module.SafetyPolicy = Mock(return_value=SimpleNamespace(fetch=self.fetch))
        self.settings = SimpleNamespace(
            headless=True, max_records=7, max_page_bytes=100_000, action_timeout_ms=250,
            allowed_domains=[], max_redirects=3,
        )

    async def install_route(self, pattern, handler):
        self.handler = handler

    async def request(self, url, *, navigation=True, main_frame=True, method="GET"):
        route = SimpleNamespace(
            request=SimpleNamespace(
                url=url, is_navigation_request=lambda: navigation,
                frame=self.page.main_frame if main_frame else object(), method=method,
            ),
            fulfill=AsyncMock(), abort=AsyncMock(),
        )
        self.routes.append(route)
        await self.handler(route)
        return route

    async def navigate(self, url, **kwargs):
        await self.request(url)
        self.events.append("navigation")

    async def title(self):
        self.events.append("title")
        return "Fetched title"

    async def extract(self, *args, **kwargs):
        self.events.append("extraction")
        return [{"title": "Actual record", "price": None}]

    async def emit(self, message, progress):
        self.events.append(("emit", progress))

    def patches(self):
        return patch.dict(sys.modules, {
            "playwright": types.ModuleType("playwright"),
            "playwright.async_api": self.api_module,
            "backend.sanyojan.safety": self.safety_module,
        })


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_http_path_and_fragment_are_normalized_only_for_navigation(self):
        driver = _BrowserHarness(final_url="https://example.com#section")
        with driver.patches(), patch.object(ExtractionAgent, "extract", side_effect=driver.extract):
            result = await BrowserExecutionAgent(driver.settings).run(
                "https://example.com", ["title"], "extraction", driver.emit
            )
        self.assertEqual(driver.page.goto.await_args.args[0], "https://example.com/")
        self.assertEqual(result["source_url"], "https://example.com#section")

    async def test_only_exact_demo_url_uses_the_fixture(self):
        for target in (DEMO_URL, DEMO_URL + "?query=1", DEMO_URL + "/", DEMO_URL + "#fragment"):
            with self.subTest(target=target):
                driver = _BrowserHarness()
                with driver.patches(), patch.object(ExtractionAgent, "extract", side_effect=driver.extract):
                    result = await BrowserExecutionAgent(driver.settings).run(
                        target, ["title"], "extraction", driver.emit
                    )
                if target == DEMO_URL:
                    driver.fetch.assert_not_awaited()
                    self.assertEqual(result["source_url"], DEMO_URL)
                    self.assertIn(b"Orbit Notebook", driver.routes[0].fulfill.await_args.kwargs["body"])
                else:
                    driver.fetch.assert_awaited_once_with(target)
                    self.assertEqual(result["source_url"], driver.final_url)

    async def test_page_byte_limit_is_enforced_before_launch(self):
        driver = _BrowserHarness()
        driver.settings.max_page_bytes = 1
        with driver.patches():
            with self.assertRaisesRegex(ValueError, "size limit"):
                await BrowserExecutionAgent(driver.settings).run(
                    DEMO_URL, ["title"], "extraction", driver.emit
                )
        driver.manager.start.assert_not_awaited()

    async def test_cancellation_during_navigation_closes_every_resource(self):
        driver = _BrowserHarness()
        navigating = asyncio.Event()

        async def blocked_navigation(*args, **kwargs):
            navigating.set()
            await asyncio.Event().wait()

        driver.page.goto.side_effect = blocked_navigation
        with driver.patches():
            task = asyncio.create_task(BrowserExecutionAgent(driver.settings).run(
                DEMO_URL, ["title"], "extraction", driver.emit
            ))
            await asyncio.wait_for(navigating.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        driver.context.close.assert_awaited_once()
        driver.browser.close.assert_awaited_once()
        driver.playwright.stop.assert_awaited_once()

    async def test_repeated_cancellation_waits_for_cleanup(self):
        driver = _BrowserHarness()
        navigating = asyncio.Event()
        closing = asyncio.Event()
        allow_close = asyncio.Event()

        async def blocked_navigation(*args, **kwargs):
            navigating.set()
            await asyncio.Event().wait()

        async def blocked_close():
            closing.set()
            await allow_close.wait()

        driver.page.goto.side_effect = blocked_navigation
        driver.context.close.side_effect = blocked_close
        with driver.patches():
            task = asyncio.create_task(BrowserExecutionAgent(driver.settings).run(
                DEMO_URL, ["title"], "extraction", driver.emit
            ))
            await asyncio.wait_for(navigating.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(closing.wait(), timeout=2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            allow_close.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        driver.browser.close.assert_awaited_once()
        driver.playwright.stop.assert_awaited_once()

    async def test_orchestrator_wait_for_timeout_cleans_up(self):
        driver = _BrowserHarness()

        async def blocked_navigation(*args, **kwargs):
            await asyncio.Event().wait()

        driver.page.goto.side_effect = blocked_navigation
        with driver.patches():
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(BrowserExecutionAgent(driver.settings).run(
                    DEMO_URL, ["title"], "extraction", driver.emit
                ), timeout=0.05)
        driver.context.close.assert_awaited_once()
        driver.browser.close.assert_awaited_once()
        driver.playwright.stop.assert_awaited_once()

    async def test_extraction_failure_still_cleans_up(self):
        driver = _BrowserHarness()
        with driver.patches(), patch.object(ExtractionAgent, "extract", side_effect=ValueError("Unsupported page")):
            with self.assertRaisesRegex(ValueError, "Unsupported page"):
                await BrowserExecutionAgent(driver.settings).run(
                    DEMO_URL, ["title"], "extraction", driver.emit
                )
        driver.context.close.assert_awaited_once()
        driver.browser.close.assert_awaited_once()
        driver.playwright.stop.assert_awaited_once()

    async def test_safe_fetch_final_provenance_progress_and_cleanup(self):
        driver = _BrowserHarness()
        with driver.patches(), patch.object(ExtractionAgent, "extract", side_effect=driver.extract) as extract:
            result = await BrowserExecutionAgent(driver.settings).run(
                "https://example.com/start", ["title", "price"], "extraction", driver.emit
            )
        driver.fetch.assert_awaited_once_with("https://example.com/start")
        driver.page.goto.assert_awaited_once_with(
            driver.final_url, wait_until="domcontentloaded", timeout=250
        )
        driver.playwright.chromium.launch.assert_awaited_once_with(headless=True)
        driver.browser.new_context.assert_awaited_once_with(
            java_script_enabled=False, service_workers="block", accept_downloads=False
        )
        extract.assert_awaited_once_with(driver.page, ["title", "price"], task_type="extraction", max_records=7)
        self.assertEqual(result["source_url"], driver.final_url)
        self.assertEqual(result["page_title"], "Fetched title")
        self.assertEqual(driver.events,
                         ["navigation", ("emit", 1), "title", ("emit", 2), "extraction", ("emit", 3)])
        driver.context.close.assert_awaited_once()
        driver.browser.close.assert_awaited_once()
        driver.playwright.stop.assert_awaited_once()

    async def test_only_one_exact_main_navigation_get_is_fulfilled(self):
        driver = _BrowserHarness()
        allowed = []

        async def navigate(url, **kwargs):
            for requested, options in (
                ("https://other.example/redirect", {}),
                (url, {"navigation": False}), (url, {"main_frame": False}),
                (url, {"method": "POST"}), (url, {}), (url, {}),
                ("http://127.0.0.1/", {}),
            ):
                allowed.append(await driver.request(requested, **options))

        driver.page.goto.side_effect = navigate
        with driver.patches(), patch.object(ExtractionAgent, "extract", side_effect=driver.extract):
            await BrowserExecutionAgent(driver.settings).run(
                "https://example.com/start", ["title"], "extraction", driver.emit
            )
        for index, route in enumerate(allowed):
            if index == 4:
                route.fulfill.assert_awaited_once()
                route.abort.assert_not_awaited()
                self.assertEqual(route.fulfill.await_args.kwargs["body"], driver.fetch.return_value[0])
            else:
                route.abort.assert_awaited_once_with("blockedbyclient")
                route.fulfill.assert_not_awaited()


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "reports"
        self.agent = ReportingAgent(SimpleNamespace(reports_dir=self.root))
        self.task_id = str(uuid4())

    def result(self):
        return {"fields": ["title", "price"], "records": [{
            "data": {"title": "=HYPERLINK(\"https://example.com\")", "price": 12.5},
            "source_url": "  @unsafe", "extracted_at": "\tdate", "valid": False,
            "warnings": [" \rwarning"],
        }]}

    def test_round_trip_preserves_json_and_protects_every_csv_column(self):
        result = self.result()
        links = self.agent.generate(self.task_id, result)
        self.assertEqual(links, {
            "csv": f"/api/tasks/{self.task_id}/reports/csv",
            "json": f"/api/tasks/{self.task_id}/reports/json",
        })
        payload = json.loads(self.agent.path_for(self.task_id, "json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["metadata"]["task_id"], self.task_id)
        self.assertEqual(payload["result"], result)
        with self.agent.path_for(self.task_id, "csv").open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["title"], "'" + result["records"][0]["data"]["title"])
        self.assertEqual(row["price"], "12.5")
        self.assertTrue(row["source_url"].startswith("'"))
        self.assertTrue(row["extracted_at"].startswith("'"))
        self.assertTrue(row["warnings"].startswith("'"))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_all_formula_prefixes_and_headers_are_escaped(self):
        for value in ("=1", "+1", "-1", "@SUM(A1)", "\ttext", "\rtext",
                      " \ttext", "  =1", "\n@SUM(A1)", " \rtext"):
            with self.subTest(value=repr(value)):
                report = {"records": [{"data": {value: value}, "warnings": []}]}
                rows = list(csv.reader(io.StringIO(self.agent._csv(report))))
                self.assertEqual(rows[0][0], "'" + value)
                self.assertEqual(rows[1][0], "'" + value)

    def test_bad_task_ids_and_formats_create_nothing(self):
        for task_id in ("../escape", self.task_id.upper(), self.task_id.replace("-", ""),
                        "{" + self.task_id + "}", "", None):
            with self.subTest(task_id=task_id):
                with self.assertRaises(ValueError):
                    self.agent.generate(task_id, self.result())
        for format in ("CSV", "../json", "html", "json/other"):
            with self.assertRaises(ValueError):
                self.agent.path_for(self.task_id, format)
        self.assertFalse(self.root.exists())

    def test_symbolic_link_destinations_and_root_are_rejected(self):
        self.root.mkdir()
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("untouched", encoding="utf-8")
        destination = self.root / f"{self.task_id}.json"
        try:
            destination.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is unavailable on this platform.")
        with self.assertRaises(ValueError):
            self.agent.generate(self.task_id, self.result())
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
        root_link = Path(self.temporary.name) / "reports-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            ReportingAgent(SimpleNamespace(reports_dir=root_link))

    def test_failed_atomic_replace_keeps_previous_report_and_removes_temporary_file(self):
        self.agent.generate(self.task_id, self.result())
        destination = self.agent.path_for(self.task_id, "json")
        previous = destination.read_bytes()
        with patch("backend.sanyojan.reporting.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.agent.generate(self.task_id, {"records": []})
        self.assertEqual(destination.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_empty_report_keeps_requested_headers(self):
        self.agent.generate(self.task_id, {"fields": ["title", "price"], "records": []})
        text = self.agent.path_for(self.task_id, "csv").read_text(encoding="utf-8")
        self.assertEqual(next(csv.reader(io.StringIO(text))),
                         ["title", "price", "source_url", "extracted_at", "valid", "warnings"])


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = ValidationAgent()
        self.source = "https://example.com/products"

    def test_normalization_missing_values_duplicates_and_provenance(self) -> None:
        result = self.agent.validate(
            [
                {"title": "  A\n book  ", "price": "GBP 1,234.50", "category": "Books"},
                {"title": "A book", "price": 1234.5, "category": "Books"},
                {"title": "Other", "price": None},
            ],
            ["title", "price", "category"],
            self.source,
        )
        self.assertEqual(result["extracted_count"], 3)
        self.assertEqual(result["valid_count"], 1)
        first, duplicate, missing = result["records"]
        self.assertEqual(first["data"], {"title": "A book", "price": 1234.5, "category": "Books"})
        self.assertEqual(first["source_url"], self.source)
        self.assertTrue(first["extracted_at"].endswith("+00:00"))
        self.assertIn("Duplicate", duplicate["warnings"][0])
        self.assertIsNone(missing["data"]["price"])
        self.assertIsNone(missing["data"]["category"])
        self.assertTrue(any("category" in warning for warning in missing["warnings"]))

    def test_prices_do_not_invent_defaults_or_strip_arbitrary_text(self) -> None:
        accepted = {"$0.00": 0.0, "€ 12.50": 12.5, "INR 999": 999.0, ".50 GBP": 0.5}
        for value, expected in accepted.items():
            with self.subTest(value=value):
                result = self.agent.validate([{"price": value}], ["price"], self.source)
                self.assertEqual(result["records"][0]["data"]["price"], expected)
                self.assertEqual(result["valid_count"], 1)
        for value in (None, "", "free", "from $12", "12,50", "1.234,50", "-1", -1,
                      True, float("nan"), float("inf"), "12-34", "USD", "1e3"):
            with self.subTest(value=value):
                record = self.agent.validate([{"price": value}], ["price"], self.source)["records"][0]
                self.assertIsNone(record["data"]["price"])
                self.assertFalse(record["valid"])
                self.assertTrue(record["warnings"])

    def test_empty_input_is_explicit(self) -> None:
        result = self.agent.validate([], ["title"], self.source)
        self.assertEqual(result["extracted_count"], 0)
        self.assertEqual(result["valid_count"], 0)
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
