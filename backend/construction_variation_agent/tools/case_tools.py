"""Case tools: read-only, scoped case context and existing review state."""

from __future__ import annotations

from google.adk.tools import ToolContext

from ..shared_libraries.constants import STATE_EXTRACTIONS
from ..shared_libraries.runtime import runtime_from_state


def get_case_context(tool_context: ToolContext) -> dict:
    """User-authored case facts: title, alleged change, parties, contract form, key dates.
    These are the USER's assertions, not verified evidence."""
    return {"user_authored": True, **runtime_from_state(tool_context.state).provider.case_context()}


def get_case_metadata(tool_context: ToolContext) -> dict:
    """Minimal case metadata for this job (ids and document count). No customer text."""
    rt = runtime_from_state(tool_context.state)
    r = rt.request
    return {"case_id": r.case_id, "job_id": r.job_id, "document_count": len(r.document_ids)}


def get_approved_source_references(tool_context: ToolContext) -> dict:
    """Source references a human reviewer has already accepted for this case (may be empty)."""
    rt = runtime_from_state(tool_context.state)
    return {"approved_references": rt.provider.review_summary().get("approved_references", [])}


def get_existing_timeline_events(tool_context: ToolContext) -> dict:
    """Events already stored on the case from earlier runs, plus events recorded in this job."""
    rt = runtime_from_state(tool_context.state)
    current = [e for evs in (tool_context.state.get(STATE_EXTRACTIONS) or {}).values() for e in evs]
    return {"stored_events": rt.provider.existing_events(), "events_this_job": current}
