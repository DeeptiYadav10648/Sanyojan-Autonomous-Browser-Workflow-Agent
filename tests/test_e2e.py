"""Offline end-to-end tests: real HTTP, real UI, real Chromium, real reports.

`npm test` builds the UI before these checks. Missing Chromium is a failure, not
a silent skip. The only local HTTP destination here is the app under test.
"""

import asyncio
import csv
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
DEMO = "https://demo.sanyojan.test/products"


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("live-sanyojan")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ,
           "SANYOJAN_DATABASE_PATH": str(directory / "tasks.sqlite3"),
           "SANYOJAN_REPORTS_DIR": str(directory / "reports"),
           "SANYOJAN_FRONTEND_DIR": str(ROOT / "frontend" / "dist"),
           "SANYOJAN_HEADLESS": "true"}
    assert (ROOT / "frontend" / "dist" / "index.html").is_file(), "Run npm run build before direct E2E invocation"
    with (directory / "server.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sanyojan.app:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(150):
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(f"Backend exited: {log.read()}")
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=1) as response:
                        health = json.load(response)
                        assert health["browser_available"], "Install Playwright Chromium before testing"
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                pytest.fail("Backend startup timed out")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def wait_terminal(request, base, identifier):
    for _ in range(200):
        response = await request.get(f"{base}/api/tasks/{identifier}")
        task = await response.json()
        if task["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError("Workflow did not finish within 20 seconds")


async def test_real_browser_api_demo_and_event_replay(live_server):
    async with async_playwright() as driver:
        request = await driver.request.new_context()
        try:
            response = await request.post(live_server + "/api/tasks/plan", data={
                "instruction": "Extract product titles, prices and categories and generate reports",
                "target_url": DEMO, "fields": ["title", "price", "category"],
            })
            assert response.status == 201
            planned = await response.json()
            assert planned["status"] == "PLANNED"
            identifier = planned["id"]
            response = await request.post(f"{live_server}/api/tasks/{identifier}/execute", data={"approved": True})
            assert response.status == 202
            task = await wait_terminal(request, live_server, identifier)
            assert task["status"] == "SUCCEEDED", task
            assert task["progress"] == 5
            result = task["result"]
            assert result["extracted_count"] == result["valid_count"] == 3
            assert all(row["source_url"] == DEMO for row in result["records"])
            assert all(isinstance(row["data"]["price"], (int, float)) for row in result["records"])
            csv_response = await request.get(live_server + result["reports"]["csv"])
            assert csv_response.status == 200
            rows = list(csv.DictReader(io.StringIO(await csv_response.text())))
            assert len(rows) == 3 and rows[0]["title"] == result["records"][0]["data"]["title"]
            json_response = await request.get(live_server + result["reports"]["json"])
            assert (await json_response.json())["result"]["records"] == result["records"]
            events_response = await request.get(f"{live_server}/api/tasks/{identifier}/events", headers={"Last-Event-ID": str(planned["logs"][-1]["id"])})
            events = [json.loads(line[6:]) for line in (await events_response.text()).splitlines() if line.startswith("data: ")]
            assert events and events[-1]["status"] == "SUCCEEDED"
            assert all(event["id"] > planned["logs"][-1]["id"] for event in events)
            assert any(event["progress"] == 3 for event in events)
        finally:
            await request.dispose()


async def test_dashboard_connected_workflow_and_mobile(live_server, tmp_path):
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
        page = await context.new_page()
        errors = []
        sse_urls = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: sse_urls.append(response.url) if response.headers.get("content-type", "").startswith("text/event-stream") else None)
        try:
            await page.goto(live_server)
            await expect(page.get_by_text("API connected", exact=True)).to_be_visible()
            await page.get_by_role("button", name="Demo catalog", exact=True).click()
            await page.get_by_role("button", name="Generate a plan").click()
            approval = page.get_by_role("button", name="Approve & execute")
            await expect(approval).to_be_visible()
            await expect(page.get_by_text("Awaiting approval", exact=True).first).to_be_visible()
            await approval.click()
            await expect(page.get_by_role("heading", name="Sanyojan Demo Products")).to_be_visible(timeout=20000)
            await expect(page.get_by_role("cell", name="Orbit Notebook", exact=True)).to_be_visible()
            await expect(page.get_by_role("cell", name="Valid", exact=True)).to_have_count(3)
            await expect(page.get_by_role("progressbar", name="Completed steps")).to_have_attribute("aria-valuenow", "5")
            async with page.expect_download() as download_info:
                await page.get_by_role("link", name="CSV", exact=True).click()
            download = await download_info.value
            await download.save_as(tmp_path / "ui-report.csv")
            assert "Orbit Notebook" in (tmp_path / "ui-report.csv").read_text(encoding="utf-8")
            await page.get_by_role("tab", name="Execution log").click()
            await expect(page.get_by_role("log", name="Execution log")).to_contain_text("Extracted 3 visible records")
            assert sse_urls, "The UI must subscribe to actual backend SSE"
            await page.reload()
            await expect(page.get_by_role("button", name="Open task", exact=False).first).to_be_visible()
            await page.get_by_role("button", name="Open task", exact=False).first.click()
            await expect(page.get_by_role("heading", name="Sanyojan Demo Products")).to_be_visible()
            await page.set_viewport_size({"width": 390, "height": 844})
            await expect(page.get_by_role("heading", name="Start with an intent")).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile viewport must not overflow horizontally"
            assert not errors, errors
        finally:
            await context.close()
            await browser.close()


async def test_real_browser_running_cancellation_and_inspection(live_server):
    async with async_playwright() as driver:
        request = await driver.request.new_context()
        try:
            response = await request.post(live_server + "/api/tasks/plan", data={"instruction": "Inspect the page title and description", "task_type": "inspection"})
            task_id = (await response.json())["id"]
            assert (await request.post(f"{live_server}/api/tasks/{task_id}/execute", data={"approved": True})).status == 202
            done = await wait_terminal(request, live_server, task_id)
            assert done["status"] == "SUCCEEDED", done
            assert done["result"]["extracted_count"] == 1
            assert done["result"]["records"][0]["data"]["title"] == "Sanyojan Demo Products"
            assert "Orbit Notebook" in done["result"]["records"][0]["data"]["description"]
            response = await request.post(live_server + "/api/tasks/plan", data={"instruction": "Extract product titles and prices"})
            task_id = (await response.json())["id"]
            await request.post(f"{live_server}/api/tasks/{task_id}/execute", data={"approved": True})
            response = await request.post(f"{live_server}/api/tasks/{task_id}/cancel")
            assert response.status == 200
            assert (await response.json())["status"] == "CANCELLED"
            assert (await (await request.post(f"{live_server}/api/tasks/{task_id}/cancel")).json())["status"] == "CANCELLED"
            assert (await request.get(f"{live_server}/api/tasks/{task_id}/reports/csv")).status == 409
        finally:
            await request.dispose()
