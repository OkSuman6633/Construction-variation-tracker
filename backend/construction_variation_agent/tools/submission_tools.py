"""Stage submission tools.

Each stage hands over its structured result by calling a submit_* tool. The tool validates
the payload, assigns ids SERVER-SIDE, and stores it in temporary ADK session state. Nothing
here writes to GCS or BigQuery: the backend persists results after final validation.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.tools import ToolContext
from pydantic import Field, ValidationError

from ..shared_libraries.constants import (
    STATE_CROSSREF,
    STATE_DOCUMENTS,
    STATE_EXTRACT_CURSOR,
    STATE_EXTRACT_DONE,
    STATE_EXTRACT_SEQ,
    STATE_EXTRACTIONS,
    STATE_INTAKE,
    STATE_PLAIN_ENGLISH_SUMMARY,
    STATE_REVIEW_PREP,
    STATE_VARIATION,
)
from ..shared_libraries.provenance import citation_errors, injection_flags, legal_conclusions
from ..shared_libraries.runtime import ScopeError, runtime_from_state
from ..shared_libraries.schemas import PlainEnglishSummary, ShortText, Strict
from ..shared_libraries.types import DocumentCategory
from .evidence_tools import UNTRUSTED_NOTICE
from .validation_tools import validate_event_payload

_ROUTES = {"extract_text", "vision_required", "skip_unsupported", "skip_irrelevant"}
_DATE_BASIS = {"stated_on_document", "from_user_metadata", "unknown"}


def _load(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc.msg}") from exc


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    return {"status": "rejected", "error": msg, **extra}


# ---------------------------------------------------------------- intake
class IntakeDocument(Strict):
    document_id: str
    category: DocumentCategory
    source_type: ShortText | None = None
    document_reference: ShortText | None = None
    document_date: str | None = None  # ISO date stated ON the document, else null
    date_basis: str = "unknown"
    author: ShortText | None = None
    parties: list[ShortText] = Field(default_factory=list, max_length=10)
    revision: ShortText | None = None
    missing_metadata: list[ShortText] = Field(default_factory=list, max_length=10)
    parser_route: str = "extract_text"
    issues: list[ShortText] = Field(default_factory=list, max_length=10)


def submit_intake(documents_json: str, tool_context: ToolContext) -> dict:
    """Submit the intake classification for EVERY document of the job as a JSON array of objects
    {document_id, category, source_type, document_reference, document_date (ISO or null),
    date_basis (stated_on_document|from_user_metadata|unknown), author, parties, revision,
    missing_metadata, parser_route (extract_text|vision_required|skip_unsupported|skip_irrelevant),
    issues}. Upload time must never be used as the document date."""
    rt = runtime_from_state(tool_context.state)
    try:
        raw = _load(documents_json)
        if not isinstance(raw, list):
            return _err("documents_json must be a JSON array")
        docs = [IntakeDocument.model_validate(x) for x in raw]
    except (ValueError, ValidationError) as exc:
        return _err(str(exc)[:600])
    ids = [d.document_id for d in docs]
    unknown = sorted(set(ids) - set(rt.request.document_ids))
    missing = sorted(set(rt.request.document_ids) - set(ids))
    if unknown:
        return _err("document ids outside the authorised scope", unknown=unknown)
    if missing or len(ids) != len(set(ids)):
        return _err("every authorised document must appear exactly once", missing=missing)
    for d in docs:
        if d.parser_route not in _ROUTES or d.date_basis not in _DATE_BASIS:
            return _err(f"invalid parser_route/date_basis for {d.document_id}")
        if rt.provider.get_document(d.document_id).image_bytes:
            d.parser_route = "vision_required"
    tool_context.state[STATE_INTAKE] = {"documents": [d.model_dump(mode="json") for d in docs]}
    return {"status": "accepted", "documents": len(docs)}


# ---------------------------------------------------------------- extraction
def next_document_to_extract(tool_context: ToolContext) -> dict:
    """Return the next document to extract, or {done: true} when none remain (this ends the
    extraction loop). Call this at the start of every extraction turn."""
    rt = runtime_from_state(tool_context.state)
    intake = (tool_context.state.get(STATE_INTAKE) or {}).get("documents", [])
    routes = {d["document_id"]: d for d in intake}
    queue = [
        d
        for d in tool_context.state.get(STATE_DOCUMENTS, [])
        if routes.get(d, {}).get("parser_route", "extract_text")
        in {"extract_text", "vision_required"}
    ]
    i = tool_context.state.get(STATE_EXTRACT_CURSOR, 0)
    if i >= len(queue):
        tool_context.actions.escalate = True
        return {"done": True}
    tool_context.state[STATE_EXTRACT_CURSOR] = i + 1
    did = queue[i]
    doc = rt.provider.get_document(did)
    pages = []
    for p in doc.pages[: rt.limits.max_pages_per_call]:
        text = p.text[: rt.limits.max_chars_per_page]
        pages.append(
            {
                "page_ref": p.page_ref,
                "text": text,
                "truncated": len(p.text) > len(text),
                "instruction_like_text_flags": injection_flags(p.text),
            }
        )
    result = {
        "notice": UNTRUSTED_NOTICE,
        "done": False,
        "document_id": did,
        "total_pages": len(doc.pages),
        "page_refs": [p.page_ref for p in doc.pages],
        "pages": pages,
        "intake": routes.get(did, {}),
    }
    if doc.image_bytes:
        result["visual_input"] = (
            "The original image is attached to this extraction turn. Inspect it visually. "
            "Cite the visible region, not a fabricated text quote."
        )
    return result


def submit_document_extraction(
    document_id: str, events_json: str, tool_context: ToolContext
) -> dict:
    """Submit extracted events for ONE document as a JSON array. Each event: {event_type,
    event_date (as written in the document or ISO, else null), description, parties, references,
    amount (number or null), currency (3-letter code or null), page_ref, quoted_span (verbatim from
    that page, at least 4 chars), confidence (0-1), uncertainties}. For images use visual_region
    and quoted_span=null; image amounts require verification against text evidence. Events whose
    citations are invalid or that state legal conclusions are REJECTED with reasons - fix and
    resubmit only those. An empty array is valid if nothing is extractable."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id)
        raw = _load(events_json)
        accepted, rejected = validate_event_payload(rt, document_id, raw)
    except ScopeError as exc:
        return {"status": "rejected", "error": "DOCUMENT_NOT_IN_SCOPE", "message": str(exc)}
    except ValueError as exc:
        return _err(str(exc))
    seq = int(tool_context.state.get(STATE_EXTRACT_SEQ, 0))
    store = dict(tool_context.state.get(STATE_EXTRACTIONS) or {})
    bucket = list(store.get(document_id, []))
    ids = []
    for ev in accepted:
        seq += 1
        ev["event_id"] = f"evt-{seq:04d}"
        ids.append(ev["event_id"])
        bucket.append(ev)
    store[document_id] = bucket
    tool_context.state[STATE_EXTRACTIONS] = store
    tool_context.state[STATE_EXTRACT_SEQ] = seq
    if not rejected:
        completed = set(tool_context.state.get(STATE_EXTRACT_DONE) or [])
        completed.add(document_id)
        tool_context.state[STATE_EXTRACT_DONE] = sorted(completed)
    return {
        "status": "accepted" if not rejected else "partially_accepted",
        "accepted_event_ids": ids,
        "rejected": rejected,
    }


# ---------------------------------------------------------------- cross-reference
def submit_cross_references(
    relationships_json: str, contradictions_json: str, tool_context: ToolContext
) -> dict:
    """Submit document relationships and proposed contradictions (each a JSON array; use [] if none).
    relationships: {document_ids (>=2), relation (revision_of|attachment_of|responds_to|references),
    shared_references (literal tokens present in the documents), rationale, confidence}.
    contradictions: {kind (date|amount|reference|scope|other), description, evidence:[{document_id,
    page_ref, quoted_span, visual_region}]} - text quotes must literally exist; image references
    require visual_region. Relationships without a verified shared reference are kept with reduced
    confidence and flagged."""
    rt = runtime_from_state(tool_context.state)
    try:
        rels, cons = _load(relationships_json), _load(contradictions_json)
        if not isinstance(rels, list) or not isinstance(cons, list):
            return _err("both arguments must be JSON arrays")
    except ValueError as exc:
        return _err(str(exc))
    out_rels, out_cons, rejected = [], [], []
    for i, r in enumerate(rels):
        try:
            ids = list(r["document_ids"])
            if len(set(ids)) < 2 or not set(ids) <= set(rt.request.document_ids):
                raise ValueError("document_ids must be >=2 distinct authorised documents")
            text = {
                d: "\n".join(p.text for p in rt.provider.get_document(d).pages).casefold()
                for d in ids
            }
            shared = [
                s
                for s in r.get("shared_references", [])
                if all(s.casefold() in text[d] for d in ids)
            ]
            conf = float(r.get("confidence", 0.0))
            conf = max(0.0, min(conf, 0.95))
            notes = []
            if not shared:
                conf = min(conf, 0.4)
                notes.append("no shared reference verified in all documents")
            rationale = str(r["rationale"])[:2000]
            if legal_conclusions(rationale):
                raise ValueError("legal conclusion language not allowed")
            out_rels.append(
                {
                    "document_ids": ids,
                    "relation": str(r["relation"])[:100],
                    "shared_references": shared,
                    "rationale": rationale,
                    "confidence": conf,
                    "flags": notes,
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"kind": "relationship", "index": i, "errors": [str(exc)]})
    for i, c in enumerate(cons):
        try:
            if c["kind"] not in {"date", "amount", "reference", "scope", "other"}:
                raise ValueError("invalid kind")
            ev = []
            for e in c["evidence"]:
                rt.require_document(e["document_id"])
                errs = citation_errors(
                    rt.provider.get_document(e["document_id"]),
                    e.get("page_ref"),
                    e.get("quoted_span"),
                    e.get("visual_region"),
                )
                if errs:
                    raise ValueError(f"evidence citation invalid: {errs}")
                ev.append(
                    {
                        "document_id": e["document_id"],
                        "page_ref": e["page_ref"],
                        "quoted_span": e.get("quoted_span"),
                        "visual_region": e.get("visual_region"),
                    }
                )
            if not ev:
                raise ValueError("at least one evidence reference required")
            desc = str(c["description"])[:2000]
            if legal_conclusions(desc):
                raise ValueError("legal conclusion language not allowed")
            out_cons.append({"kind": c["kind"], "description": desc, "evidence": ev})
        except (KeyError, ValueError, TypeError, ScopeError) as exc:
            rejected.append({"kind": "contradiction", "index": i, "errors": [str(exc)]})
    for n, r in enumerate(out_rels, 1):
        r["relationship_id"] = f"rel-{n:04d}"
    for n, c in enumerate(out_cons, 1):
        c["contradiction_id"] = f"ctr-agent-{n:04d}"
    tool_context.state[STATE_CROSSREF] = {"relationships": out_rels, "contradictions": out_cons}
    return {
        "status": "accepted" if not rejected else "partially_accepted",
        "relationships": len(out_rels),
        "contradictions": len(out_cons),
        "rejected": rejected,
    }


# ---------------------------------------------------------------- variation analysis
def submit_variation_analysis(
    candidates_json: str, missing_evidence_json: str, tool_context: ToolContext
) -> dict:
    """Submit potential variation candidates and missing evidence (JSON arrays; [] if none).
    candidate: {title, alleged_original_scope, proposed_scope_delta, related_instruction_refs,
    quantity_text, amount, currency, event_ids (>=1 recorded event ids), confidence}. Amounts must equal
    an amount on a linked event. Candidates describe a POSSIBLE change for human review - never
    entitlement, liability or validity. missing_evidence: {description, expected_document_kind,
    related_event_ids}."""
    runtime_from_state(tool_context.state)  # enforces the authorised job scope
    events = {
        e["event_id"]: e
        for evs in (tool_context.state.get(STATE_EXTRACTIONS) or {}).values()
        for e in evs
    }
    try:
        cands, miss = _load(candidates_json), _load(missing_evidence_json)
        if not isinstance(cands, list) or not isinstance(miss, list):
            return _err("both arguments must be JSON arrays")
    except ValueError as exc:
        return _err(str(exc))
    out_c, out_m, rejected = [], [], []
    for i, c in enumerate(cands):
        try:
            ev_ids = list(c["event_ids"])
            if not ev_ids or not set(ev_ids) <= set(events):
                raise ValueError("event_ids must be non-empty and reference recorded events")
            for field in (
                "title",
                "alleged_original_scope",
                "proposed_scope_delta",
                "quantity_text",
            ):
                if c.get(field) and (lc := legal_conclusions(str(c[field]))):
                    raise ValueError(f"{field}: legal conclusion language not allowed {lc}")
            amount, currency, notes = c.get("amount"), c.get("currency"), []
            linked = [(events[e].get("amount"), events[e].get("currency")) for e in ev_ids]
            if amount is not None and (float(amount), currency) not in [
                (a, cur) for a, cur in linked if a is not None
            ]:
                amount, currency = None, None
                notes.append("amount removed: not an amount recorded on any linked event")
            evidence = [ref for e in ev_ids for ref in events[e]["evidence"]]
            out_c.append(
                {
                    "title": str(c["title"])[:500],
                    "alleged_original_scope": (
                        str(c["alleged_original_scope"])[:5000]
                        if c.get("alleged_original_scope")
                        else None
                    ),
                    "proposed_scope_delta": (
                        str(c["proposed_scope_delta"])[:5000]
                        if c.get("proposed_scope_delta")
                        else None
                    ),
                    "related_instruction_refs": [
                        str(x)[:200] for x in c.get("related_instruction_refs", [])
                    ][:10],
                    "quantity_text": (
                        str(c["quantity_text"])[:500] if c.get("quantity_text") else None
                    ),
                    "amount": float(amount) if amount is not None else None,
                    "currency": currency if amount is not None else None,
                    "event_ids": ev_ids,
                    "evidence": evidence,
                    "basis": "ai_inferred_relationship",
                    "confidence": max(0.0, min(float(c.get("confidence", 0.0)), 0.9)),
                    "flags": notes,
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"kind": "candidate", "index": i, "errors": [str(exc)]})
    for i, m in enumerate(miss):
        try:
            if legal_conclusions(str(m["description"])):
                raise ValueError("legal conclusion language not allowed")
            kind = m.get("expected_document_kind")
            if kind is not None:
                DocumentCategory(kind)
            rel = [e for e in m.get("related_event_ids", []) if e in events]
            out_m.append(
                {
                    "description": str(m["description"])[:2000],
                    "expected_document_kind": kind,
                    "related_event_ids": rel,
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"kind": "missing_evidence", "index": i, "errors": [str(exc)]})
    for n, c in enumerate(out_c, 1):
        c["candidate_id"] = f"cand-{n:04d}"
    for n, m in enumerate(out_m, 1):
        m["missing_id"] = f"miss-{n:04d}"
    tool_context.state[STATE_VARIATION] = {"candidates": out_c, "missing_evidence": out_m}
    return {
        "status": "accepted" if not rejected else "partially_accepted",
        "candidates": len(out_c),
        "missing_evidence": len(out_m),
        "rejected": rejected,
    }


# ---------------------------------------------------------------- review preparation
def submit_review_preparation(questions_json: str, tool_context: ToolContext) -> dict:
    """Submit reviewer questions as a JSON array of {question, related_event_ids,
    related_candidate_ids}. Questions ask the human to confirm or supply information; they must not
    state conclusions. Ids must reference recorded events/candidates."""
    events = {
        e["event_id"]
        for evs in (tool_context.state.get(STATE_EXTRACTIONS) or {}).values()
        for e in evs
    }
    cands = {
        c["candidate_id"]
        for c in (tool_context.state.get(STATE_VARIATION) or {}).get("candidates", [])
    }
    try:
        raw = _load(questions_json)
        if not isinstance(raw, list):
            return _err("questions_json must be a JSON array")
    except ValueError as exc:
        return _err(str(exc))
    out, rejected = [], []
    for i, q in enumerate(raw):
        try:
            text = str(q["question"]).strip()
            if not text or legal_conclusions(text):
                raise ValueError("empty question or legal conclusion language")
            out.append(
                {
                    "question": text[:2000],
                    "related_event_ids": [e for e in q.get("related_event_ids", []) if e in events],
                    "related_candidate_ids": [
                        c for c in q.get("related_candidate_ids", []) if c in cands
                    ],
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"index": i, "errors": [str(exc)]})
    for n, q in enumerate(out, 1):
        q["question_id"] = f"qst-{n:04d}"
    tool_context.state[STATE_REVIEW_PREP] = {"questions": out}
    return {
        "status": "accepted" if not rejected else "partially_accepted",
        "questions": len(out),
        "rejected": rejected,
    }


def submit_plain_english_summary(summary_json: str, tool_context: ToolContext) -> dict:
    """Submit one plain-English report paragraph as JSON with text, event_ids and candidate_ids.
    The paragraph must be 70 words or fewer; ids must come from this case's analysis."""
    runtime_from_state(tool_context.state)
    try:
        summary = PlainEnglishSummary.model_validate(_load(summary_json))
    except (ValueError, ValidationError) as exc:
        return _err(str(exc)[:600])
    if legal_conclusions(summary.text):
        return _err("summary contains legal-conclusion language")

    events = {
        event["event_id"]
        for document_events in (tool_context.state.get(STATE_EXTRACTIONS) or {}).values()
        for event in document_events
    }
    candidates = {
        candidate["candidate_id"]
        for candidate in (tool_context.state.get(STATE_VARIATION) or {}).get("candidates", [])
    }
    if not set(summary.event_ids) <= events:
        return _err("summary references an event outside this analysis")
    if not set(summary.candidate_ids) <= candidates:
        return _err("summary references a candidate outside this analysis")

    tool_context.state[STATE_PLAIN_ENGLISH_SUMMARY] = summary.model_dump(mode="json")
    return {
        "status": "accepted",
        "words": len(summary.text.split()),
        "event_references": len(summary.event_ids),
        "candidate_references": len(summary.candidate_ids),
    }
