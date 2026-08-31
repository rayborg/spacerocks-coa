from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from ipaddress import ip_address

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.settings import Settings
from app.db.repositories import RateLimitStore
from app.observability.logging import safe_log

SECURITY_HEADERS = {
    b"cache-control": b"no-store",
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
    b"referrer-policy": b"no-referrer",
    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
    b"cross-origin-resource-policy": b"same-origin",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
}

_BODY_LIMITS = {
    ("POST", "/v1/checkout"): 4 * 1024,
    ("POST", "/v1/webhooks/stripe"): 256 * 1024,
    ("POST", "/v1/webhooks/resend"): 256 * 1024,
}


class BodyLimitExceeded(Exception):
    pass


class HttpSecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope["method"])
        path = str(scope["path"])
        limit = _BODY_LIMITS.get((method, path))
        headers = Headers(scope=scope)
        if limit is not None:
            media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                await _send_error(send, 415, "unsupported media type")
                return
            try:
                content_length = int(headers.get("content-length", "0"))
            except ValueError:
                await _send_error(send, 400, "invalid request")
                return
            if content_length > limit:
                await _send_error(send, 413, "request body too large")
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if limit is not None and message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyLimitExceeded
            return message

        response_started = False

        async def secure_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                response_headers = list(message.get("headers", []))
                existing = {key.lower() for key, _value in response_headers}
                response_headers.extend((key, value) for key, value in SECURITY_HEADERS.items() if key not in existing)
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, limited_receive, secure_send)
        except BodyLimitExceeded:
            if not response_started:
                await _send_error(secure_send, 413, "request body too large")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, store: RateLimitStore, settings: Settings) -> None:
        super().__init__(app)
        self.store = store
        self.settings = settings
        self.limits = {
            ("POST", "/v1/checkout"): ("checkout", settings.checkout_rate_limit),
            ("GET", "/v1/checkout/price"): ("checkout_price", settings.checkout_price_rate_limit),
            ("POST", "/v1/webhooks/stripe"): ("webhook", settings.webhook_rate_limit),
            ("POST", "/v1/webhooks/resend"): ("resend_webhook", settings.resend_webhook_rate_limit),
            ("GET", "/v1/orders/status"): ("status", settings.status_rate_limit),
            ("GET", "/v1/orders/proof"): ("proof", settings.proof_rate_limit),
            ("POST", "/v1/orders/rotate-token"): ("rotation", settings.rotation_rate_limit),
        }

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        policy = self.limits.get((request.method, request.url.path))
        if policy is not None:
            endpoint, limit = policy
            address = _client_address(request, self.settings.trusted_proxy_ips)
            if not self.store.hit(endpoint, address, datetime.now(UTC), limit):
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        return await call_next(request)


class SafeRequestLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, logger: logging.Logger) -> None:
        super().__init__(app)
        self.logger = logger

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.monotonic()
        request_id = secrets.token_hex(8)
        try:
            response = await call_next(request)
        except Exception:
            safe_log(
                self.logger,
                logging.ERROR,
                event="request_failed",
                method=request.method,
                path=_safe_path(request.url.path),
                status=500,
                request_id=request_id,
            )
            raise
        safe_log(
            self.logger,
            logging.INFO,
            event="request_complete",
            method=request.method,
            path=_safe_path(request.url.path),
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            request_id=request_id,
        )
        return response


def _client_address(request: Request, trusted_proxies: list[str]) -> str:
    direct = request.client.host if request.client else "unknown"
    if direct not in trusted_proxies:
        return direct
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    try:
        return str(ip_address(forwarded))
    except ValueError:
        return direct


def _safe_path(path: str) -> str:
    allowed = {
        "/v1/checkout",
        "/v1/checkout/price",
        "/v1/webhooks/stripe",
        "/v1/webhooks/resend",
        "/v1/orders/status",
        "/v1/orders/proof",
        "/v1/orders/rotate-token",
        "/health/live",
        "/health/ready",
        "/docs",
        "/openapi.json",
    }
    return path if path in allowed else "unmatched"


async def _send_error(send: Send, status: int, detail: str) -> None:
    response = JSONResponse({"detail": detail}, status_code=status)
    for key, value in SECURITY_HEADERS.items():
        response.headers[key.decode("ascii")] = value.decode("ascii")
    await response({"type": "http"}, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
