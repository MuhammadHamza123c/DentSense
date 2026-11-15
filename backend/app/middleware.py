"""HTTP middleware: request IDs, access logging, security headers, size guard."""

from __future__ import annotations

import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.logging_config import get_logger, request_id_ctx

logger = get_logger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID, log the request and add hardening headers."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming_id = request.headers.get("X-Request-ID", "")
        request_id = incoming_id if _is_valid_request_id(incoming_id) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers take care of the response body; log first.
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "%s %s failed after %.1f ms",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        finally:
            request_id_ctx.reset(token)

        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-ms"] = f"{duration_ms:.1f}"
        _apply_security_headers(response)

        logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject obviously oversized requests before reading the body."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": "bad_request",
                            "message": "Invalid Content-Length header.",
                            "request_id": request_id_ctx.get(),
                        }
                    },
                )
            if declared > self.max_bytes:
                limit_mb = self.max_bytes / (1024 * 1024)
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": f"Request body exceeds the {limit_mb:g} MB limit.",
                            "request_id": request_id_ctx.get(),
                        }
                    },
                )
        return await call_next(request)


def _is_valid_request_id(value: str) -> bool:
    if not value or len(value) > 64:
        return False
    return all(char.isalnum() or char in "-_" for char in value)


def _apply_security_headers(response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(self), camera=(self), microphone=()"
    )
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    response.headers.setdefault("Cache-Control", "no-store")
