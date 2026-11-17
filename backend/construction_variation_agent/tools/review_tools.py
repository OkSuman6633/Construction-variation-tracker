"""Review tools: help prepare material for the human reviewer. They never record a decision."""

from __future__ import annotations

from google.adk.tools import ToolContext

from ..shared_libraries.constants import STATE_EXTRACTIONS, STATE_VARIATION
from ..shared_libraries.runtime import runtime_from_state
from .chronology_tools import detect_date_conflicts


def _events(tool_context: ToolContext) -> list[dict]:
    return [e for evs in (tool_context.state.get(STATE_EXTRACTIONS) or {}).values() for e in evs]


def prepare_unresolved_questions(tool_context: ToolContext) -> dict:
    """Deterministically list issues a reviewer must resolve: low confidence (<0.6), undated events,
    events with stated uncertainties, inferred (not literal) dates, date conflicts, and candidates
    that lack an instruction event. Use this to ground the questions you submit."""
    rt = runtime_from_state(tool_context.state)
    events = _events(tool_context)
    issues = []
    for e in events:
        reasons = []
        if e.get("confidence", 0) < 0.6:
            reasons.append("low model confidence (uncalibrated)")
        if not e.get("event_date"):
            reasons.append("no event date")
        if e.get("basis") != "explicitly_evidenced":
            reasons.append("date or detail not literally evidenced")
        if e.get("uncertainties"):
            reasons.append("stated uncertainties: " + "; ".join(e["uncertainties"][:3]))
        if reasons:
            issues.append({"event_ids": [e["event_id"]], "reasons": reasons})
    doc_dates = {
        d["document_id"]: d.get("recorded_event_date") for d in rt.provider.list_documents()
    }
    for c in detect_date_conflicts(events, doc_dates):
        issues.append({"event_ids": c["event_ids"], "reasons": [c["description"]]})
    kinds = {e["event_id"]: e["event_type"] for e in events}
    for c in (tool_context.state.get(STATE_VARIATION) or {}).get("candidates", []):
        if not any(
            kinds.get(i) in {"instruction_issued", "scope_change_request"} for i in c["event_ids"]
        ):
            issues.append(
                {
                    "candidate_ids": [c["candidate_id"]],
                    "reasons": ["no instruction or scope-change event is linked to this candidate"],
                }
            )
    return {"issues": issues[:60]}


def get_review_status(tool_context: ToolContext) -> dict:
    """Current human review status of the case (counts of accepted, edited, rejected items)."""
    return runtime_from_state(tool_context.state).provider.review_summary()


def generate_reviewer_checklist(tool_context: ToolContext) -> dict:
    """Return the reviewer checklist items for this case (fixed items plus case-specific ones)."""
    events = _events(tool_context)
    items = [
        "Open each cited source and confirm the quoted text and page.",
        "Confirm every event date against the document itself, not its upload time.",
        "Confirm parties, references and amounts; correct any that differ.",
        "Decide for each candidate: accept, edit (give a reason) or reject (give a reason).",
        "Confirm contractual notice periods and entitlement separately - not assessed here.",
    ]
    if any(not e.get("event_date") for e in events):
        items.append("Supply or confirm dates for undated events.")
    if any(e.get("basis") != "explicitly_evidenced" for e in events):
        items.append("Check events marked as not literally evidenced.")
    return {"checklist": items}


def summarise_review_history(tool_context: ToolContext) -> dict:
    """Summary of corrections and rejected candidates from earlier human review (may be empty)."""
    s = runtime_from_state(tool_context.state).provider.review_summary()
    return {"corrections": s.get("corrections", []), "rejected": s.get("rejected_items", [])}
