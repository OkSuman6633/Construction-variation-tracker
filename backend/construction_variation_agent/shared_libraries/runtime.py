"""Authorised execution context for one analysis job.

The backend builds a JobRuntime (with an EvidenceProvider limited to ONE tenant/case)
and registers it under job_id. Agent tools never receive tenant/case/path parameters;
they resolve the runtime from ADK session state that only the backend writes, and the
runtime is cross-checked against that state. Agents therefore cannot choose a tenant,
a case or a storage path.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .constants import (
    MAX_CHARS_PER_PAGE,
    MAX_MODEL_CALLS,
    MAX_PAGES_PER_CALL,
    MAX_TOOL_CALLS,
    STATE_CASE,
    STATE_JOB,
    STATE_TENANT,
)
from .schemas import CaseAnalysisRequest


class ScopeError(Exception):
    """A tool tried to act outside the authorised tenant/case/job scope."""


class BudgetExceeded(Exception):
    """Model-call, tool-call or time budget exhausted (job fails closed)."""


class StageIncompleteError(Exception):
    """A stage ended without producing its required, validated output."""


@dataclass(frozen=True)
class ParsedPage:
    page_ref: str  # "page_1", "sheet_Costs", "message_body" ...
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    pages: tuple[ParsedPage, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)
    mime_type: str | None = None
    image_bytes: bytes | None = None

    def page(self, page_ref: str) -> ParsedPage | None:
        return next((p for p in self.pages if p.page_ref == page_ref), None)


class EvidenceProvider(Protocol):
    """Read-only, single-case evidence access. Implementations must enforce scope."""

    def case_context(self) -> dict[str, Any]: ...

    def list_documents(self) -> list[dict[str, Any]]: ...

    def get_document(self, document_id: str) -> ParsedDocument: ...

    def existing_events(self) -> list[dict[str, Any]]: ...

    def review_summary(self) -> dict[str, Any]: ...


@dataclass
class JobLimits:
    max_model_calls: int = MAX_MODEL_CALLS
    max_tool_calls: int = MAX_TOOL_CALLS
    max_pages_per_call: int = MAX_PAGES_PER_CALL
    max_chars_per_page: int = MAX_CHARS_PER_PAGE
    timeout_seconds: float = 900.0


@dataclass
class JobRuntime:
    request: CaseAnalysisRequest
    provider: EvidenceProvider
    limits: JobLimits = field(default_factory=JobLimits)
    tracer: Any = None  # TraceRecorder (tracing.py); typed Any to avoid an import cycle
    started_at: float = field(default_factory=time.monotonic)
    model_calls: int = 0
    tool_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def count_model_call(self) -> None:
        with self._lock:
            self.model_calls += 1
            if self.model_calls > self.limits.max_model_calls:
                raise BudgetExceeded("model call budget exceeded")
        self._check_time()

    def count_tool_call(self) -> None:
        with self._lock:
            self.tool_calls += 1
            if self.tool_calls > self.limits.max_tool_calls:
                raise BudgetExceeded("tool call budget exceeded")
        self._check_time()

    def _check_time(self) -> None:
        if time.monotonic() - self.started_at > self.limits.timeout_seconds:
            raise BudgetExceeded("job time budget exceeded")

    def require_document(self, document_id: str) -> None:
        if document_id not in self.request.document_ids:
            raise ScopeError("document is not part of this authorised analysis")


_REGISTRY: dict[str, JobRuntime] = {}
_REG_LOCK = threading.Lock()


def register_runtime(rt: JobRuntime) -> None:
    with _REG_LOCK:
        _REGISTRY[rt.request.job_id] = rt


def unregister_runtime(job_id: str) -> None:
    with _REG_LOCK:
        _REGISTRY.pop(job_id, None)


def runtime_from_state(state: Any) -> JobRuntime:
    """Resolve the runtime from backend-written state and verify it matches."""
    job_id = state.get(STATE_JOB)
    with _REG_LOCK:
        rt = _REGISTRY.get(job_id) if job_id else None
    if rt is None:
        raise ScopeError(
            "No backend-authorized job runtime. Run analysis through the authenticated case API."
        )
    if (
        state.get(STATE_TENANT) != rt.request.tenant_id
        or state.get(STATE_CASE) != rt.request.case_id
    ):
        raise ScopeError("session scope does not match the authorised job")
    return rt
