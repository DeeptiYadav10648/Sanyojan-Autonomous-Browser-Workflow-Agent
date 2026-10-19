"""Normalize extracted records while making missing and invalid values explicit."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


_CURRENCY_CODES = (
    r"USD|EUR|GBP|INR|JPY|CAD|AUD|NZD|CHF|CNY|HKD|SGD|SEK|NOK|DKK|"
    r"ZAR|BRL|MXN|KRW|AED|SAR"
)
_CURRENCY_SYMBOL = r"[$£€¥₹₩₽₺₫₴₪₱฿]"
_AMOUNT = re.compile(
    rf"(?:(?:{_CURRENCY_CODES})\s*)?(?:{_CURRENCY_SYMBOL}\s*)?"
    r"(?P<amount>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)"
    rf"(?:\s*{_CURRENCY_SYMBOL})?(?:\s*(?:{_CURRENCY_CODES}))?",
    re.IGNORECASE,
)


def _price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        amount = str(value)
    elif isinstance(value, str):
        match = _AMOUNT.fullmatch(" ".join(value.split()))
        if match is None:
            return None
        amount = match.group("amount").replace(",", "")
    else:
        return None
    try:
        decimal = Decimal(amount)
        if not decimal.is_finite() or decimal < 0:
            return None
        number = float(decimal)
        if not math.isfinite(number) or (decimal != 0 and number == 0):
            return None
        return number
    except (InvalidOperation, OverflowError, ValueError):
        return None


class ValidationAgent:
    def validate(
        self, raw_records: list[dict], fields: list[str], source_url: str
    ) -> dict[str, Any]:
        if not fields:
            raise ValueError("At least one requested field is required.")
        fields = list(dict.fromkeys(fields))
        extracted_at = datetime.now(timezone.utc).isoformat()
        records: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        summary: list[str] = []
        for raw in raw_records:
            data: dict[str, str | float | None] = {}
            warnings: list[str] = []
            for field in fields:
                value = raw.get(field)
                if isinstance(value, str):
                    value = " ".join(value.split()) or None
                if value is None:
                    data[field] = None
                    warnings.append(f"Missing required field: {field}.")
                elif field == "price":
                    data[field] = _price(value)
                    if data[field] is None:
                        warnings.append("Invalid price: expected a nonnegative decimal amount.")
                elif isinstance(value, str):
                    data[field] = value
                else:
                    data[field] = None
                    warnings.append(f"Invalid text value for field: {field}.")

            fingerprint = tuple(
                value.casefold() if isinstance(value, str) else value
                for value in data.values()
            )
            if fingerprint in seen:
                warnings.append("Duplicate record: matches an earlier normalized record.")
            seen.add(fingerprint)
            records.append({
                "data": data,
                "source_url": source_url,
                "extracted_at": extracted_at,
                "valid": not warnings,
                "warnings": warnings,
            })
            for warning in warnings:
                if warning not in summary:
                    summary.append(warning)

        if not records:
            summary.append("No records were extracted.")
        return {
            "records": records,
            "extracted_count": len(records),
            "valid_count": sum(record["valid"] for record in records),
            "warnings": summary,
        }
