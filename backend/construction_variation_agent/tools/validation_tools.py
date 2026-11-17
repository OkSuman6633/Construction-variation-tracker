"""Validation tools and the reusable deterministic checks behind every submission.

The checks (top) are pure and shared with the backend engine; the ADK tool wrappers
(bottom) let an agent dry-run them before submitting.
"""

from __future__ import annotations

import json
import re
from typing import Any

from google.adk.tools import ToolContext
from pydantic import Field, ValidationError

from ..shared_libraries.provenance import (
    citation_errors,
    find_quote,
    injection_flags,
    legal_conclusions,
)
from ..shared_libraries.runtime import JobRuntime, ScopeError, runtime_from_state
from ..shared_libraries.schemas import EvidenceReference, LongText, ShortText, Strict
from ..shared_libraries.types import EventType, EvidenceBasis
from .chronology_tools import find_dates_in_text, normalise_date

MAX_EVENTS_PER_SUBMISSION = 40
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


class ExtractedEventInput(Strict):
    """Agent-facing event. No ids, no review status, no basis: the backend assigns those."""

    event_type: EventType
    event_date: str | None = None
    description: LongText
    parties: list[ShortText] = Field(default_factory=list, max_length=10)
    references: list[ShortText] = Field(default_factory=list, max_length=10)
    amount: float | None = None
    currency: str | None = None
    page_ref: ShortText
    quoted_span: str | None = Field(default=None, min_length=4, max_length=1000)
    visual_region: ShortText | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=10)


def page_numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM.finditer(text):
        try:
            out.add(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def validate_event_payload(
    rt: JobRuntime, document_id: str, raw: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (accepted, rejected). Accepted events have verified citations; amounts and dates
    that are not literally supported are rejected or downgraded - never invented."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return [], [{"index": -1, "errors": ["events_json must be a JSON array"]}]
    if len(raw) > MAX_EVENTS_PER_SUBMISSION:
        return [], [
            {"index": -1, "errors": [f"at most {MAX_EVENTS_PER_SUBMISSION} events per call"]}
        ]
    rt.require_document(document_id)
    doc = rt.provider.get_document(document_id)
    for i, item in enumerate(raw):
        try:
            ev = ExtractedEventInput.model_validate(item)
        except ValidationError as exc:
            rejected.append(
                {
                    "index": i,
                    "errors": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()],
                }
            )
            continue
        errs = citation_errors(doc, ev.page_ref, ev.quoted_span, ev.visual_region)
        text_fields = [ev.description, *ev.uncertainties]
        for t in text_fields:
            if lc := legal_conclusions(t):
                errs.append(f"legal conclusion language not allowed: {lc}")
        currency = ev.currency
        if currency in {"£", "GBP", "gbp"}:
            currency = "GBP"
        if ev.amount is not None and (currency is None or len(currency) != 3):
            errs.append("amount requires a 3-letter currency code")
        page = doc.page(ev.page_ref)
        if doc.image_bytes and ev.amount is not None:
            errs.append("amounts from image-only sources require verification against text evidence")
        elif page is not None and ev.amount is not None and ev.amount not in page_numbers(page.text):
            errs.append("amount does not appear on the cited page")
        if errs:
            rejected.append({"index": i, "errors": errs})
            continue
        uncertainties = list(ev.uncertainties)
        basis = EvidenceBasis.EXPLICITLY_EVIDENCED
        confidence = min(ev.confidence, 0.95)
        if doc.image_bytes:
            basis = EvidenceBasis.AI_INFERRED
            confidence = min(confidence, 0.5)
            uncertainties.append(
                "Visual observation is model-derived; verify it directly against the original image."
            )
        iso = None
        if ev.event_date:
            norm = normalise_date(ev.event_date)
            iso = norm["iso"]
            if iso is None:
                uncertainties.append(f"date '{ev.event_date}' could not be normalised")
            else:
                uncertainties += norm["assumptions"]
                on_page = {d["iso"] for d in find_dates_in_text(page.text)} if page else set()
                if iso not in on_page:
                    basis = EvidenceBasis.AI_INFERRED
                    confidence = min(confidence, 0.5)
                    uncertainties.append("date not found literally on the cited page")
        span = find_quote(page.text, ev.quoted_span) if page and ev.quoted_span else None
        if (ev.quoted_span and injection_flags(ev.quoted_span)) or injection_flags(ev.description):
            uncertainties.append("source contains instruction-like text (treated as data)")
        accepted.append(
            {
                "event_type": ev.event_type.value,
                "event_date": iso,
                "description": ev.description,
                "parties": ev.parties,
                "references": ev.references,
                "amount": ev.amount,
                "currency": currency if ev.amount is not None else None,
                "document_id": document_id,
                "page_ref": ev.page_ref,
                "quoted_span": None if doc.image_bytes else ev.quoted_span,
                "visual_region": ev.visual_region,
                "evidence": [
                    EvidenceReference(
                        document_id=document_id,
                        page_ref=ev.page_ref,
                        quoted_span=None if doc.image_bytes else ev.quoted_span,
                        visual_region=ev.visual_region,
                        offset_start=span[0] if span else None,
                        offset_end=span[1] if span else None,
                    ).model_dump(mode="json")
                ],
                "basis": basis.value,
                "confidence": confidence,
                "uncertainties": uncertainties,
                "review_status": "needs_review",
            }
        )
    return accepted, rejected


# ------------------------------------------------------------------ ADK tool wrappers
def validate_extraction_schema(
    document_id: str, events_json: str, tool_context: ToolContext
) -> dict:
    """DRY-RUN: validate a JSON array of events for one document without storing anything.
    Returns accepted_count and, for each rejected event, the exact errors to fix."""
    rt = runtime_from_state(tool_context.state)
    try:
        raw = json.loads(events_json)
        acc, rej = validate_event_payload(rt, document_id, raw)
    except (json.JSONDecodeError, ScopeError) as exc:
        return {"error": "INVALID_INPUT", "message": str(exc)}
    return {"accepted_count": len(acc), "rejected": rej}


def check_source_reference(
    document_id: str,
    page_ref: str,
    quoted_span: str | None,
    tool_context: ToolContext,
    visual_region: str | None = None,
) -> dict:
    """Check a text quote or image region against the authorised source."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
    except ScopeError as exc:
        return {"error": "DOCUMENT_NOT_IN_SCOPE", "message": str(exc)}
    errs = citation_errors(
        rt.provider.get_document(document_id), page_ref, quoted_span, visual_region
    )
    return {"valid": not errs, "errors": errs}


def check_date_consistency(date_iso: str, document_id: str, tool_context: ToolContext) -> dict:
    """Compare a proposed ISO date with the document's recorded date and with dates that literally
    appear in the document. Returns notes; it does not decide which date is right."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
    except ScopeError as exc:
        return {"error": "DOCUMENT_NOT_IN_SCOPE", "message": str(exc)}
    meta = next((d for d in rt.provider.list_documents() if d["document_id"] == document_id), {})
    text = "\n".join(p.text for p in rt.provider.get_document(document_id).pages)
    on_doc = sorted({d["iso"] for d in find_dates_in_text(text)})
    notes = []
    rec = meta.get("recorded_event_date")
    if rec and rec != date_iso:
        notes.append(f"differs from the recorded document date {rec}")
    if date_iso not in on_doc:
        notes.append("does not literally appear in the document")
    return {
        "date": date_iso,
        "recorded_document_date": rec,
        "dates_in_document": on_doc,
        "notes": notes,
    }


def missing_reference_candidates(rt: JobRuntime) -> list[dict[str, Any]]:
    """References mentioned in some documents but never the subject (first lines) of any document."""
    from .document_tools import detect_references

    mentioned: dict[str, set[str]] = {}
    titles: set[str] = set()
    for did in rt.request.document_ids:
        doc = rt.provider.get_document(did)
        full = "\n".join(p.text for p in doc.pages)
        titles |= set(detect_references("\n".join(full.splitlines()[:3])))
        for r in detect_references(full):
            mentioned.setdefault(r, set()).add(did)
    return [
        {"reference": r, "mentioned_in": sorted(d)}
        for r, d in sorted(mentioned.items())
        if r not in titles
    ]


def check_missing_references(tool_context: ToolContext) -> dict:
    """List instruction/drawing/quote reference tokens that are mentioned in some documents but are
    not the subject (title lines) of any document in the case - candidates for missing evidence."""
    rt = runtime_from_state(tool_context.state)
    return {"possibly_missing_documents": missing_reference_candidates(rt)[:30]}


def check_unsupported_claims(text: str, tool_context: ToolContext) -> dict:
    """Check wording you intend to submit for legal-conclusion language (entitlement, breach,
    liability) or embedded instruction text. Rewrite as neutral fact if anything is flagged."""
    return {
        "legal_conclusion_phrases": legal_conclusions(text),
        "instruction_like_text": injection_flags(text),
    }
