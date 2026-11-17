"""Chronology tools and the pure date/ordering/conflict logic used by the evidence engine.

Pure functions (top of file) are shared with the backend's deterministic engine.
ADK tool wrappers (bottom) read session state only.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from google.adk.tools import ToolContext

from ..shared_libraries.constants import STATE_EXTRACTIONS

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}
_ORD = r"(?:st|nd|rd|th)?"


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def normalise_date(text: str) -> dict[str, Any]:
    """Parse a date string. UK day-first is assumed for numeric forms and FLAGGED.

    Returns {"iso": str|None, "assumptions": [..], "ambiguous": bool}. Never guesses a
    date that is not present; an unparseable input returns iso=None.
    """
    t = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m and (d := _valid(int(m[1]), int(m[2]), int(m[3]))):
        return {"iso": d.isoformat(), "assumptions": [], "ambiguous": False}
    m = re.fullmatch(rf"(\d{{1,2}}){_ORD}\s+([A-Za-z]{{3,9}})\.?,?\s+(\d{{4}})", t)
    if m and m[2].lower() in _MONTHS and (d := _valid(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))):
        return {"iso": d.isoformat(), "assumptions": [], "ambiguous": False}
    m = re.fullmatch(rf"([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})", t)
    if m and m[1].lower() in _MONTHS and (d := _valid(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))):
        return {"iso": d.isoformat(), "assumptions": [], "ambiguous": False}
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        d = _valid(y, b, a)  # day-first
        if d:
            amb = a <= 12 and b <= 12 and a != b
            notes = ["numeric date interpreted day-first (UK convention)"]
            if amb:
                notes.append("ambiguous: could be month-first")
            return {"iso": d.isoformat(), "assumptions": notes, "ambiguous": amb}
    return {"iso": None, "assumptions": ["unparseable date"], "ambiguous": False}


_DATE_IN_TEXT = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|"
    r"[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\b"
)


def find_dates_in_text(text: str) -> list[dict[str, Any]]:
    out = []
    for m in _DATE_IN_TEXT.finditer(text):
        n = normalise_date(m.group(1))
        if n["iso"]:
            out.append(
                {
                    "text": m.group(1),
                    "iso": n["iso"],
                    "offset": m.start(),
                    "ambiguous": n["ambiguous"],
                }
            )
    return out


def sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chronological order; undated events last, stable by document then id."""
    return sorted(
        events,
        key=lambda e: (
            e.get("event_date") is None,
            e.get("event_date") or "",
            e.get("document_id") or "",
            e.get("event_id") or "",
        ),
    )


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.casefold())


def find_duplicate_events(events: list[dict[str, Any]]) -> list[list[str]]:
    groups: dict[tuple, list[str]] = defaultdict(list)
    for e in events:
        key = (e.get("event_type"), e.get("event_date"), _norm(e.get("description", ""))[:80])
        groups[key].append(e["event_id"])
    return [ids for ids in groups.values() if len(ids) > 1]


def detect_date_conflicts(
    events: list[dict[str, Any]], doc_dates: dict[str, str | None]
) -> list[dict[str, Any]]:
    """Deterministic date conflicts. Returns dicts with kind='date', description, event_ids.

    Rule A: an event that records the document's own issue (instruction/quotation/
            correspondence/site record) disagrees with the document's recorded date.
    Rule B: the same reference + event type is dated differently across documents.
    """
    conflicts: list[dict[str, Any]] = []
    self_dated = {
        "instruction_issued",
        "quotation_issued",
        "quotation_revised",
        "correspondence",
        "site_record",
    }
    for e in events:
        rec = doc_dates.get(e.get("document_id") or "")
        if (
            e.get("event_type") in self_dated
            and rec
            and e.get("event_date")
            and rec != e["event_date"]
        ):
            conflicts.append(
                {
                    "kind": "date",
                    "event_ids": [e["event_id"]],
                    "description": (
                        f"Event date {e['event_date']} differs from the recorded date {rec} "
                        f"of its source document."
                    ),
                }
            )
    by_ref: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("event_date"):
            for ref in e.get("references") or []:
                by_ref[(_norm(ref), e.get("event_type"))].append(e)
    for (_, etype), group in by_ref.items():
        dates = {g["event_date"] for g in group}
        docs = {g.get("document_id") for g in group}
        if len(dates) > 1 and len(docs) > 1:
            ref_txt = (group[0].get("references") or ["?"])[0]
            conflicts.append(
                {
                    "kind": "date",
                    "event_ids": [g["event_id"] for g in group],
                    "description": (
                        f"Reference '{ref_txt}' ({etype}) is dated differently across documents: "
                        + ", ".join(sorted(dates))
                        + "."
                    ),
                }
            )
    return conflicts


def detect_amount_conflicts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ref: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("amount") is not None and e.get("event_type") != "quotation_revised":
            for ref in e.get("references") or []:
                by_ref[(_norm(ref), e.get("event_type"), e.get("currency"))].append(e)
    out = []
    for group in by_ref.values():
        amounts = {g["amount"] for g in group}
        if len(amounts) > 1 and len({g.get("document_id") for g in group}) > 1:
            ref_txt = (group[0].get("references") or ["?"])[0]
            out.append(
                {
                    "kind": "amount",
                    "event_ids": [g["event_id"] for g in group],
                    "description": f"Reference '{ref_txt}' carries different amounts across documents: "
                    + ", ".join(str(a) for a in sorted(amounts))
                    + ".",
                }
            )
    return out


# ------------------------------------------------------------------ ADK tool wrappers
def _recorded_events(tool_context: ToolContext) -> list[dict[str, Any]]:
    ex = tool_context.state.get(STATE_EXTRACTIONS, {}) or {}
    return [e for evs in ex.values() for e in evs]


def normalise_date_tool(text: str, tool_context: ToolContext) -> dict:
    """Normalise a date string found in a document to ISO format (UK day-first assumed).

    Use this instead of converting dates yourself. Returns the ISO date or null plus any
    assumptions or ambiguity. Allowed use: dates that appear in evidence text.
    """
    return normalise_date(text)


def sort_recorded_events(tool_context: ToolContext) -> dict:
    """Return all events recorded so far in chronological order (undated last)."""
    evs = sort_events(_recorded_events(tool_context))
    return {
        "events": [
            {
                k: e.get(k)
                for k in ("event_id", "event_date", "event_type", "document_id", "description")
            }
            for e in evs
        ]
    }


def detect_recorded_date_conflicts(tool_context: ToolContext) -> dict:
    """Run the deterministic date-conflict rules over recorded events and return warnings."""
    from ..shared_libraries.runtime import runtime_from_state

    rt = runtime_from_state(tool_context.state)
    doc_dates = {
        d["document_id"]: d.get("recorded_event_date") for d in rt.provider.list_documents()
    }
    events = _recorded_events(tool_context)
    return {"conflicts": detect_date_conflicts(events, doc_dates), "warnings_only": True}


def find_recorded_duplicate_events(tool_context: ToolContext) -> dict:
    """Return groups of recorded events that look like duplicates (same type, date, wording)."""
    return {"duplicate_groups": find_duplicate_events(_recorded_events(tool_context))}
