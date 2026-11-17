"""Correlation IDs, body-size limit, in-process rate limit, security headers."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .errors import AppError, correlation_id_var


class RequestGuardMiddleware(BaseHTTPMiddleware):
    """Per-instance limits only; production additionally relies on Cloud Run/Armor limits."""

    def __init__(self, app, *, per_minute: int, max_body: int, max_upload: int = 0) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.per_minute = per_minute
        self.max_body = max_body
        self.max_upload = max_upload or max_body
        self._hits: dict[str, tuple[int, int]] = {}

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        cid = (request.headers.get("x-correlation-id") or uuid.uuid4().hex)[:100]
        token = correlation_id_var.set(cid)
        try:
            limit = self.max_upload if "/uploads/" in request.url.path else self.max_body
            if int(request.headers.get("content-length") or 0) > limit:
                err = AppError("PAYLOAD_TOO_LARGE", "Request body too large.", 413)
                return self._finish(JSONResponse(err.body(), status_code=413), cid)
            if request.url.path != "/healthz":
                ident = request.headers.get("authorization", "") or (
                    request.client.host if request.client else "anon"
                )
                window = int(time.time() // 60)
                count, win = self._hits.get(ident, (0, window))
                count = count + 1 if win == window else 1
                self._hits[ident] = (count, window)
                if count > self.per_minute:
                    err = AppError("RATE_LIMITED", "Too many requests.", 429, True)
                    resp = JSONResponse(err.body(), status_code=429)
                    resp.headers["Retry-After"] = "30"
                    return self._finish(resp, cid)
            return self._finish(await call_next(request), cid)
        finally:
            correlation_id_var.reset(token)

    @staticmethod
    def _finish(resp: Response, cid: str) -> Response:
        resp.headers["X-Correlation-ID"] = cid
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp
