"""Deterministic document helpers: overview, classification hint, reference detection."""

from __future__ import annotations

import re
from typing import Any

from google.adk.tools import ToolContext

from ..shared_libraries.provenance import injection_flags
from ..shared_libraries.runtime import ScopeError, runtime_from_state
from .chronology_tools import find_dates_in_text
from .evidence_tools import UNTRUSTED_NOTICE, _denied

_MONTHS = {
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "SEPT",
    "OCT",
    "NOV",
    "DEC",
}
_REF = re.compile(r"\b([A-Z]{1,5}[- ]?\d{2,5}(?:[./-][A-Z0-9]{1,4})?)\b")
_AMOUNT = re.compile(r"(?:£|GBP\s?)\s?(\d[\d,]*(?:\.\d{1,2})?)")

_CATEGORY_KEYWORDS = {
    "instruction": [
        "instruction",
        "variation order",
        "instruct you",
        "please proceed",
        "cvi",
        "ai no",
    ],
    "quotation": ["quotation", "quote", "tender", "our price", "estimate"],
    "cost_breakdown": ["cost breakdown", "build-up", "schedule of rates", "rate", "total"],
    "site_diary": ["site diary", "daily report", "weather", "labour on site", "attendance"],
    "programme": ["programme", "milestone", "baseline", "completion date", "gantt"],
    "correspondence": ["from:", "subject:", "dear", "regards", "email"],
    "revised_drawing": ["drawing", "revision", "rev ", "dwg"],
    "contract_record": ["contract", "subcontract", "appendix", "clause"],
}


def detect_references(text: str) -> list[str]:
    found = []
    for m in _REF.finditer(text):
        tok = m.group(1)
        if tok.split()[0].upper() in _MONTHS or tok.upper().startswith(
            tuple(f"{x} " for x in _MONTHS)
        ):
            continue
        if tok not in found:
            found.append(tok)
    return found[:40]


def detect_amounts(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).strip() for m in _AMOUNT.finditer(text)))[:40]


def _full_text(rt: Any, document_id: str) -> str:
    return "\n".join(p.text for p in rt.provider.get_document(document_id).pages)


def get_document_overview(document_id: str, tool_context: ToolContext) -> dict:
    """Overview of one document: diagnostics, first 1,200 characters, and deterministic hints
    (detected references, dates and amounts). Hints are not facts - verify against the text."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
    except ScopeError as exc:
        return _denied(exc, tool_context)
    doc = rt.provider.get_document(document_id)
    meta = next((d for d in rt.provider.list_documents() if d["document_id"] == document_id), {})
    full = "\n".join(p.text for p in doc.pages)
    return {
        "notice": UNTRUSTED_NOTICE,
        "document_id": document_id,
        "metadata": meta,
        "diagnostics": doc.diagnostics,
        "page_count": len(doc.pages),
        "text_chars": len(full),
        "opening_text": full[:1200],
        "detected_references": detect_references(full),
        "detected_dates": find_dates_in_text(full)[:20],
        "detected_amounts": detect_amounts(full),
        "instruction_like_text_flags": injection_flags(full),
    }


def classify_document_hint(document_id: str, tool_context: ToolContext) -> dict:
    """Keyword-based category HINT (instruction, quotation, site_diary ...). Advisory only:
    the intake decision is yours and must rest on the document's actual content."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
    except ScopeError as exc:
        return _denied(exc, tool_context)
    text = _full_text(rt, document_id).casefold()
    scores = {c: sum(text.count(k) for k in kws) for c, kws in _CATEGORY_KEYWORDS.items()}
    ranked = sorted(((s, c) for c, s in scores.items() if s), reverse=True)
    return {
        "document_id": document_id,
        "hint_scores": {c: s for s, c in ranked[:4]},
        "hint": ranked[0][1] if ranked else "other",
    }


def find_document_references(document_id: str, tool_context: ToolContext) -> dict:
    """List reference-like tokens (e.g. VO-014, RFI-104) found in one document with the page each
    appears on."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
    except ScopeError as exc:
        return _denied(exc, tool_context)
    doc = rt.provider.get_document(document_id)
    refs: dict[str, list[str]] = {}
    for p in doc.pages:
        for r in detect_references(p.text):
            refs.setdefault(r, []).append(p.page_ref)
    return {"document_id": document_id, "references": refs}


def get_extraction_diagnostics(document_id: str, tool_context: ToolContext) -> dict:
    """Parser diagnostics for one document (text layer present, pages, warnings)."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
    except ScopeError as exc:
        return _denied(exc, tool_context)
    doc = rt.provider.get_document(document_id)
    return {"document_id": document_id, "diagnostics": doc.diagnostics, "pages": len(doc.pages)}
