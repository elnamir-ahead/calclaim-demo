"""FastAPI middleware: correlation IDs and optional JWT bearer enforcement."""

from __future__ import annotations

import uuid
from typing import Callable, ClassVar, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.utils.request_context import reset_correlation_id, set_correlation_id


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """When REQUIRE_AUTH=true, require valid JWT (JWKS) except for public paths."""

    public_paths: ClassVar[Tuple[str, ...]] = (
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
    )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        from src.utils.jwt_verify import (
            JWTConfigurationError,
            jwt_auth_enabled,
            verify_bearer_token,
        )

        if not jwt_auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path in self.public_paths:
            return await call_next(request)

        auth = request.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return JSONResponse({"detail": "Missing bearer token"}, status_code=401)
        token = auth[7:].strip()
        if not token:
            return JSONResponse({"detail": "Missing bearer token"}, status_code=401)
        try:
            verify_bearer_token(token)
        except JWTConfigurationError:
            return JSONResponse(
                {"detail": "Authentication misconfigured on server"},
                status_code=500,
            )
        except Exception:
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
        return await call_next(request)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate X-Correlation-ID / X-Request-ID; echo on response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        raw = (
            request.headers.get("x-correlation-id")
            or request.headers.get("X-Correlation-ID")
            or request.headers.get("x-request-id")
            or request.headers.get("X-Request-ID")
        )
        cid = (raw or "").strip() or str(uuid.uuid4())
        token = set_correlation_id(cid)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            reset_correlation_id(token)
