"""FastAPI application, local-first API and optional built dashboard hosting."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .errors import AppError
from .middleware import RequestLimits
from .orchestrator import Orchestrator
from .schemas import Approval, Health, PlanInput, Result, Status, Task, TERMINAL
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def browser_ready() -> bool:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(headless=True)
            await browser.close()
        return True
    except Exception:
        return False


def create_app(settings: Settings | None = None, browser=None, adapter=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.database_path, settings.max_stored_tasks)
        store.recover()
        app.state.store = store
        app.state.orchestrator = Orchestrator(settings, store, browser, adapter)
        app.state.browser_available = bool(browser) or await browser_ready()
        try:
            yield
        finally:
            await app.state.orchestrator.shutdown()
            store.close()

    app = FastAPI(title="Sanyojan", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(RequestLimits, settings=settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Last-Event-ID"], allow_credentials=False)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo raw user inputs (which might contain accidental private data).
        details = [{"field": ".".join(map(str, error["loc"])), "message": error["msg"]} for error in exc.errors()]
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "Invalid request. Check the indicated fields.", "details": details}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"code": "HTTP_ERROR", "message": "Route not found." if exc.status_code == 404 else "Request not permitted."}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger("sanyojan").error("request_failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred. No successful result is implied."}}, status_code=500)

    @app.get("/api/health", response_model=Health)
    async def health():
        return Health(browser_available=app.state.browser_available)

    @app.post("/api/tasks/plan", response_model=Task, status_code=201)
    async def plan(request: PlanInput):
        return await app.state.orchestrator.plan(request)

    @app.get("/api/tasks", response_model=list[Task])
    async def history(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
        return app.state.store.list(limit, offset)

    @app.get("/api/tasks/{task_id}", response_model=Task)
    async def task(task_id: UUID):
        return app.state.store.get(str(task_id))

    @app.post("/api/tasks/{task_id}/execute", response_model=Task, status_code=202)
    async def execute(task_id: UUID, approval: Approval):
        if not app.state.browser_available:
            raise AppError("BROWSER_UNAVAILABLE", "Chromium could not start. Install Playwright's Chromium and system dependencies.", 503)
        return await app.state.orchestrator.execute(str(task_id))

    @app.post("/api/tasks/{task_id}/cancel", response_model=Task)
    async def cancel(task_id: UUID):
        return await app.state.orchestrator.cancel(str(task_id))

    @app.get("/api/tasks/{task_id}/results", response_model=Result)
    async def results(task_id: UUID):
        snapshot = app.state.store.get(str(task_id))
        if snapshot.result is None:
            raise AppError("RESULTS_NOT_READY", "This task has no results yet.", 409)
        return snapshot.result

    @app.get("/api/tasks/{task_id}/reports/{format}")
    async def report(task_id: UUID, format: str):
        if format not in {"csv", "json"}:
            raise AppError("REPORT_FORMAT", "Report format must be csv or json.", 400)
        snapshot = app.state.store.get(str(task_id))
        if snapshot.status != Status.SUCCEEDED:
            raise AppError("REPORT_NOT_READY", "Only successful tasks have downloadable reports.", 409)
        path = app.state.orchestrator.reporting.path_for(str(task_id), format)
        if not path.is_file():
            raise AppError("REPORT_NOT_FOUND", "The report was removed from local storage.", 404)
        return FileResponse(path, filename=f"sanyojan-{task_id}.{format}", media_type="text/csv" if format == "csv" else "application/json")

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: UUID, request: Request, after: int = Query(0, ge=0)):
        identifier = str(task_id)
        app.state.store.get(identifier)
        last_id = request.headers.get("last-event-id", str(after))
        if not last_id.isdigit() or len(last_id) > 18:
            raise AppError("EVENT_ID_INVALID", "Last-Event-ID must be a nonnegative integer.")

        async def stream():
            cursor = int(last_id)
            yield "retry: 2000\n\n"
            heartbeat = 0
            while not await request.is_disconnected():
                batch = app.state.store.events(identifier, cursor)
                for event in batch:
                    cursor = event.id
                    yield f"id: {event.id}\nevent: progress\ndata: {event.model_dump_json()}\n\n"
                # State and events are persisted together. Drain the last batch before closing.
                if app.state.store.get(identifier).status in TERMINAL:
                    break
                heartbeat += 1
                if heartbeat % 40 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="dashboard")
    return app


app = create_app()
