"""Small ASGI limits: bounded request bodies, origin checks, and mutation rate."""

import asyncio
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.clients = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode().lower(): value.decode("latin1") for key, value in scope["headers"]}

        async def reject(status, code, message):
            await JSONResponse({"error": {"code": code, "message": message}}, status_code=status)(scope, receive, send)

        if scope["method"] == "POST":
            origin = headers.get("origin")
            same_origin = bool(origin and urlsplit(origin).netloc == headers.get("host") and urlsplit(origin).scheme in {"http", "https"})
            if origin and origin not in self.settings.cors_origins and not same_origin:
                return await reject(403, "ORIGIN_BLOCKED", "This browser origin is not approved.")
            client = (scope.get("client") or ("local", 0))[0]
            current = time.monotonic()
            hits = self.clients.setdefault(client, deque())
            self.clients.move_to_end(client)
            while hits and hits[0] < current - 60:
                hits.popleft()
            if len(hits) >= 30:
                return await reject(429, "RATE_LIMITED", "Too many mutations. Wait one minute and retry.")
            hits.append(current)
            if len(self.clients) > 1024:
                self.clients.popitem(last=False)
            body = bytearray()
            try:
                async with asyncio.timeout(5):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > 16384:
                            return await reject(413, "BODY_TOO_LARGE", "Request body exceeds 16 KiB.")
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "BODY_TIMEOUT", "Request body timed out.")
            if body and headers.get("content-type", "").split(";")[0] != "application/json":
                return await reject(415, "JSON_REQUIRED", "Use application/json for request bodies.")
            original_receive, sent = receive, False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = replay

        async def secure_send(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                ])
                if scope["path"] != "/docs":
                    response_headers.append((b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, secure_send)
