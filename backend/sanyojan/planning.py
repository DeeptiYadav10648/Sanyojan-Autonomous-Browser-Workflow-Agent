"""Bounded interpretation behind an injectable, provider-neutral interface."""

import asyncio
import re
from typing import Protocol

from pydantic import ValidationError

from .errors import AppError
from .schemas import DEMO_URL, Plan, PlanInput, PlanStep, TaskSpec


class UnderstandingAdapter(Protocol):
    async def understand(self, request: PlanInput) -> TaskSpec:
        """Return a strictly validated specification, never executable instructions."""
        ...


class DeterministicAdapter:
    """Small, transparent fallback grammar. No AI provider or credentials are used."""

    async def understand(self, request: PlanInput) -> TaskSpec:
        text = request.instruction.lower()
        forbidden = (
            r"\b(passwords?|credentials?|cookies?|tokens?|captcha|paywalls?|bypass|evade|"
            r"log\s?in|sign\s?in|purchase|buy|checkout|payments?|delete|upload|submit|"
            r"click|fill|shell|javascript|scripts?|execute|email|phone|address|"
            r"ignore|override|crawl|pagination|paginate|all pages)\b"
        )
        if re.search(forbidden, text):
            raise AppError("UNSUPPORTED_TASK", "Only read-only public page inspection and listing extraction are supported.")
        if not re.search(r"\b(extract|inspect|read|list|listing|report|products?|books?|title|prices?)\b", text):
            raise AppError("AMBIGUOUS_TASK", "Use a supported instruction, such as 'Extract product titles and prices and generate CSV and JSON reports'.")
        urls = re.findall(r"https?://[^\s<>\"']+", request.instruction)
        if any(url.rstrip(".,)") != request.target_url for url in urls):
            raise AppError("AMBIGUOUS_URL", "Instruction URLs must match the explicit target URL. Only one page is supported.")
        if re.search(r"\b(first|last|top|sort|filter|cheapest|expensive|compare|convert)\b|\b\d+\s+(records?|products?|books?|items?)\b", text):
            raise AppError("UNSUPPORTED_CONSTRAINT", "Sorting, filtering, cross-page comparison, conversion, and custom record counts are not supported.")
        fields = request.fields
        if fields is None:
            if request.task_type == "inspection":
                fields = ["title", "description"]
            else:
                names = {"title": r"\b(titles?|names?)\b", "price": r"\bprices?\b", "category": r"\bcategor(y|ies)\b", "description": r"\bdescriptions?\b"}
                fields = [field for field, pattern in names.items() if re.search(pattern, text)] or ["title", "price"]
        if request.task_type == "inspection" and not set(fields) <= {"title", "description"}:
            raise AppError("UNSUPPORTED_FIELDS", "Page inspection supports title and description only.")
        return TaskSpec(
            objective=f"Read-only {request.task_type} of one approved static page",
            target_url=request.target_url, task_type=request.task_type, fields=fields,
            constraints=["No scripts or side effects", "One page only", "Missing values require review"],
        )


class TaskUnderstandingAgent:
    def __init__(self, adapter: UnderstandingAdapter | None = None):
        self.adapter = adapter or DeterministicAdapter()

    async def understand(self, request: PlanInput) -> TaskSpec:
        try:
            output = await asyncio.wait_for(self.adapter.understand(request), timeout=20)
            # Revalidate even objects returned by adapters; never trust a model's output.
            spec = TaskSpec.model_validate(output.model_dump() if isinstance(output, TaskSpec) else output)
        except (ValidationError, asyncio.TimeoutError) as exc:
            raise AppError("PLANNER_RESPONSE_INVALID", "The planning adapter failed or returned an invalid specification.", 502) from exc
        if spec.target_url != request.target_url or spec.task_type != request.task_type:
            raise AppError("PLANNER_POLICY", "The adapter cannot change the approved URL or task type.", 400)
        return spec


class WorkflowPlanningAgent:
    def __init__(self, settings):
        self.settings = settings

    def plan(self, spec: TaskSpec) -> Plan:
        details = [
            ("navigate", spec.target_url, "One bounded static HTML page loaded", "Public DNS/IP and every redirect validated; scripts and subrequests blocked"),
            ("inspect_title", "Page title", "Actual title read", "Read page.title() after successful navigation"),
            ("extract", ", ".join(spec.fields), f"At most {self.settings.max_records} visible records", "Only supported visible DOM profiles; never invent missing fields"),
            ("validate", "Extracted records", "Valid and review-needed records separated", "Required fields, normalized prices, duplicate checks"),
            ("report", "Task-scoped CSV and JSON", "Two real downloadable files", "Canonical UUID paths and CSV formula escaping"),
        ]
        warnings = [
            "Deterministic fallback planner — no live AI model is connected.",
            "Single-page, static HTML only. All requested fields are required; missing values are flagged, not invented.",
            "Reports contain untrusted public page data. Review warnings before using results.",
        ]
        if spec.target_url == DEMO_URL:
            warnings.append("Bundled demo data: this URL is intercepted locally, not fetched from the internet.")
        return Plan(summary=spec.objective, fields=spec.fields, warnings=warnings, steps=[
            PlanStep(number=i, action=action, target=target, expected_result=expected,
                     validation_rule=rule, timeout_ms=self.settings.action_timeout_ms)
            for i, (action, target, expected, rule) in enumerate(details, 1)
        ])
