"""Explicit approval is the only path from a validated plan to browser execution."""

import asyncio
import logging
import time

from .browser import BrowserExecutionAgent
from .errors import AppError
from .planning import TaskUnderstandingAgent, WorkflowPlanningAgent
from .reporting import ReportingAgent
from .safety import SafetyPolicy
from .schemas import PlanInput, Result, Status, TERMINAL
from .validation import ValidationAgent

logger = logging.getLogger("sanyojan")


class Orchestrator:
    def __init__(self, settings, store, browser=None, adapter=None):
        self.settings = settings
        self.store = store
        self.understanding = TaskUnderstandingAgent(adapter)
        self.planning = WorkflowPlanningAgent(settings)
        self.safety = SafetyPolicy(settings)
        self.browser = browser or BrowserExecutionAgent(settings)
        self.validation = ValidationAgent()
        self.reporting = ReportingAgent(settings)
        self.workers: dict[str, asyncio.Task] = {}
        self.gate = asyncio.Lock()

    async def plan(self, request: PlanInput):
        # Validate policy before persistence to avoid storing embedded URL credentials.
        self.safety.validate_url(request.target_url)
        spec = await self.understanding.understand(request)
        task = self.store.create(request)
        try:
            self.store.update(task.id, "Understanding task and validating a bounded workflow.", "state", status=Status.PLANNING)
            plan = self.planning.plan(spec)
            await self.safety.validate_plan(plan, request.target_url)
            return self.store.update(task.id, "Plan ready. Explicit user approval is required.", "state",
                                     status=Status.PLANNED, plan=plan, current_action="Awaiting approval")
        except asyncio.CancelledError:
            self.store.update(task.id, "Planning interrupted.", "error", status=Status.FAILED, error="Planning interrupted.")
            raise
        except Exception:
            self.store.update(task.id, "Planning or safety validation failed; no browser ran.", "error", status=Status.FAILED,
                              error="Planning or safety validation failed.", current_action="Planning failed")
            raise

    async def execute(self, task_id: str):
        task = self.store.get(task_id)
        if task.status != Status.PLANNED or task.plan is None:
            raise AppError("INVALID_TRANSITION", "Only an approved PLANNED task can execute, exactly once.", 409)
        await self.safety.validate_plan(task.plan, task.target_url)
        async with self.gate:
            # DNS validation yields: cancellation or another execute may have won.
            task = self.store.get(task_id)
            if task.status != Status.PLANNED:
                raise AppError("ALREADY_EXECUTED", "This task is no longer awaiting approval.", 409)
            if len(self.workers) >= self.settings.max_concurrent_tasks:
                raise AppError("CAPACITY_REACHED", "All browser slots are busy. Retry after another task finishes.", 429)
            task = self.store.update(task_id, "Approval received. Starting read-only browser execution.", "state",
                                     status=Status.RUNNING, current_action="Loading approved page")
            worker = asyncio.create_task(self._run(task_id), name=f"workflow-{task_id}")
            self.workers[task_id] = worker
            worker.add_done_callback(lambda done: self.workers.pop(task_id, None))
            return task

    async def _run(self, task_id: str):
        started = time.monotonic()
        task = self.store.get(task_id)

        async def emit(message: str, progress: int):
            self.store.update(task_id, message, "step", progress=progress, current_action=message)
            # Real cancellation point between bounded synchronous stages.
            await asyncio.sleep(0)

        try:
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                browser_result = await self.browser.run(task.target_url, task.plan.fields, task.task_type, emit)
                await emit("Validating extracted data.", 3)
                validated = self.validation.validate(browser_result["raw_records"], task.plan.fields, browser_result["source_url"])
                await emit(f"Validation complete: {validated['valid_count']} of {validated['extracted_count']} records valid.", 4)
                for warning in validated["warnings"][:10]:
                    self.store.update(task_id, warning, "warning")
                result = Result(**validated, failed_steps=[], duration_seconds=round(time.monotonic() - started, 3),
                                reports={fmt: f"/api/tasks/{task_id}/reports/{fmt}" for fmt in ("csv", "json")},
                                page_title=browser_result["page_title"], fields=task.plan.fields)
                self.reporting.generate(task_id, result.model_dump())
                await emit("CSV and JSON reports written successfully.", 5)
                self.store.update(task_id, "Workflow completed. Review validation warnings before using data.", "state",
                                  status=Status.SUCCEEDED, result=result, current_action="Completed")
                logger.info("workflow_succeeded task_id=%s records=%d", task_id, result.extracted_count)
        except asyncio.CancelledError:
            if self.store.get(task_id).status not in TERMINAL:
                self.store.update(task_id, "Workflow cancelled; browser resources closed.", "state",
                                  status=Status.CANCELLED, current_action="Cancelled")
        except Exception as exc:
            # Never expose raw Playwright errors: these can contain page text or paths.
            message = exc.message if isinstance(exc, AppError) else (
                "Task duration limit exceeded." if isinstance(exc, TimeoutError) else
                "Browser or data pipeline failed. Check browser installation and supported static markup."
            )
            if isinstance(exc, ValueError):
                message = "No supported visible records, malformed data, or invalid report output. Try the bundled demo."
            current = self.store.get(task_id)
            failure = Result(records=[], extracted_count=0, valid_count=0, warnings=[],
                             failed_steps=[min(current.progress + 1, 5)], duration_seconds=round(time.monotonic() - started, 3),
                             reports={}, page_title="", fields=task.plan.fields)
            self.store.update(task_id, message, "error", status=Status.FAILED, error=message, result=failure, current_action="Failed")
            logger.warning("workflow_failed task_id=%s error_type=%s", task_id, type(exc).__name__)

    async def cancel(self, task_id: str):
        task = self.store.get(task_id)
        if task.status in TERMINAL:
            return task
        worker = self.workers.get(task_id)
        if worker is not None:
            # Do not cancel repeatedly while Playwright's shielded teardown runs.
            if not worker.cancelling():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        task = self.store.get(task_id)
        if task.status not in TERMINAL:  # Includes a worker cancelled before its first tick.
            task = self.store.update(task_id, "Task cancelled. No further actions will run.", "state",
                                     status=Status.CANCELLED, current_action="Cancelled")
        return task

    async def shutdown(self):
        for task_id in list(self.workers):
            await self.cancel(task_id)
