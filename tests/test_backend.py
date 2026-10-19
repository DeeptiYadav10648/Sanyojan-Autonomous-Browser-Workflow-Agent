"""Offline backend contracts; TestClient contexts deliberately run the lifespan.

Chromium belongs to the separate E2E suite. Only the browser/model/network
boundaries are replaced here; storage, orchestration, validation and reports run.
"""

import asyncio
import csv
import importlib
import io
import json
import socket
from datetime import datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sanyojan.config import Settings
from sanyojan.errors import AppError
from sanyojan.planning import TaskUnderstandingAgent, WorkflowPlanningAgent
from sanyojan.schemas import ACTIONS, DEMO_URL, Approval, PlanInput, Status, TaskSpec


class FakeBrowser:
    def __init__(self, *, error=None):
        self.calls = []
        self.cleaned = 0
        self.error = error

    async def run(self, url, fields, task_type, emit):
        self.calls.append((url, list(fields), task_type))
        try:
            await emit("Approved page loaded.", 1)
            await emit("Actual page title inspected.", 2)
            if self.error is not None:
                raise self.error
            await emit("Visible records extracted.", 3)
            return {
                "source_url": url,
                "page_title": "Offline catalog",
                "raw_records": [
                    {"title": "=SUM(1,2)", "price": "$12.50"},
                    {"title": "Needs review", "price": None},
                ],
            }
        finally:
            self.cleaned += 1


class SlowBrowser(FakeBrowser):
    """Events are awaited/set on the app loop, never from the pytest thread."""

    def __init__(self, *, hold_cleanup=False):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.allow_cleanup = asyncio.Event()
        if not hold_cleanup:
            self.allow_cleanup.set()
        self.cancellations = 0

    async def run(self, url, fields, task_type, emit):
        self.calls.append((url, list(fields), task_type))
        try:
            await emit("Slow page loading.", 1)
            self.started.set()
            await self.release.wait()
            return {"source_url": url, "page_title": "Slow catalog",
                    "raw_records": [{"title": "Product", "price": "2.00"}]}
        except asyncio.CancelledError:
            self.cancellations += 1
            raise
        finally:
            self.cleanup_started.set()
            await asyncio.wait_for(self.allow_cleanup.wait(), timeout=3)
            self.cleaned += 1


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Backend unit/integration tests must not access the network or Chromium")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_backend_modules_import():
    """Keep import failures visible without suppressing independent safety tests."""
    importlib.import_module("sanyojan.store")
    importlib.import_module("sanyojan.app")


@pytest.fixture
def app_factory(monkeypatch):
    module = importlib.import_module("sanyojan.app")
    monkeypatch.setattr(module, "browser_ready", AsyncMock(side_effect=AssertionError("Fake browser must bypass Chromium probe")))
    return module.create_app


@pytest.fixture
def store_factory(app_factory):
    return importlib.import_module("sanyojan.store").Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "db" / "tasks.sqlite3",
        reports_dir=tmp_path / "reports",
        frontend_dir=tmp_path / "nonexistent-frontend",
        allowed_domains=["public.example"],
        trusted_hosts=["testserver"],
        cors_origins=["http://localhost:5173"],
        max_concurrent_tasks=2,
    )


@pytest.fixture
def browser():
    return FakeBrowser()


@pytest.fixture
def client(settings, browser, app_factory):
    with TestClient(app_factory(settings, browser=browser)) as client:
        yield client


def plan(client, **changes):
    response = client.post("/api/tasks/plan", json={
        "instruction": "Extract product titles and prices and generate reports",
        "target_url": DEMO_URL,
        **changes,
    })
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    return error


async def wait_workers(orchestrator):
    workers = list(orchestrator.workers.values())
    if workers:
        await asyncio.wait_for(asyncio.gather(*workers), timeout=3)


def complete(client, task_id):
    response = client.post(f"/api/tasks/{task_id}/execute", json={"approved": True})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "RUNNING"
    client.portal.call(wait_workers, client.app.state.orchestrator)
    return client.get(f"/api/tasks/{task_id}").json()


def test_health_and_plan_have_no_browser_side_effects(client, browser):
    assert client.get("/api/health").json() == {
        "status": "ok", "version": "0.1.0", "planner_mode": "deterministic", "browser_available": True,
    }
    task = plan(client)
    assert str(UUID(task["id"])) == task["id"]
    assert task["status"] == "PLANNED"
    assert task["progress"] == 0 and task["result"] is None
    assert task["started_at"] is None and task["finished_at"] is None
    assert [event["status"] for event in task["logs"]] == ["CREATED", "PLANNING", "PLANNED"]
    workflow = task["plan"]
    assert workflow["mode"] == "deterministic"
    assert [step["action"] for step in workflow["steps"]] == list(ACTIONS)
    assert [step["number"] for step in workflow["steps"]] == [1, 2, 3, 4, 5]
    assert workflow["steps"][0]["target"] == DEMO_URL
    assert workflow["fields"] == ["title", "price"]
    assert any("demo" in warning.lower() for warning in workflow["warnings"])
    assert browser.calls == []


@pytest.mark.parametrize("payload", [
    {}, {"instruction": "short"}, {"instruction": " " * 12},
    {"instruction": "Extract products\x00secret"}, {"instruction": "x" * 2001},
    {"instruction": 42}, {"instruction": "Extract products", "unknown": "private-value"},
    {"instruction": "Extract products", "fields": []},
    {"instruction": "Extract products", "fields": ["title", "title"]},
    {"instruction": "Extract products", "fields": ["password"]},
    {"instruction": "Extract products", "task_type": "shell"},
    {"instruction": "Extract products", "target_url": "bad"},
])
def test_plan_input_validation_does_not_create_tasks(client, browser, payload):
    response = client.post("/api/tasks/plan", json=payload)
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"]
    assert all(set(detail) == {"field", "message"} for detail in error["details"])
    assert "private-value" not in response.text and "secret" not in response.text
    assert client.get("/api/tasks").json() == []
    assert browser.calls == []


@pytest.mark.parametrize(("instruction", "code"), [
    ("Please do something useful", "AMBIGUOUS_TASK"),
    ("Extract passwords and product prices", "UNSUPPORTED_TASK"),
    ("Click products and submit the report", "UNSUPPORTED_TASK"),
    ("Extract the first 5 products", "UNSUPPORTED_CONSTRAINT"),
    ("Extract products from https://other.example/products", "AMBIGUOUS_URL"),
])
def test_unsupported_instruction_errors_are_not_persisted(client, browser, instruction, code):
    assert_error(client.post("/api/tasks/plan", json={"instruction": instruction}), 400, code)
    assert client.get("/api/tasks").json() == []
    assert browser.calls == []


@pytest.mark.parametrize("target", [
    "http://127.0.0.1/products", "https://user:topsecret@public.example/products",
    "https://public.example/products?token=topsecret", "https://public.example:8443/products",
    "https://unlisted.example/products",
])
def test_rejected_target_is_not_persisted_or_echoed(client, browser, target):
    response = client.post("/api/tasks/plan", json={"instruction": "Extract products", "target_url": target})
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"URL_BLOCKED", "DOMAIN_BLOCKED"}
    assert "topsecret" not in response.text
    assert client.get("/api/tasks").json() == [] and browser.calls == []


@pytest.mark.parametrize("approval", [None, {}, {"approved": False}, {"approved": "true"},
                                     {"approved": "yes"}, {"approved": True, "extra": 1}])
def test_execute_requires_explicit_approval_object(client, browser, approval):
    task = plan(client)
    assert_error(client.post(f"/api/tasks/{task['id']}/execute", json=approval), 422, "VALIDATION_ERROR")
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "PLANNED"
    assert browser.calls == []


@pytest.mark.parametrize("number", [1, 1.0], ids=["integer-one", "float-one"])
def test_approval_schema_rejects_numeric_true(number):
    with pytest.raises(ValidationError):
        Approval.model_validate({"approved": number})


@pytest.mark.parametrize("number", [1, 1.0], ids=["integer-one", "float-one"])
def test_execute_rejects_numeric_approval(client, browser, number):
    """Approval must be the JSON boolean true, not a value equal to True."""
    task = plan(client)
    response = client.post(f"/api/tasks/{task['id']}/execute", json={"approved": number})
    assert_error(response, 422, "VALIDATION_ERROR")
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "PLANNED"
    assert browser.calls == []


def test_success_lifecycle_real_reports_and_history(client, browser, settings):
    task = plan(client)
    task_id = task["id"]
    assert_error(client.get(f"/api/tasks/{task_id}/results"), 409, "RESULTS_NOT_READY")
    assert_error(client.get(f"/api/tasks/{task_id}/reports/csv"), 409, "REPORT_NOT_READY")
    done = complete(client, task_id)
    assert done["status"] == "SUCCEEDED" and done["progress"] == 5
    assert done["error"] is None and done["current_action"] == "Completed"
    assert datetime.fromisoformat(done["created_at"]) <= datetime.fromisoformat(done["started_at"])
    assert datetime.fromisoformat(done["started_at"]) <= datetime.fromisoformat(done["finished_at"])
    assert browser.calls == [(DEMO_URL, ["title", "price"], "extraction")]
    assert browser.cleaned == 1
    assert [event["status"] for event in done["logs"] if event["type"] == "state"] == [
        "CREATED", "PLANNING", "PLANNED", "RUNNING", "SUCCEEDED",
    ]
    ids = [event["id"] for event in done["logs"]]
    assert ids == sorted(set(ids))
    assert any(event["type"] == "warning" for event in done["logs"])
    result = client.get(f"/api/tasks/{task_id}/results").json()
    assert result == done["result"]
    assert result["extracted_count"] == 2 and result["valid_count"] == 1
    assert result["failed_steps"] == [] and result["duration_seconds"] >= 0
    assert result["records"][0]["data"] == {"title": "=SUM(1,2)", "price": 12.5}
    assert result["records"][1]["data"]["price"] is None
    assert all(record["source_url"] == DEMO_URL for record in result["records"])
    for format in ("csv", "json"):
        assert result["reports"][format] == f"/api/tasks/{task_id}/reports/{format}"
        response = client.get(result["reports"][format])
        assert response.status_code == 200
        assert f"sanyojan-{task_id}.{format}" in response.headers["content-disposition"]
        assert response.headers["content-type"].startswith("text/csv" if format == "csv" else "application/json")
        assert response.content == (settings.reports_dir / f"{task_id}.{format}").read_bytes()
        if format == "json":
            payload = response.json()
            assert payload["schema_version"] == 1 and payload["metadata"]["task_id"] == task_id
            assert payload["result"] == result
        else:
            rows = list(csv.DictReader(io.StringIO(response.text)))
            assert len(rows) == 2 and rows[0]["title"] == "'=SUM(1,2)"
            assert rows[0]["price"] == "12.5" and rows[1]["price"] == ""
    history = client.get("/api/tasks").json()
    assert history[0]["id"] == task_id and history[0]["logs"] == []
    assert history[0]["result"]["records"] == []
    assert client.get(f"/api/tasks/{task_id}/results").json()["records"] == result["records"]
    assert client.post(f"/api/tasks/{task_id}/cancel").json() == done
    assert_error(client.post(f"/api/tasks/{task_id}/execute", json={"approved": True}), 409, "INVALID_TRANSITION")
    assert len(browser.calls) == 1


def test_history_pagination_and_validation(client):
    first, second, third = [plan(client) for _ in range(3)]
    assert [task["id"] for task in client.get("/api/tasks?limit=2&offset=1").json()] == [second["id"], first["id"]]
    assert client.get("/api/tasks?offset=3").json() == []
    for query in ("limit=0", "limit=101", "offset=-1", "offset=10001"):
        assert_error(client.get(f"/api/tasks?{query}"), 422, "VALIDATION_ERROR")
    assert client.get("/api/tasks?limit=1").json()[0]["id"] == third["id"]


@pytest.mark.parametrize("suffix,method", [("", "get"), ("/results", "get"), ("/events", "get"),
                                         ("/reports/json", "get"), ("/cancel", "post"), ("/execute", "post")])
def test_missing_and_invalid_task_identifiers(client, suffix, method):
    kwargs = {"json": {"approved": True}} if suffix == "/execute" else {}
    request = getattr(client, method)
    assert_error(request(f"/api/tasks/{uuid4()}{suffix}", **kwargs), 404, "TASK_NOT_FOUND")
    assert_error(request(f"/api/tasks/not-a-uuid{suffix}", **kwargs), 422, "VALIDATION_ERROR")


def test_report_format_validation_normalized_uuid_and_missing_file(client, settings):
    task_id = plan(client)["id"]
    for format in ("xml", "CSV", "json.exe"):
        assert_error(client.get(f"/api/tasks/{task_id}/reports/{format}"), 400, "REPORT_FORMAT")
    complete(client, task_id)
    assert client.get(f"/api/tasks/{task_id.upper()}/reports/json").status_code == 200
    (settings.reports_dir / f"{task_id}.json").unlink()
    assert_error(client.get(f"/api/tasks/{task_id}/reports/json"), 404, "REPORT_NOT_FOUND")
    assert client.get(f"/api/tasks/{task_id}/reports/csv").status_code == 200
    assert client.get("/api/tasks/..%2Foutside/reports/json").status_code in {404, 422}


def test_duplicate_execution_and_capacity(settings, app_factory):
    settings.max_concurrent_tasks = 1
    browser = SlowBrowser()
    with TestClient(app_factory(settings, browser=browser)) as client:
        first, second = plan(client), plan(client)
        assert client.post(f"/api/tasks/{first['id']}/execute", json={"approved": True}).status_code == 202
        client.portal.call(asyncio.wait_for, browser.started.wait(), 2)
        assert_error(client.post(f"/api/tasks/{first['id']}/execute", json={"approved": True}), 409, "INVALID_TRANSITION")
        assert_error(client.post(f"/api/tasks/{second['id']}/execute", json={"approved": True}), 429, "CAPACITY_REACHED")
        assert client.get(f"/api/tasks/{second['id']}").json()["status"] == "PLANNED"
        assert client.post(f"/api/tasks/{first['id']}/cancel").json()["status"] == "CANCELLED"
        assert browser.cleaned == 1 and len(browser.calls) == 1
        client.portal.call(browser.release.set)
        done = complete(client, second["id"])
        assert done["status"] == "SUCCEEDED" and len(browser.calls) == 2


def test_concurrent_execute_rechecks_state_after_dns_yield(client, browser, monkeypatch):
    task_id = plan(client)["id"]
    orchestrator = client.app.state.orchestrator

    async def race():
        both_validating = asyncio.Event()
        arrivals = 0

        async def validate(*args):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                both_validating.set()
            await asyncio.wait_for(both_validating.wait(), 2)

        monkeypatch.setattr(orchestrator.safety, "validate_plan", validate)
        outcomes = await asyncio.gather(orchestrator.execute(task_id), orchestrator.execute(task_id), return_exceptions=True)
        await wait_workers(orchestrator)
        return outcomes

    outcomes = client.portal.call(race)
    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    errors = [outcome for outcome in outcomes if isinstance(outcome, AppError)]
    assert len(errors) == 1 and errors[0].code == "ALREADY_EXECUTED" and errors[0].status == 409
    assert len(browser.calls) == 1


def test_cancellation_is_idempotent_and_waits_for_slow_cleanup(settings, app_factory):
    browser = SlowBrowser(hold_cleanup=True)
    with TestClient(app_factory(settings, browser=browser)) as client:
        task_id = plan(client)["id"]
        assert client.post(f"/api/tasks/{task_id}/execute", json={"approved": True}).status_code == 202
        orchestrator = client.app.state.orchestrator

        async def cancel_twice():
            await asyncio.wait_for(browser.started.wait(), 2)
            first = asyncio.create_task(orchestrator.cancel(task_id))
            second = None
            try:
                await asyncio.wait_for(browser.cleanup_started.wait(), 2)
                second = asyncio.create_task(orchestrator.cancel(task_id))
                await asyncio.sleep(0)
                assert not first.done() and not second.done()
                assert orchestrator.store.get(task_id).status == Status.RUNNING
                assert browser.cleaned == 0 and browser.cancellations == 1
            finally:
                browser.allow_cleanup.set()
                outcomes = await asyncio.wait_for(asyncio.gather(*[job for job in (first, second) if job]), 2)
            assert all(task.status == Status.CANCELLED for task in outcomes)
            assert orchestrator.workers == {}

        client.portal.call(cancel_twice)
        cancelled = client.get(f"/api/tasks/{task_id}").json()
        assert browser.cleaned == 1 and browser.cancellations == 1
        assert cancelled["finished_at"] and cancelled["result"] is None
        assert len([event for event in cancelled["logs"] if event["status"] == "CANCELLED"]) == 1
        assert client.post(f"/api/tasks/{task_id}/cancel").json() == cancelled
        assert_error(client.post(f"/api/tasks/{task_id}/execute", json={"approved": True}), 409, "INVALID_TRANSITION")
        assert_error(client.get(f"/api/tasks/{task_id}/reports/json"), 409, "REPORT_NOT_READY")


def test_cancel_planned_task_never_starts_browser(client, browser):
    task_id = plan(client)["id"]
    cancelled = client.post(f"/api/tasks/{task_id}/cancel").json()
    assert cancelled["status"] == "CANCELLED" and cancelled["started_at"] is None
    assert client.post(f"/api/tasks/{task_id}/cancel").json() == cancelled
    assert browser.calls == []


def test_lifespan_shutdown_cancels_workers_and_persists_cleanup(settings, app_factory):
    browser = SlowBrowser()
    with TestClient(app_factory(settings, browser=browser)) as client:
        task_id = plan(client)["id"]
        client.post(f"/api/tasks/{task_id}/execute", json={"approved": True})
        client.portal.call(asyncio.wait_for, browser.started.wait(), 2)
    assert browser.cleaned == 1 and browser.cancellations == 1
    with TestClient(app_factory(settings, browser=FakeBrowser())) as restarted:
        task = restarted.get(f"/api/tasks/{task_id}").json()
        assert task["status"] == "CANCELLED" and task["finished_at"]


@pytest.mark.parametrize("error,message", [
    (RuntimeError("secret-page-text C:/private/path"), "Browser or data pipeline failed"),
    (ValueError("secret-page-text"), "No supported visible records"),
    (TimeoutError("secret-page-text"), "Task duration limit exceeded"),
    (AppError("PAGE_HTTP_ERROR", "The approved public page returned HTTP 503."), "HTTP 503"),
])
def test_failed_execution_is_terminal_and_does_not_expose_raw_errors(settings, app_factory, error, message):
    browser = FakeBrowser(error=error)
    with TestClient(app_factory(settings, browser=browser)) as client:
        task_id = plan(client)["id"]
        failed = complete(client, task_id)
        assert failed["status"] == "FAILED" and message in failed["error"]
        assert "secret-page-text" not in json.dumps(failed) and "C:/private/path" not in json.dumps(failed)
        result = client.get(f"/api/tasks/{task_id}/results").json()
        assert result["records"] == [] and result["reports"] == {} and result["failed_steps"] == [3]
        assert failed["finished_at"] and browser.cleaned == 1
        assert client.post(f"/api/tasks/{task_id}/cancel").json() == failed
        assert_error(client.get(f"/api/tasks/{task_id}/reports/csv"), 409, "REPORT_NOT_READY")


def test_browser_unavailable_error_keeps_task_planned(client, browser):
    task_id = plan(client)["id"]
    client.app.state.browser_available = False
    assert_error(client.post(f"/api/tasks/{task_id}/execute", json={"approved": True}), 503, "BROWSER_UNAVAILABLE")
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "PLANNED"
    assert browser.calls == []


def dns_answer(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]


def test_planning_dns_failure_is_persisted_but_never_runs_browser(client, browser, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", Mock(return_value=dns_answer("127.0.0.1")))
    response = client.post("/api/tasks/plan", json={"instruction": "Extract products", "target_url": "https://public.example/products"})
    assert_error(response, 400, "ADDRESS_BLOCKED")
    history = client.get("/api/tasks").json()
    assert len(history) == 1 and history[0]["status"] == "FAILED"
    failed = client.get(f"/api/tasks/{history[0]['id']}").json()
    assert failed["logs"][-1]["type"] == "error" and failed["finished_at"]
    assert browser.calls == []


def test_execute_revalidates_dns_without_consuming_approval(client, browser, monkeypatch):
    resolver = Mock(side_effect=[dns_answer("93.184.216.34"), dns_answer("10.0.0.1")])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    task = plan(client, target_url="https://public.example/products")
    assert_error(client.post(f"/api/tasks/{task['id']}/execute", json={"approved": True}), 400, "ADDRESS_BLOCKED")
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "PLANNED"
    assert resolver.call_count == 2 and browser.calls == []


def parse_events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith("retry: 2000\n\n")
    parsed = []
    for frame in response.text.split("\n\n"):
        if not frame.startswith("id: "):
            continue
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        assert lines["event"] == "progress"
        event = json.loads(lines["data"])
        assert int(lines["id"]) == event["id"]
        parsed.append(event)
    return parsed


@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_sse_terminal_replay_and_last_event_id(settings, app_factory, terminal):
    browser = FakeBrowser(error=RuntimeError("failure") if terminal == "FAILED" else None)
    with TestClient(app_factory(settings, browser=browser)) as client:
        task_id = plan(client)["id"]
        if terminal == "CANCELLED":
            snapshot = client.post(f"/api/tasks/{task_id}/cancel").json()
        else:
            snapshot = complete(client, task_id)
        assert snapshot["status"] == terminal
        expected = snapshot["logs"]
        endpoint = f"/api/tasks/{task_id}/events"
        assert parse_events(client.get(endpoint)) == expected
        cursor = expected[1]["id"]
        assert parse_events(client.get(endpoint, params={"after": cursor})) == expected[2:]
        # The resume header overrides the query cursor, not the other way round.
        assert parse_events(client.get(endpoint, params={"after": 10**9}, headers={"Last-Event-ID": str(cursor)})) == expected[2:]
        assert parse_events(client.get(endpoint, headers={"Last-Event-ID": str(expected[-1]["id"])})) == []
        assert parse_events(client.get(endpoint, headers={"Last-Event-ID": str(10**9)})) == []


@pytest.mark.parametrize("cursor", ["-1", "abc", "1.0", "", "9" * 19])
def test_sse_invalid_resume_headers_are_json_errors(client, cursor):
    task_id = plan(client)["id"]
    assert_error(client.get(f"/api/tasks/{task_id}/events", headers={"Last-Event-ID": cursor}), 400, "EVENT_ID_INVALID")


def test_store_restart_preserves_plan_results_events_and_reports(settings, app_factory):
    with TestClient(app_factory(settings, browser=FakeBrowser())) as client:
        succeeded = complete(client, plan(client)["id"])
        pending = plan(client)
        csv_before = client.get(succeeded["result"]["reports"]["csv"]).content
    browser = FakeBrowser()
    with TestClient(app_factory(settings, browser=browser)) as restarted:
        for snapshot in (succeeded, pending):
            assert restarted.get(f"/api/tasks/{snapshot['id']}").json() == snapshot
        assert restarted.get(succeeded["result"]["reports"]["csv"]).content == csv_before
        assert parse_events(restarted.get(f"/api/tasks/{succeeded['id']}/events")) == succeeded["logs"]
        assert complete(restarted, pending["id"])["status"] == "SUCCEEDED"
        assert len(browser.calls) == 1


@pytest.mark.parametrize("status", list(Status))
def test_store_restart_recovers_only_interrupted_states(settings, app_factory, store_factory, status):
    store = store_factory(settings.database_path)
    try:
        task = store.create(PlanInput(instruction="Extract products"))
        if status in {Status.PLANNING, Status.PLANNED, Status.RUNNING, Status.SUCCEEDED}:
            store.update(task.id, "Planning", status=Status.PLANNING)
        if status in {Status.PLANNED, Status.RUNNING, Status.SUCCEEDED}:
            store.update(task.id, "Planned", status=Status.PLANNED)
        if status in {Status.RUNNING, Status.SUCCEEDED}:
            store.update(task.id, "Running", status=Status.RUNNING)
        if status in {Status.SUCCEEDED, Status.FAILED, Status.CANCELLED}:
            store.update(task.id, "Terminal", status=status)
        before = store.get(task.id)
    finally:
        store.close()
    with TestClient(app_factory(settings, browser=FakeBrowser())) as restarted:
        recovered = restarted.get(f"/api/tasks/{task.id}").json()
        if status in {Status.CREATED, Status.PLANNING, Status.RUNNING}:
            assert recovered["status"] == "FAILED" and recovered["finished_at"]
            assert recovered["error"] == "Execution interrupted by service restart."
            assert recovered["logs"][:-1] == [event.model_dump(mode="json") for event in before.logs]
            assert recovered["logs"][-1]["type"] == "error"
        else:
            assert recovered == before.model_dump(mode="json")
    with TestClient(app_factory(settings, browser=FakeBrowser())) as restarted_again:
        assert restarted_again.get(f"/api/tasks/{task.id}").json() == recovered


def test_store_rejects_invalid_transition_atomically_and_limits_storage(settings, store_factory):
    store = store_factory(settings.database_path, max_tasks=2)
    try:
        first = store.create(PlanInput(instruction="Extract products"))
        with pytest.raises(AppError) as rejected:
            store.update(first.id, "Cannot skip approval", status=Status.RUNNING)
        assert rejected.value.code == "INVALID_TRANSITION" and rejected.value.status == 409
        assert store.get(first.id) == first
        second = store.create(PlanInput(instruction="Inspect page title"))
        assert second.logs[0].id > first.logs[-1].id
        assert store.events(first.id, after=first.logs[-1].id) == []
        with pytest.raises(AppError) as full:
            store.create(PlanInput(instruction="Extract products"))
        assert full.value.code == "HISTORY_FULL" and full.value.status == 429
        assert len(store.list()) == 2
        store.update(first.id, "Cancelled", status=Status.CANCELLED)
        snapshot = store.get(first.id)
        with pytest.raises(AppError):
            store.update(first.id, "Cannot restart", status=Status.PLANNING)
        assert store.get(first.id) == snapshot
    finally:
        store.close()


@pytest.mark.parametrize("task_type,fields", [("extraction", ["title", "price"]),
                                            ("inspection", ["title", "description"]),
                                            ("report", ["title", "price"])])
async def test_deterministic_fallback_spec_and_plan(settings, task_type, fields):
    request = PlanInput(instruction="Extract products and generate reports", task_type=task_type)
    spec = await TaskUnderstandingAgent().understand(request)
    assert spec.target_url == DEMO_URL and spec.task_type == task_type and spec.fields == fields
    workflow = WorkflowPlanningAgent(settings).plan(spec)
    assert workflow.fields == fields and workflow.steps[0].target == DEMO_URL
    assert tuple(step.action for step in workflow.steps) == ACTIONS
    assert all(step.risk == "low" and step.timeout_ms == settings.action_timeout_ms for step in workflow.steps)


async def test_fallback_recognizes_fields_and_explicit_selection():
    agent = TaskUnderstandingAgent()
    inferred = await agent.understand(PlanInput(instruction="List product categories and descriptions"))
    assert inferred.fields == ["category", "description"]
    explicit = await agent.understand(PlanInput(instruction="Extract product titles and prices", fields=["description"]))
    assert explicit.fields == ["description"]
    with pytest.raises(AppError) as error:
        await agent.understand(PlanInput(instruction="Inspect the page title", task_type="inspection", fields=["price"]))
    assert error.value.code == "UNSUPPORTED_FIELDS"


def spec_payload(**changes):
    return {"objective": "Read a public page", "target_url": DEMO_URL, "task_type": "extraction",
            "fields": ["title"], "constraints": ["Read-only"], **changes}


@pytest.mark.parametrize("output", [None, "not JSON", {}, spec_payload(fields=["password"]),
                                    spec_payload(fields=["title", "title"]), spec_payload(code="execute()"),
                                    TaskSpec.model_construct(**spec_payload(fields=["password"]))])
async def test_injected_model_malformed_output_is_revalidated(output):
    adapter = Mock(understand=AsyncMock(return_value=output))
    with pytest.raises(AppError) as error:
        await TaskUnderstandingAgent(adapter).understand(PlanInput(instruction="Extract products"))
    assert error.value.code == "PLANNER_RESPONSE_INVALID" and error.value.status == 502
    adapter.understand.assert_awaited_once()


@pytest.mark.parametrize("changes", [{"target_url": "https://other.example/products"}, {"task_type": "report"}])
def test_injected_model_cannot_change_target_or_task_type(settings, app_factory, changes):
    browser = FakeBrowser()
    adapter = Mock(understand=AsyncMock(return_value=spec_payload(**changes)))
    with TestClient(app_factory(settings, browser=browser, adapter=adapter)) as client:
        assert_error(client.post("/api/tasks/plan", json={"instruction": "Extract products"}), 400, "PLANNER_POLICY")
        assert client.get("/api/tasks").json() == [] and browser.calls == []


def test_valid_injected_model_output_flows_to_plan_and_execution(settings, app_factory):
    adapter = Mock(understand=AsyncMock(return_value=spec_payload(fields=["title"])))
    browser = FakeBrowser()
    with TestClient(app_factory(settings, browser=browser, adapter=adapter)) as client:
        task = plan(client)
        assert task["plan"]["summary"] == "Read a public page" and task["plan"]["fields"] == ["title"]
        assert complete(client, task["id"])["result"]["valid_count"] == 2
        assert browser.calls == [(DEMO_URL, ["title"], "extraction")]


async def test_injected_model_timeout_cancels_adapter(monkeypatch):
    cancelled = asyncio.Event()

    async def understand(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    real_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, *, timeout):
        assert timeout == 20
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(importlib.import_module("sanyojan.planning").asyncio, "wait_for", short_wait_for)
    with pytest.raises(AppError) as error:
        await TaskUnderstandingAgent(Mock(understand=understand)).understand(PlanInput(instruction="Extract products"))
    assert error.value.code == "PLANNER_RESPONSE_INVALID" and error.value.status == 502
    assert cancelled.is_set()


def test_unexpected_adapter_error_has_safe_api_envelope(settings, app_factory):
    adapter = Mock(understand=AsyncMock(side_effect=RuntimeError("secret provider response C:/private")))
    with TestClient(app_factory(settings, browser=FakeBrowser(), adapter=adapter), raise_server_exceptions=False) as client:
        response = client.post("/api/tasks/plan", json={"instruction": "Extract products"})
        assert_error(response, 500, "INTERNAL_ERROR")
        assert "secret" not in response.text and "C:/private" not in response.text
        assert client.get("/api/tasks").json() == []


def test_malformed_json_and_unknown_route_use_error_envelopes(client):
    assert_error(client.post("/api/tasks/plan", content=b'{"instruction":', headers={"Content-Type": "application/json"}), 422, "VALIDATION_ERROR")
    assert_error(client.get("/api/unknown"), 404, "HTTP_ERROR")
