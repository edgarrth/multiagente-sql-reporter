from __future__ import annotations

import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

import structlog
from structlog.contextvars import bound_contextvars

logger = structlog.get_logger(__name__)

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


class RequestLoggingMiddleware:
    """Pure ASGI request logging that does not buffer SSE responses.

    A pure ASGI middleware is used instead of BaseHTTPMiddleware so streaming agent
    responses remain incremental. Health-check traffic is suppressed by default.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        log_health_checks: bool = False,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.log_health_checks = log_health_checks

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        should_log = self.enabled and (
            self.log_health_checks or not path.startswith("/health/")
        )
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        request_id = headers.get("x-request-id") or str(uuid4())
        method = str(scope.get("method") or "")
        client = scope.get("client") or (None, None)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message):  # type: ignore[no-untyped-def]
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 500)
                response_headers = list(message.get("headers") or [])
                if not any(key.lower() == b"x-request-id" for key, _ in response_headers):
                    response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        context = {
            "request_id": request_id,
            "http_method": method,
            "http_path": path,
            "client_ip": client[0],
        }
        with bound_contextvars(**context):
            if should_log:
                logger.info("http_request_started")
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                if should_log:
                    logger.exception(
                        "http_request_failed",
                        duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    )
                raise
            finally:
                if should_log:
                    logger.info(
                        "http_request_completed",
                        status_code=status_code,
                        duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    )
