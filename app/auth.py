"""Simple API key check for /mcp, /a2a and /api.

Written as raw ASGI middleware because BaseHTTPMiddleware messed with
the MCP streaming responses. Accepts X-API-Key or Authorization: Bearer.
"""

from __future__ import annotations

import hmac
import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings

log = logging.getLogger(__name__)

PROTECTED_PREFIXES = ("/mcp", "/a2a", "/api")


def _extract_key(scope: Scope) -> str | None:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    if "x-api-key" in headers:
        return headers["x-api-key"]
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        if not get_settings().api_key:
            log.warning("API_KEY is not set: authentication is DISABLED (local development only).")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PROTECTED_PREFIXES):
            await self.app(scope, receive, send)
            return
        expected = get_settings().api_key
        if not expected:
            await self.app(scope, receive, send)
            return
        provided = _extract_key(scope) or ""
        # compare_digest so the check isn't vulnerable to timing attacks
        if hmac.compare_digest(provided.encode(), expected.encode()):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "unauthorized", "detail": "Missing or invalid API key."}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="incar-agent-hub"'),
            ],
        })
        await send({"type": "http.response.body", "body": body})
