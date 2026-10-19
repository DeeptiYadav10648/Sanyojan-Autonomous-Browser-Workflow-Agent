"""Behavioral SSRF/plan/transport tests. No DNS, TCP or TLS leaves this process."""

import asyncio
import copy
import http.client
import io
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from sanyojan.config import Settings
from sanyojan.errors import AppError
from sanyojan.planning import WorkflowPlanningAgent
from sanyojan.safety import SafetyPolicy, public_ip
from sanyojan.schemas import DEMO_URL, Plan, TaskSpec


PUBLIC_URL = "https://public.example/products"
PUBLIC_IP = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Safety tests must stub every DNS/TCP boundary")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "tasks.sqlite3",
        reports_dir=tmp_path / "reports",
        frontend_dir=tmp_path / "absent-frontend",
        allowed_domains=["public.example", "other.example", "localhost", "metadata.google.internal"],
        max_redirects=2,
        max_page_bytes=1024,
        task_timeout_seconds=5,
    )


@pytest.fixture
def policy(settings):
    return SafetyPolicy(settings)


def answers(*addresses):
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
         (address, 443, 0, 0) if ":" in address else (address, 443))
        for address in addresses
    ]


@pytest.fixture
def resolver(monkeypatch):
    resolver = Mock(return_value=answers(PUBLIC_IP))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    return resolver


@pytest.fixture
def workflow(settings):
    return WorkflowPlanningAgent(settings).plan(TaskSpec(
        objective="Extract public product titles and prices",
        target_url=PUBLIC_URL,
        task_type="extraction",
        fields=["title", "price"],
        constraints=["Read-only", "One page"],
    ))


def assert_app_error(caught, code, status=400):
    assert caught.value.code == code
    assert caught.value.status == status


@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.0.1", "192.168.0.1",
    "169.254.169.254", "169.254.170.2", "100.64.0.1", "0.0.0.0", "192.0.2.1",
    "198.51.100.1", "203.0.113.1", "198.18.0.1", "224.0.0.1", "240.0.0.1",
    "255.255.255.255", "::", "::1", "fc00::1", "fe80::1", "ff02::1", "2001:db8::1",
    "::ffff:8.8.8.8", "::ffff:127.0.0.1", "2002:0808:0808::1",
    "2001:0000:4136:e378:8000:63bf:3fff:fdd2", "not-an-ip",
])
def test_private_reserved_metadata_and_transition_ips_are_not_public(address):
    assert public_ip(address) is False


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "1.1.1.1", PUBLIC_V6])
def test_global_unicast_ips_are_public(address):
    assert public_ip(address) is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://public.example/products", "javascript:alert(1)",
    "//public.example/products", "https:///products", "https://user@public.example/products",
    "https://user:password@public.example/products", "https://@public.example/products",
    "https://public.example:8443/products", "http://public.example:443/products",
    "https://public.example:80/products", "https://public.example:notaport/products",
    "https://public.example:99999/products", "https://public.example/products?key=value",
    "https://public.example/products#section", "https://public.example./products",
    "https://public.example/a b", " https://public.example/products", "https://public.example/\nproducts",
    "https://public.example/\\127.0.0.1", "https://public.example/%0d%0aHost:evil",
    "https://public.example/%0Asecret", "https://public.example/\x00", "https://public.example/\x7f",
    "https://public.example/" + "x" * 2048, "https://[::1]/", "https://[fe80::1%25eth0]/",
    "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/latest/meta-data/",
    "http://0.0.0.0/", "http://192.0.2.1/", "http://[::ffff:8.8.8.8]/",
])
def test_disallowed_url_forms_are_rejected_before_dns(policy, url):
    with pytest.raises(AppError) as rejected:
        policy.validate_url(url)
    assert_app_error(rejected, "URL_BLOCKED")


@pytest.mark.parametrize("url", [
    "https://unlisted.example/products", "https://sub.public.example/products",
    "https://public.example.evil/products", "https://localhost/products",
    "https://metadata.google.internal/latest/meta-data/", "https://demo.sanyojan.test/products/",
    "https://demo.sanyojan.test/", "http://2130706433/", "http://0x7f000001/",
])
def test_allowlist_is_exact_and_special_names_cannot_be_enabled(policy, url):
    with pytest.raises(AppError) as rejected:
        policy.validate_url(url)
    assert_app_error(rejected, "DOMAIN_BLOCKED")


@pytest.mark.parametrize("url,host", [
    (PUBLIC_URL, "public.example"), ("https://PUBLIC.EXAMPLE:443/products", "public.example"),
    ("http://public.example:80/products", "public.example"), ("https://public.example", "public.example"),
    (DEMO_URL, "demo.sanyojan.test"),
])
def test_valid_urls_and_default_ports(policy, url, host):
    assert policy.validate_url(url) == host


async def test_demo_never_uses_dns_or_network(policy, workflow):
    assert await policy.resolve(DEMO_URL) == []
    workflow.steps[0].target = DEMO_URL
    await policy.validate_plan(workflow, DEMO_URL)
    with pytest.raises(AppError) as rejected:
        await policy.fetch(DEMO_URL)
    assert_app_error(rejected, "DEMO_FETCH")


async def test_resolve_checks_all_answers_and_deduplicates(policy, resolver):
    resolver.return_value = answers(PUBLIC_IP, PUBLIC_V6, PUBLIC_IP)
    assert await policy.resolve(PUBLIC_URL) == [PUBLIC_IP, PUBLIC_V6]
    call = resolver.call_args
    assert call.args[:2] == ("public.example", 443)
    # asyncio forwards getaddrinfo options positionally to its executor.
    assert call.args[3] == socket.SOCK_STREAM
    assert await policy.resolve("http://public.example/products") == [PUBLIC_IP, PUBLIC_V6]
    assert resolver.call_args.args[:2] == ("public.example", 80)


@pytest.mark.parametrize("addresses", [
    (), ("127.0.0.1",), ("169.254.169.254",), (PUBLIC_IP, "10.0.0.1"),
    ("10.0.0.1", PUBLIC_IP), (PUBLIC_V6, "::1"), (PUBLIC_IP, "::ffff:8.8.8.8"),
    (PUBLIC_IP, "invalid"),
])
async def test_any_nonpublic_dns_answer_blocks_entire_destination(policy, resolver, addresses):
    resolver.return_value = answers(*addresses)
    with pytest.raises(AppError) as rejected:
        await policy.resolve(PUBLIC_URL)
    assert_app_error(rejected, "ADDRESS_BLOCKED")


@pytest.mark.parametrize("failure", [socket.gaierror("no address"), TimeoutError("DNS timeout")])
async def test_dns_failures_have_bounded_safe_errors(policy, resolver, failure):
    resolver.side_effect = failure
    with pytest.raises(AppError) as rejected:
        await policy.resolve(PUBLIC_URL)
    assert_app_error(rejected, "DNS_FAILED")


async def test_fetch_rechecks_dns_after_plan_validation(policy, workflow, resolver, monkeypatch):
    resolver.side_effect = [answers(PUBLIC_IP), answers("169.254.169.254")]
    request = Mock()
    monkeypatch.setattr(policy, "_request", request)
    await policy.validate_plan(workflow, PUBLIC_URL)
    with pytest.raises(AppError) as rejected:
        await policy.fetch(PUBLIC_URL)
    assert_app_error(rejected, "ADDRESS_BLOCKED")
    assert resolver.call_count == 2
    request.assert_not_called()


@pytest.mark.parametrize("location,code", [
    ("https://other.example/products", "REDIRECT_BLOCKED"),
    ("//other.example/products", "REDIRECT_BLOCKED"),
    ("http://127.0.0.1/private", "URL_BLOCKED"),
    ("http://169.254.169.254/latest/meta-data/", "URL_BLOCKED"),
    ("https://public.example/products?secret=1", "URL_BLOCKED"),
    ("https://user:password@public.example/products", "URL_BLOCKED"),
    ("file:///etc/passwd", "URL_BLOCKED"),
])
async def test_redirects_cannot_escape_approved_host_or_url_policy(policy, resolver, monkeypatch, location, code):
    request = Mock(return_value=(302, {"location": location}, b""))
    monkeypatch.setattr(policy, "_request", request)
    with pytest.raises(AppError) as rejected:
        await policy.fetch(PUBLIC_URL)
    assert_app_error(rejected, code)
    assert request.call_count == 1 and resolver.call_count == 1


async def test_same_host_redirect_gets_fresh_dns_and_blocks_rebinding(policy, resolver, monkeypatch):
    resolver.side_effect = [answers(PUBLIC_IP), answers(PUBLIC_IP, "192.168.1.1")]
    request = Mock(return_value=(307, {"location": "/next"}, b""))
    monkeypatch.setattr(policy, "_request", request)
    with pytest.raises(AppError) as rejected:
        await policy.fetch(PUBLIC_URL)
    assert_app_error(rejected, "ADDRESS_BLOCKED")
    assert request.call_count == 1 and resolver.call_count == 2


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_relative_same_host_redirect_returns_final_url_and_vetted_ip(policy, resolver, monkeypatch, status):
    resolver.side_effect = [answers(PUBLIC_IP), answers("1.1.1.1")]
    request = Mock(side_effect=[(status, {"location": "../catalog"}, b""), (200, {}, b"<html>OK</html>")])
    monkeypatch.setattr(policy, "_request", request)
    assert await policy.fetch(PUBLIC_URL) == (b"<html>OK</html>", "https://public.example/catalog")
    assert [(call.args[0], call.args[1]) for call in request.call_args_list] == [
        (PUBLIC_URL, PUBLIC_IP), ("https://public.example/catalog", "1.1.1.1"),
    ]
    assert request.call_args_list[0].args[2] == request.call_args_list[1].args[2]


@pytest.mark.parametrize("limit", [0, 1, 2])
async def test_redirect_limit_is_enforced_without_extra_request(policy, resolver, monkeypatch, limit):
    policy.settings.max_redirects = limit
    request = Mock(return_value=(302, {"location": "/loop"}, b""))
    monkeypatch.setattr(policy, "_request", request)
    with pytest.raises(AppError) as rejected:
        await policy.fetch(PUBLIC_URL)
    assert_app_error(rejected, "REDIRECT_BLOCKED")
    assert request.call_count == limit + 1 and resolver.call_count == limit + 1


async def test_redirect_missing_location_is_rejected(policy, resolver, monkeypatch):
    request = Mock(return_value=(302, {}, b""))
    monkeypatch.setattr(policy, "_request", request)
    with pytest.raises(AppError) as rejected:
        await policy.fetch(PUBLIC_URL)
    assert_app_error(rejected, "REDIRECT_BLOCKED")
    assert request.call_count == 1


@pytest.mark.parametrize("status", [201, 204, 400, 404, 500])
async def test_fetch_requires_http_200(policy, resolver, monkeypatch, status):
    monkeypatch.setattr(policy, "_request", Mock(return_value=(status, {}, b"untrusted page text")))
    with pytest.raises(AppError) as rejected:
        await policy.fetch(PUBLIC_URL)
    assert_app_error(rejected, "PAGE_HTTP_ERROR")
    assert str(status) in rejected.value.message and "untrusted" not in rejected.value.message


def mutate_plan(payload, case):
    if case == "executable-action":
        payload["steps"][2]["action"] = "javascript"
    elif case == "reordered-actions":
        payload["steps"][1]["action"], payload["steps"][2]["action"] = "extract", "inspect_title"
    elif case == "wrong-step-number":
        payload["steps"][1]["number"] = 1
    elif case == "missing-step":
        payload["steps"].pop()
    elif case == "extra-step":
        payload["steps"].append(copy.deepcopy(payload["steps"][-1]))
    elif case == "unknown-root-key":
        payload["code"] = "import os"
    elif case == "unknown-step-key":
        payload["steps"][0]["script"] = "fetch('http://127.0.0.1')"
    elif case == "high-risk":
        payload["steps"][0]["risk"] = "high"
    elif case == "too-short-timeout":
        payload["steps"][0]["timeout_ms"] = 999
    elif case == "too-long-timeout":
        payload["steps"][0]["timeout_ms"] = 30001
    elif case == "unsupported-field":
        payload["fields"] = ["password"]
    elif case == "duplicate-fields":
        payload["fields"] = ["title", "title"]
    elif case == "unsafe-mode":
        payload["mode"] = "autonomous"
    else:
        raise AssertionError(case)
    return payload


@pytest.mark.parametrize("case", [
    "executable-action", "reordered-actions", "wrong-step-number", "missing-step", "extra-step",
    "unknown-root-key", "unknown-step-key", "high-risk", "too-short-timeout", "too-long-timeout",
    "unsupported-field", "duplicate-fields", "unsafe-mode",
])
def test_plan_schema_rejects_malicious_or_unbounded_workflows(workflow, case):
    with pytest.raises(ValidationError):
        Plan.model_validate(mutate_plan(workflow.model_dump(), case))


async def test_validate_plan_revalidates_mutated_objects_before_dns(policy, workflow, resolver):
    workflow.steps[0].action = "shell"
    with pytest.raises(ValidationError):
        await policy.validate_plan(workflow, PUBLIC_URL)
    resolver.assert_not_called()


async def test_plan_navigation_must_match_exact_approved_target(policy, workflow, resolver):
    workflow.steps[0].target = "https://other.example/products"
    with pytest.raises(AppError) as rejected:
        await policy.validate_plan(workflow, PUBLIC_URL)
    assert_app_error(rejected, "PLAN_BLOCKED")
    resolver.assert_not_called()


@pytest.fixture
def transport(monkeypatch):
    body = b"<html><title>Safe page</title></html>"
    stream = io.BytesIO(body)
    response = Mock(status=200)
    response.getheaders.return_value = [("Content-Type", "text/html; charset=utf-8")]
    response.read1.side_effect = stream.read
    connection = Mock()
    connection.getresponse.return_value = response
    constructor = Mock(return_value=connection)
    tcp = Mock(name="numeric-IP TCP socket")
    connect = Mock(return_value=tcp)
    tls = Mock(name="TLS socket")
    context = Mock()
    context.wrap_socket.return_value = tls
    tls_context = Mock(return_value=context)
    monkeypatch.setattr(http.client, "HTTPConnection", constructor)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr("sanyojan.safety.ssl.create_default_context", tls_context)
    return SimpleNamespace(body=body, response=response, connection=connection, constructor=constructor,
                           tcp=tcp, connect=connect, tls=tls, context=context, tls_context=tls_context)


def request(policy, url, *, address=PUBLIC_IP, aborted=None, deadline=None, active=None):
    return policy._request(url, address, time.monotonic() + 10 if deadline is None else deadline,
                           threading.Event() if aborted is None else aborted, [] if active is None else active)


@pytest.mark.parametrize("scheme,port", [("https", 443), ("http", 80)])
def test_transport_pins_numeric_ip_and_preserves_host_and_tls_sni(policy, transport, scheme, port):
    active = []
    status, headers, body = request(policy, f"{scheme}://public.example/products", active=active)
    assert status == 200 and body == transport.body and headers["content-type"].startswith("text/html")
    transport.connect.assert_called_once_with((PUBLIC_IP, port), timeout=3)
    transport.constructor.assert_called_once_with("public.example", port, timeout=3)
    assert active == [transport.connection]
    call = transport.connection.request.call_args
    assert call.args == ("GET", "/products")
    assert call.kwargs["headers"]["Host"] == "public.example"
    assert call.kwargs["headers"]["Accept-Encoding"] == "identity"
    assert "Cookie" not in call.kwargs["headers"] and "Authorization" not in call.kwargs["headers"]
    if scheme == "https":
        transport.tls_context.assert_called_once_with()
        transport.context.wrap_socket.assert_called_once_with(transport.tcp, server_hostname="public.example")
        assert transport.connection.sock is transport.tls
    else:
        transport.tls_context.assert_not_called()
        assert transport.connection.sock is transport.tcp
    transport.connection.close.assert_called_once_with()


def test_transport_ipv6_pin_root_path_and_idna_sni(policy, transport):
    request(policy, "https://bücher.example", address=PUBLIC_V6)
    transport.connect.assert_called_once_with((PUBLIC_V6, 443), timeout=3)
    transport.context.wrap_socket.assert_called_once_with(transport.tcp, server_hostname="xn--bcher-kva.example")
    call = transport.connection.request.call_args
    assert call.args == ("GET", "/") and call.kwargs["headers"]["Host"] == "xn--bcher-kva.example"


@pytest.mark.parametrize("headers,code", [
    ([], "PAGE_TYPE_BLOCKED"),
    ([("Content-Type", "application/json")], "PAGE_TYPE_BLOCKED"),
    ([("Content-Type", "image/png")], "PAGE_TYPE_BLOCKED"),
    ([("Content-Type", "text/html"), ("Content-Encoding", "gzip")], "PAGE_ENCODING_BLOCKED"),
    ([("Content-Type", "text/html"), ("Content-Encoding", "br")], "PAGE_ENCODING_BLOCKED"),
    ([("Content-Type", "text/html"), ("Content-Length", "1025")], "PAGE_TOO_LARGE"),
    ([("Content-Type", "text/html"), ("Content-Length", "-1")], "PAGE_TOO_LARGE"),
    ([("Content-Type", "text/html"), ("Content-Length", "not-a-number")], "PAGE_TOO_LARGE"),
])
def test_transport_rejects_type_encoding_and_declared_size_before_reading(policy, transport, headers, code):
    transport.response.getheaders.return_value = headers
    with pytest.raises(AppError) as rejected:
        request(policy, PUBLIC_URL)
    assert_app_error(rejected, code)
    transport.response.read1.assert_not_called()
    transport.connection.close.assert_called_once_with()


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_transport_enforces_streamed_size_even_if_length_missing_or_false(policy, transport, declared_length):
    headers = [("Content-Type", "text/html")]
    if declared_length is not None:
        headers.append(("Content-Length", declared_length))
    transport.response.getheaders.return_value = headers
    transport.response.read1.side_effect = io.BytesIO(b"x" * 1025).read
    with pytest.raises(AppError) as rejected:
        request(policy, PUBLIC_URL)
    assert_app_error(rejected, "PAGE_TOO_LARGE")
    assert all(call.args[0] <= 1025 for call in transport.response.read1.call_args_list)
    transport.connection.close.assert_called_once_with()


@pytest.mark.parametrize("mime", ["text/html", "application/xhtml+xml", "TEXT/HTML; charset=UTF-8"])
def test_transport_accepts_exact_byte_limit_and_html_types(policy, transport, mime):
    transport.response.getheaders.return_value = [("Content-Type", mime), ("Content-Length", "1024")]
    transport.response.read1.side_effect = io.BytesIO(b"x" * 1024).read
    assert request(policy, PUBLIC_URL)[2] == b"x" * 1024
    transport.connection.close.assert_called_once_with()


@pytest.mark.parametrize("status", [302, 404])
def test_transport_does_not_read_redirect_or_error_body(policy, transport, status):
    transport.response.status = status
    transport.response.getheaders.return_value = [("Location", "/next")]
    assert request(policy, PUBLIC_URL) == (status, {"location": "/next"}, b"")
    transport.response.read1.assert_not_called()
    transport.connection.close.assert_called_once_with()


@pytest.mark.parametrize("state", ["cancelled", "expired"])
def test_transport_checks_abort_and_deadline_before_connecting(policy, transport, state):
    aborted = threading.Event()
    if state == "cancelled":
        aborted.set()
    with pytest.raises(AppError) as rejected:
        request(policy, PUBLIC_URL, aborted=aborted, deadline=time.monotonic() - 1 if state == "expired" else None)
    assert_app_error(rejected, "FETCH_TIMEOUT")
    transport.connect.assert_not_called()
    transport.connection.close.assert_called_once_with()


def test_transport_checks_cancellation_after_connect_before_http_request(policy, transport):
    aborted = threading.Event()

    def wrap(*args, **kwargs):
        aborted.set()
        return transport.tls

    transport.context.wrap_socket.side_effect = wrap
    with pytest.raises(AppError) as rejected:
        request(policy, PUBLIC_URL, aborted=aborted)
    assert_app_error(rejected, "FETCH_CANCELLED")
    transport.connection.request.assert_not_called()
    transport.connection.close.assert_called_once_with()


def test_transport_checks_cancellation_during_stream_read(policy, transport):
    aborted = threading.Event()

    def read(size):
        aborted.set()
        return b"partial HTML"

    transport.response.read1.side_effect = read
    with pytest.raises(AppError) as rejected:
        request(policy, PUBLIC_URL, aborted=aborted)
    assert_app_error(rejected, "FETCH_TIMEOUT")
    assert transport.response.read1.call_count == 1
    transport.connection.close.assert_called_once_with()


@pytest.mark.parametrize("boundary", ["tcp", "tls", "request", "response", "read"])
def test_transport_failures_close_connection_and_hide_raw_error(policy, transport, boundary):
    failing = {"tcp": transport.connect, "tls": transport.context.wrap_socket,
               "request": transport.connection.request, "response": transport.connection.getresponse,
               "read": transport.response.read1}[boundary]
    failing.side_effect = OSError("secret proxy credentials C:/private")
    with pytest.raises(AppError) as rejected:
        request(policy, PUBLIC_URL)
    assert_app_error(rejected, "FETCH_FAILED")
    assert "secret" not in rejected.value.message and "C:/private" not in rejected.value.message
    transport.connection.close.assert_called_once_with()


async def test_fetch_cancellation_closes_active_connection_and_signals_thread(policy, resolver, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    connection = Mock()
    captured = {}

    def blocking_request(url, address, deadline, aborted, active):
        captured["aborted"] = aborted
        active.append(connection)
        started.set()
        try:
            if not release.wait(timeout=3):
                raise AssertionError("Test did not release the fetch thread")
            raise OSError("closed socket")
        finally:
            exited.set()

    monkeypatch.setattr(policy, "_request", blocking_request)
    task = asyncio.create_task(policy.fetch(PUBLIC_URL))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert captured["aborted"].is_set()
        connection.close.assert_called_once_with()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(exited.wait, 2)
        await asyncio.sleep(0)
