"""Drive the actual dashboard and save local screenshots; requires running app."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")


async def main(base):
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1050})
        try:
            await page.goto(base)
            await expect(page.get_by_text("API connected", exact=True)).to_be_visible()
            await page.get_by_role("button", name="Demo catalog", exact=True).click()
            await page.get_by_role("button", name="Generate a plan").click()
            await page.get_by_role("button", name="Approve & execute").click()
            await expect(page.get_by_role("cell", name="Orbit Notebook", exact=True)).to_be_visible(timeout=25000)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.screenshot(path=output / "demo-desktop.png", full_page=True)
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=output / "demo-mobile.png", full_page=True)
            metrics = await page.evaluate("({innerWidth,scrollWidth:document.documentElement.scrollWidth,body:document.body.scrollWidth,scrollX,scrollY})")
            assert metrics["scrollWidth"] <= metrics["innerWidth"], "The mobile layout overflows the viewport"
            print(json.dumps({"demo": "SUCCEEDED", "screenshots": str(output), "mobile_fits": True, "mobile_metrics": metrics}, indent=2))
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    asyncio.run(main(parser.parse_args().url))
