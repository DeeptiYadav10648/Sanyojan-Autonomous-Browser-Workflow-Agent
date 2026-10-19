"""Visible, locator-only extraction from explicitly supported HTML profiles."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


MAX_FIELD_LENGTH = 2000


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value[:MAX_FIELD_LENGTH] or None


async def _visible_value(
    locator: Locator, *, preferred_attribute: str | None = None
) -> str | None:
    """Never read hidden elements, including hidden metadata or form inputs."""
    if not await locator.is_visible():
        return None
    if preferred_attribute:
        value = _text(await locator.get_attribute(preferred_attribute))
        if value is not None:
            return value
    value = _text(await locator.inner_text())
    if value is not None:
        return value
    for attribute in ("aria-label", "title", "value"):
        value = _text(await locator.get_attribute(attribute))
        if value is not None:
            return value
    return None


async def _first_value(locator: Locator, *, attribute: str | None = None) -> str | None:
    for index in range(await locator.count()):
        value = await _visible_value(locator.nth(index), preferred_attribute=attribute)
        if value is not None:
            return value
    return None


class ExtractionAgent:
    """Extract requested fields without executing page-supplied or generated JS."""

    async def extract(
        self,
        page: Page,
        fields: list[str],
        task_type: str = "extraction",
        max_records: int = 100,
    ) -> list[dict[str, str | None]]:
        if max_records < 1:
            raise ValueError("max_records must be positive.")
        if task_type in {"page_inspection", "inspection"}:
            body = page.locator("body")
            description = None
            if await body.count() and await body.nth(0).is_visible():
                description = _text(await body.nth(0).inner_text())
            values = {"title": _text(await page.title()), "description": description}
            return [{field: values.get(field) for field in fields}]
        if task_type not in {"extraction", "report"}:
            raise ValueError("Supported task types are extraction, report and page_inspection (inspection).")

        records = page.locator("[data-record]")
        profile = "demo"
        if not await records.count():
            if urlsplit(page.url).hostname == "books.toscrape.com":
                records = page.locator("article.product_pod")
                profile = "books"
            if not await records.count():
                records = page.locator("[itemtype*='schema.org/Product']")
                profile = "schema"

        extracted: list[dict[str, str | None]] = []
        for index in range(await records.count()):
            record = records.nth(index)
            if not await record.is_visible():
                continue
            if profile == "books":
                values = {
                    "title": await _first_value(record.locator("h3 a"), attribute="title"),
                    "price": await _first_value(record.locator(".price_color")),
                }
                data = {field: values.get(field) for field in fields}
            else:
                data = await self._attributes(record, fields, profile)
            # An empty product container does not constitute an extracted record.
            if not any(value is not None for value in data.values()):
                continue
            extracted.append(data)
            if len(extracted) >= max_records:
                break

        if not extracted:
            raise ValueError(
                "No supported visible product records with the requested fields were found. "
                "Use data-record fields, books.toscrape.com product listings, "
                "schema.org Product markup, or the page_inspection task type."
            )
        return extracted

    @staticmethod
    async def _attributes(
        record: Locator, fields: list[str], profile: str
    ) -> dict[str, str | None]:
        attribute = "data-field" if profile == "demo" else "itemprop"
        elements = record.locator(f"[{attribute}]")
        result: dict[str, str | None] = dict.fromkeys(fields)
        names = {
            field: ("name" if field == "title" and profile == "schema" else field)
            for field in fields
        }
        for index in range(await elements.count()):
            element = elements.nth(index)
            declared = await element.get_attribute(attribute) or ""
            declared_names = declared.split() if profile == "schema" else [declared]
            matching = [
                field for field, name in names.items()
                if name in declared_names and result[field] is None
            ]
            if matching:
                value = await _visible_value(element)
                for field in matching:
                    result[field] = value
        return result
