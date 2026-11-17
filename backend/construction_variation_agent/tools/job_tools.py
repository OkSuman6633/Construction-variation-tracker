"""Job tools: read-only job context and stage reporting. Agents cannot change job status."""

from __future__ import annotations

import time

from google.adk.tools import ToolContext

from ..shared_libraries.constants import STAGES, STATE_EXTRACTIONS, STATE_INTAKE
from ..shared_libraries.runtime import runtime_from_state


def get_job_context(tool_context: ToolContext) -> dict:
    """Authorised job context: ids, document count and remaining model/tool budget."""
    rt = runtime_from_state(tool_context.state)
    return {
        "job_id": rt.request.job_id,
        "case_id": rt.request.case_id,
        "correlation_id": rt.request.correlation_id,
        "document_count": len(rt.request.document_ids),
        "model_calls_remaining": rt.limits.max_model_calls - rt.model_calls,
        "tool_calls_remaining": rt.limits.max_tool_calls - rt.tool_calls,
        "seconds_remaining": max(
            0, int(rt.limits.timeout_seconds - (time.monotonic() - rt.started_at))
        ),
    }


def report_stage(stage: str, note: str, tool_context: ToolContext) -> dict:
    """Report a short progress note for the current stage (shown to operators, no document text).
    stage must be one of the six stage names."""
    if stage not in STAGES:
        return {"status": "ignored", "reason": "unknown stage"}
    rt = runtime_from_state(tool_context.state)
    if rt.tracer:
        rt.tracer.record(kind="stage_note", stage=stage, detail=note[:200])
    return {"status": "recorded"}


def get_processing_diagnostics(tool_context: ToolContext) -> dict:
    """Structured diagnostics: documents skipped at intake, extraction counts, counters."""
    rt = runtime_from_state(tool_context.state)
    intake = (tool_context.state.get(STATE_INTAKE) or {}).get("documents", [])
    ex = tool_context.state.get(STATE_EXTRACTIONS) or {}
    return {
        "skipped_documents": [
            {"document_id": d["document_id"], "route": d["parser_route"]}
            for d in intake
            if d["parser_route"] != "extract_text"
        ],
        "events_per_document": {k: len(v) for k, v in ex.items()},
        "model_calls": rt.model_calls,
        "tool_calls": rt.tool_calls,
    }
