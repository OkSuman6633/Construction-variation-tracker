"""Reference tools: find links between documents inside the authorised case only."""

from __future__ import annotations

import difflib
import re
from typing import Any

from google.adk.tools import ToolContext

from ..shared_libraries.provenance import normalise_for_match
from ..shared_libraries.runtime import ScopeError, runtime_from_state
from .chronology_tools import find_dates_in_text
from .document_tools import detect_amounts, detect_references
from .evidence_tools import UNTRUSTED_NOTICE, _denied


def _hits(rt: Any, needle: str, limit: int = 20) -> list[dict[str, str]]:
    n = normalise_for_match(needle)
    out: list[dict[str, str]] = []
    if len(n) < 3:
        return out
    for did in rt.request.document_ids:
        for p in rt.provider.get_document(did).pages:
            norm = normalise_for_match(p.text)
            idx = norm.find(n)
            if idx >= 0:
                snippet = p.text[max(0, idx - 60) : idx + len(needle) + 100]
                out.append(
                    {
                        "document_id": did,
                        "page_ref": p.page_ref,
                        "snippet": re.sub(r"\s+", " ", snippet)[:200],
                    }
                )
                if len(out) >= limit:
                    return out
    return out


def find_related_document_identifiers(reference: str, tool_context: ToolContext) -> dict:
    """Find which documents in this case contain a given reference string (e.g. 'VO-014').
    Matching is on the literal reference, not on similar wording."""
    rt = runtime_from_state(tool_context.state)
    hits = _hits(rt, reference)
    return {
        "notice": UNTRUSTED_NOTICE,
        "reference": reference,
        "documents": sorted({h["document_id"] for h in hits}),
        "hits": hits,
    }


def search_case_references(query: str, tool_context: ToolContext) -> dict:
    """Search the authorised documents for a literal phrase. Returns up to 20 short snippets with
    document_id and page_ref. Only documents of this job are searched."""
    rt = runtime_from_state(tool_context.state)
    return {"notice": UNTRUSTED_NOTICE, "query": query, "hits": _hits(rt, query)}


def compare_document_revisions(
    document_id_a: str, document_id_b: str, tool_context: ToolContext
) -> dict:
    """Compare two documents (e.g. quotation revisions): added/removed lines (max 30) and the
    amounts, dates and references that differ. Output is a factual diff, not a conclusion."""
    rt = runtime_from_state(tool_context.state)
    try:
        rt.require_document(document_id_a)
        rt.require_document(document_id_b)
    except ScopeError as exc:
        return _denied(exc, tool_context)
    ta = "\n".join(p.text for p in rt.provider.get_document(document_id_a).pages)
    tb = "\n".join(p.text for p in rt.provider.get_document(document_id_b).pages)
    la, lb = (
        [x.strip() for x in ta.splitlines() if x.strip()],
        [x.strip() for x in tb.splitlines() if x.strip()],
    )
    diff = [
        d
        for d in difflib.unified_diff(la, lb, lineterm="", n=0)
        if d[:1] in "+-" and d[:3] not in ("+++", "---")
    ]
    set_a, set_b = set(detect_amounts(ta)), set(detect_amounts(tb))
    da = {d["iso"] for d in find_dates_in_text(ta)}
    db = {d["iso"] for d in find_dates_in_text(tb)}
    return {
        "notice": UNTRUSTED_NOTICE,
        "diff_lines": [d[:200] for d in diff[:30]],
        "amounts_only_in_a": sorted(set_a - set_b),
        "amounts_only_in_b": sorted(set_b - set_a),
        "dates_only_in_a": sorted(da - db),
        "dates_only_in_b": sorted(db - da),
        "shared_references": sorted(set(detect_references(ta)) & set(detect_references(tb))),
    }


def suggest_potential_relationships(tool_context: ToolContext) -> dict:
    """Deterministically rank document pairs that share at least one reference token. Shared
    references are CANDIDATE links for you to verify; similar wording alone is never a link."""
    rt = runtime_from_state(tool_context.state)
    refs = {
        d: set(detect_references("\n".join(p.text for p in rt.provider.get_document(d).pages)))
        for d in rt.request.document_ids
    }
    ids = list(refs)
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            shared = sorted(refs[a] & refs[b])
            if shared:
                pairs.append({"document_ids": [a, b], "shared_references": shared})
    pairs.sort(key=lambda p: -len(p["shared_references"]))
    return {"candidate_pairs": pairs[:40]}
