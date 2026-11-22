"""Executes one analysis job through the real Google ADK workflow.

Builds a fresh agent tree and an in-memory ADK session per job (ADK state is temporary; the
backend persists validated results to GCS afterwards). Scope identifiers are written into
session state by this module only. Budgets, timeout and stage guards fail the job closed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

from construction_variation_agent.agent import ROOT_AGENT_NAME, build_root_agent
from construction_variation_agent.model_config import ModelNotConfiguredError
from construction_variation_agent.shared_libraries.constants import (
    STATE_CASE,
    STATE_CASE_CONTEXT,
    STATE_CORRELATION,
    STATE_DOCUMENTS,
    STATE_JOB,
    STATE_TENANT,
)
from construction_variation_agent.shared_libraries.runtime import (
    BudgetExceeded,
    EvidenceProvider,
    JobLimits,
    JobRuntime,
    ScopeError,
    StageIncompleteError,
    register_runtime,
    unregister_runtime,
)
from construction_variation_agent.shared_libraries.schemas import (
    AnalysisResult,
    CaseAnalysisRequest,
)
from construction_variation_agent.tracing import TraceRecorder

from .engine import HardValidationError, ValidationReport, assemble_result

log = logging.getLogger("cvea.analysis")
APP_NAME = "construction_variation_agent"


@dataclass
class AnalysisFailure:
    code: str
    message: str
    retryable: bool


@dataclass
class AnalysisOutcome:
    result: AnalysisResult | None
    report: ValidationReport | None
    failure: AnalysisFailure | None
    trace_summary: dict[str, Any]
    trace_events: list[dict[str, Any]]
    model_calls: int = 0
    tool_calls: int = 0
    intake: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def _failure(exc: BaseException) -> AnalysisFailure:
    if isinstance(exc, BudgetExceeded):
        return AnalysisFailure("BUDGET_EXCEEDED", str(exc), False)
    if isinstance(exc, StageIncompleteError):
        return AnalysisFailure("STAGE_INCOMPLETE", str(exc), True)
    if isinstance(exc, ScopeError):
        return AnalysisFailure(
            "SCOPE_VIOLATION", "An agent attempted to act outside its authorised scope.", False
        )
    if isinstance(exc, ModelNotConfiguredError):
        return AnalysisFailure("MODEL_NOT_CONFIGURED", str(exc), False)
    if isinstance(exc, HardValidationError):
        return AnalysisFailure(exc.code, str(exc)[:300], False)
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return AnalysisFailure("JOB_TIMEOUT", "The analysis exceeded its time budget.", True)
    if isinstance(exc, genai_errors.APIError):
        return AnalysisFailure(
            "MODEL_API_ERROR", f"Model API error (status {getattr(exc, 'code', '?')}).", True
        )
    return AnalysisFailure("UNEXPECTED_ERROR", type(exc).__name__, True)


async def run_analysis(
    request: CaseAnalysisRequest,
    provider: EvidenceProvider,
    *,
    model: Any,
    limits: JobLimits | None = None,
    trace_sink: Callable[[dict[str, Any]], None] | None = None,
    attempt: int = 0,
) -> AnalysisOutcome:
    limits = limits or JobLimits()
    model_id = model if isinstance(model, str) else getattr(model, "model", type(model).__name__)
    tracer = TraceRecorder(
        request, str(model_id), ROOT_AGENT_NAME, sink=trace_sink, attempt=attempt
    )
    rt = JobRuntime(request=request, provider=provider, limits=limits, tracer=tracer)
    register_runtime(rt)
    runner: Runner | None = None
    try:
        root = build_root_agent(model, max_documents=len(request.document_ids))
        service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=root, session_service=service)
        session = await service.create_session(
            app_name=APP_NAME,
            user_id=f"job-{request.job_id}",
            state={
                STATE_TENANT: request.tenant_id,
                STATE_CASE: request.case_id,
                STATE_JOB: request.job_id,
                STATE_CORRELATION: request.correlation_id,
                STATE_DOCUMENTS: list(request.document_ids),
                STATE_CASE_CONTEXT: provider.case_context(),
            },
        )
        standard_analysis_prompt = (
            "Perform a rigorous, evidence-led variation analysis on all authorized case documents. "
            "Identify and extract every scope change, architect/engineer instruction, quotation revision, "
            "delay or disruption record, quantity change, and price consequence evidenced in the files. "
            "Cross-reference related documents by literal reference, detect recorded date and amount "
            "contradictions, group events into potential variation candidates with verbatim citations, "
            "and prepare clear questions for reviewer verification."
        )
        message = types.Content(
            role="user", parts=[types.Part(text=standard_analysis_prompt)]
        )

        async def consume() -> None:
            async for _ in runner.run_async(
                user_id=session.user_id, session_id=session.id, new_message=message
            ):
                pass

        await asyncio.wait_for(consume(), timeout=limits.timeout_seconds)
        final = await service.get_session(
            app_name=APP_NAME, user_id=session.user_id, session_id=session.id
        )
        state = dict(final.state) if final else {}
        result, report = assemble_result(rt, state, str(model_id))
        tracer.record(
            kind="job_validated",
            citation_validity=round(report.citation_validity, 4),
            events=len(result.events),
            candidates=len(result.candidates),
            dropped=len(report.dropped),
        )
        return AnalysisOutcome(
            result,
            report,
            None,
            tracer.summary(),
            tracer.events,
            rt.model_calls,
            rt.tool_calls,
            intake=state.get("intake_result"),
        )
    except BaseException as exc:  # noqa: BLE001 - mapped to a structured failure, never swallowed silently
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            raise
        failure = _failure(exc)
        tracer.record(kind="job_failed", failure_category=failure.code)
        log.error("analysis failed: %s", failure.code)
        return AnalysisOutcome(
            None, None, failure, tracer.summary(), tracer.events, rt.model_calls, rt.tool_calls
        )
    finally:
        unregister_runtime(request.job_id)
        if runner is not None:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001
                log.debug("runner close failed", exc_info=True)
