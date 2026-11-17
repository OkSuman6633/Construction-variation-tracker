"""Structured error contract: {code, message, correlation_id, retryable}."""

from __future__ import annotations

import contextvars

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable

    def body(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "correlation_id": correlation_id_var.get(),
            "retryable": self.retryable,
        }


def not_found(what: str = "Resource") -> AppError:
    # Identical for "missing" and "belongs to another tenant" - no existence oracle.
    return AppError("NOT_FOUND", f"{what} not found.", 404)


def forbidden(message: str = "You do not have permission to do this.") -> AppError:
    return AppError("FORBIDDEN", message, 403)


def revision_conflict() -> AppError:
    return AppError(
        "CASE_REVISION_CONFLICT", "The case has changed. Reload before saving.", 409, True
    )
