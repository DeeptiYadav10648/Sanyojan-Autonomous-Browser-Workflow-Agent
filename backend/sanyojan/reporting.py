"""Atomic JSON/CSV reports scoped to canonical task IDs in one report directory."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


_FORMULA_PREFIX = re.compile(r"^\s*[=+\-@\t\r]")
_METADATA_COLUMNS = ("source_url", "extracted_at", "valid", "warnings")


def _csv_cell(value: Any) -> Any:
    if isinstance(value, str) and _FORMULA_PREFIX.match(value):
        return "'" + value
    return value


class ReportingAgent:
    def __init__(self, settings: Any) -> None:
        self._configured_dir = Path(settings.reports_dir).absolute()
        if self._configured_dir.is_symlink():
            raise ValueError("The reports directory must not be a symbolic link.")
        self.reports_dir = self._configured_dir.resolve()

    def path_for(self, task_id: str, format: str) -> Path:
        try:
            canonical = str(UUID(task_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("A canonical UUID task ID is required.") from exc
        if canonical != task_id:
            raise ValueError("A canonical UUID task ID is required.")
        if format not in {"csv", "json"}:
            raise ValueError("Report format must be csv or json.")
        if (self._configured_dir.is_symlink()
                or self._configured_dir.resolve() != self.reports_dir):
            raise ValueError("The reports directory cannot be redirected.")
        candidate = self.reports_dir / f"{task_id}.{format}"
        if candidate.is_symlink() or candidate.resolve().parent != self.reports_dir:
            raise ValueError("Report paths must remain inside the reports directory.")
        return candidate

    def generate(self, task_id: str, result: dict) -> dict[str, str]:
        # Validate before creating directories or serializing potentially large results.
        self.path_for(task_id, "json")
        self.path_for(task_id, "csv")
        payload = {
            "schema_version": 1,
            "metadata": {
                "task_id": task_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "result": result,
        }
        json_text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        csv_text = self._csv(result)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._write_atomic(task_id, "json", json_text)
        self._write_atomic(task_id, "csv", csv_text)
        return {
            "csv": f"/api/tasks/{task_id}/reports/csv",
            "json": f"/api/tasks/{task_id}/reports/json",
        }

    @staticmethod
    def _csv(result: dict) -> str:
        records = result.get("records", [])
        requested = result.get("fields", [])
        if not isinstance(requested, list) or any(not isinstance(field, str) for field in requested):
            raise ValueError("Report fields must be a list of field names.")
        fields = list(dict.fromkeys(requested))
        for record in records:
            for field in record["data"]:
                if not isinstance(field, str):
                    raise ValueError("Report data keys must be field names.")
                if field not in fields:
                    fields.append(field)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow([_csv_cell(field) for field in [*fields, *_METADATA_COLUMNS]])
        for record in records:
            data = record["data"]
            values = [data.get(field) for field in fields]
            values.extend([
                record.get("source_url"),
                record.get("extracted_at"),
                record.get("valid"),
                "; ".join(record.get("warnings", [])),
            ])
            writer.writerow([_csv_cell(value) for value in values])
        return stream.getvalue()

    def _write_atomic(self, task_id: str, format: str, text: str) -> None:
        self.path_for(task_id, format)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=f".{task_id}.",
                suffix=".tmp",
                dir=self.reports_dir,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path_for(task_id, format))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
