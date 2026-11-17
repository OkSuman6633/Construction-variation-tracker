"""Authentication of queue-delivered worker calls."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from ..errors import AppError


class TaskVerifier(Protocol):
    def verify(self, request: Request) -> None: ...


class NoopTaskVerifier:
    """LOCAL/TEST ONLY: accepts any caller. Refused in staging/production by Settings."""

    def verify(self, request: Request) -> None:
        return None


class GoogleOidcTaskVerifier:
    """Verifies the Google-signed OIDC token that Cloud Tasks attaches.

    STATUS: implemented, NOT verified against live Cloud Tasks/Cloud Run.
    """

    def __init__(self, audience: str, service_account: str) -> None:
        self.audience, self.sa = audience, service_account

    def verify(self, request: Request) -> None:
        from google.auth.transport import requests as greq
        from google.oauth2 import id_token

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise AppError("UNAUTHENTICATED", "Missing task credentials.", 401)
        try:
            claims = id_token.verify_oauth2_token(auth[7:], greq.Request(), audience=self.audience)
        except Exception as exc:  # noqa: BLE001
            raise AppError("UNAUTHENTICATED", "Invalid task credentials.", 401) from exc
        if claims.get("email") != self.sa or not claims.get("email_verified"):
            raise AppError("FORBIDDEN", "Caller is not the task service account.", 403)
