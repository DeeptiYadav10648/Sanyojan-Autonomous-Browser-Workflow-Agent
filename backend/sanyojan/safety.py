"""SSRF boundary: validate every destination and connect to the vetted IP itself.

The browser never fetches network content. This small GET-only transport does not
use system proxies, cookies, auth, scripts, automatic redirects, or decompression.
"""

import asyncio
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

from .errors import AppError
from .schemas import DEMO_URL, Plan


def public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        # Deny transition mechanisms that can tunnel to an unvalidated IPv4 target.
        return ip.is_global and not ip.is_multicast and not (
            isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo)
        )
    except ValueError:
        return False


class SafetyPolicy:
    def __init__(self, settings):
        self.settings = settings

    def validate_url(self, url: str) -> str:
        if url == DEMO_URL:
            return "demo.sanyojan.test"
        if len(url) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
            raise AppError("URL_BLOCKED", "Malformed URL or disallowed characters.")
        try:
            p = urlsplit(url)
            if p.scheme not in {"http", "https"} or not p.hostname or p.username is not None or p.password is not None:
                raise ValueError()
            if p.port not in {None, 443 if p.scheme == "https" else 80}:
                raise ValueError()
            host = p.hostname.encode("idna").decode("ascii").lower()
            if host.endswith(".") or "%" in host or p.fragment or p.query:
                raise ValueError()
            if re.search(r"%0[ad]", url, re.I):
                raise ValueError()
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not public_ip(host):
                    raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise AppError("URL_BLOCKED", "Use an HTTP(S) public URL without credentials, query strings, fragments, or custom ports.") from exc
        allowed = {domain.lower() for domain in self.settings.allowed_domains}
        if host not in allowed or host in {"localhost", "metadata.google.internal", "demo.sanyojan.test"}:
            raise AppError("DOMAIN_BLOCKED", "This exact hostname is not on the configured public domain allowlist.")
        return host

    async def resolve(self, url: str) -> list[str]:
        host = self.validate_url(url)
        if url == DEMO_URL:
            return []
        port = 443 if urlsplit(url).scheme == "https" else 80
        try:
            answers = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=5
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise AppError("DNS_FAILED", "Public destination DNS resolution failed.", 400) from exc
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or any(not public_ip(ip) for ip in addresses):
            raise AppError("ADDRESS_BLOCKED", "DNS resolved to a private, reserved, or disallowed network address.")
        return addresses

    async def validate_plan(self, plan: Plan, target_url: str) -> None:
        # Pydantic enforces the action order; bind navigation to the user's URL.
        plan = Plan.model_validate(plan.model_dump())
        if plan.steps[0].target != target_url:
            raise AppError("PLAN_BLOCKED", "Plan navigation differs from the approved target.")
        await self.resolve(target_url)

    async def fetch(self, url: str) -> tuple[bytes, str]:
        if url == DEMO_URL:
            raise AppError("DEMO_FETCH", "The demo is a bundled fixture, never a network destination.")
        original_host = self.validate_url(url)
        deadline = time.monotonic() + min(self.settings.task_timeout_seconds, 30)
        for redirect in range(self.settings.max_redirects + 1):
            host = self.validate_url(url)
            if host != original_host:
                raise AppError("REDIRECT_BLOCKED", "Cross-host redirects require a new separately approved plan.")
            addresses = await self.resolve(url)  # Fresh check at every hop, not just at planning time.
            aborted = threading.Event()
            active: list[http.client.HTTPConnection] = []
            worker = asyncio.create_task(asyncio.to_thread(self._request, url, addresses[0], deadline, aborted, active))
            try:
                status, headers, body = await asyncio.wait_for(asyncio.shield(worker), timeout=max(0.1, deadline - time.monotonic()))
            except (asyncio.CancelledError, TimeoutError) as exc:
                aborted.set()
                for connection in active:
                    try:
                        if connection.sock:
                            connection.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    connection.close()
                # Retrieve any thread exception; the worker has strict I/O/deadline limits.
                worker.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
                if isinstance(exc, TimeoutError):
                    raise AppError("FETCH_TIMEOUT", "Page fetch deadline exceeded.") from exc
                raise
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location or redirect >= self.settings.max_redirects:
                    raise AppError("REDIRECT_BLOCKED", "Missing redirect destination or redirect limit exceeded.")
                url = urljoin(url, location)
                continue
            if status != 200:
                raise AppError("PAGE_HTTP_ERROR", f"The approved public page returned HTTP {status}.", 400)
            return body, url
        raise AppError("REDIRECT_BLOCKED", "Redirect limit exceeded.")

    def _request(self, url, address, deadline, aborted, active):
        p = urlsplit(url)
        host = p.hostname.encode("idna").decode("ascii")
        port = 443 if p.scheme == "https" else 80
        connection = http.client.HTTPConnection(host, port, timeout=3)
        active.append(connection)
        try:
            if aborted.is_set() or time.monotonic() >= deadline:
                raise AppError("FETCH_TIMEOUT", "Page fetch deadline exceeded.")
            # Numeric address is used for the actual TCP connection: no second DNS lookup.
            transport = socket.create_connection((address, port), timeout=3)
            connection.sock = transport
            if p.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(transport, server_hostname=host)
            if aborted.is_set():
                raise AppError("FETCH_CANCELLED", "Fetch cancelled.")
            path = p.path or "/"
            connection.request("GET", path, headers={
                "Host": host, "User-Agent": "Sanyojan/0.1 (read-only workflow)",
                "Accept": "text/html", "Accept-Encoding": "identity", "Connection": "close",
            })
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status in {301, 302, 303, 307, 308}:
                return response.status, headers, b""
            if response.status != 200:
                return response.status, headers, b""
            if headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {"text/html", "application/xhtml+xml"}:
                raise AppError("PAGE_TYPE_BLOCKED", "Only static HTML responses are supported.")
            if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise AppError("PAGE_ENCODING_BLOCKED", "Compressed responses are rejected to bound decompressed page size.")
            content_length = headers.get("content-length")
            if content_length and (not content_length.isdigit() or int(content_length) > self.settings.max_page_bytes):
                raise AppError("PAGE_TOO_LARGE", "The public page exceeds the configured size limit.")
            chunks, size = [], 0
            while True:
                if aborted.is_set() or time.monotonic() >= deadline:
                    raise AppError("FETCH_TIMEOUT", "Page fetch cancelled or deadline exceeded.")
                chunk = response.read1(min(65536, self.settings.max_page_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > self.settings.max_page_bytes:
                    raise AppError("PAGE_TOO_LARGE", "The public page exceeds the configured size limit.")
            return response.status, headers, b"".join(chunks)
        except (OSError, http.client.HTTPException, UnicodeError) as exc:
            raise AppError("FETCH_FAILED", "Could not safely fetch the approved public HTML page.", 400) from exc
        finally:
            connection.close()
