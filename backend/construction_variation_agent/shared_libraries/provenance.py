"""Deterministic provenance checks: citation validity, injection flags, legal-conclusion guard.

These run OUTSIDE model authority. A high model confidence never substitutes for a
citation that verifiably exists in the cited document.
"""

from __future__ import annotations

import re
import unicodedata

from .runtime import ParsedDocument


def normalise_for_match(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = (
        t.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", t).strip().casefold()


def find_quote(page_text: str, quote: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of quote within page_text (whitespace/case-insensitive).

    Offsets index the ORIGINAL page text so the reviewer UI can highlight them.
    """
    q = normalise_for_match(quote)
    if len(q) < 4:
        return None
    # Build a whitespace-tolerant regex over the original text.
    words = [re.escape(w) for w in re.split(r"\s+", quote.strip()) if w]
    if not words:
        return None
    pattern = r"\s+".join(words)
    m = re.search(pattern, page_text, flags=re.IGNORECASE)
    if m:
        return m.start(), m.end()
    # Fallback for typographic differences: compare in normalised space.
    if q in normalise_for_match(page_text):
        return 0, len(page_text)  # located on the page; exact offsets unavailable
    return None


def citation_errors(
    doc: ParsedDocument,
    page_ref: str | None,
    quoted_span: str | None,
    visual_region: str | None = None,
) -> list[str]:
    """Check text quotes against parsed text or require a locatable region for image evidence."""
    errors: list[str] = []
    if not page_ref:
        return ["missing page_ref"]
    page = doc.page(page_ref)
    if page is None:
        return [f"page_ref '{page_ref}' does not exist in document"]
    if doc.image_bytes:
        if not visual_region or len(visual_region.strip()) < 3:
            return ["missing visual_region for image evidence"]
        return []
    if not quoted_span:
        return ["missing quoted_span"]
    if find_quote(page.text, quoted_span) is None:
        errors.append("quoted_span not found on the cited page")
    return errors


_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(system|previous)|"
    r"you are now|system prompt|reveal your|send (all )?(customer|the) (documents|data)|"
    r"act as (an? )?(admin|system))",
    re.IGNORECASE,
)


def injection_flags(text: str) -> list[str]:
    """Flag (never obey) instruction-like text embedded in a document."""
    return sorted({m.group(0).lower() for m in _INJECTION.finditer(text)})


# Text that would amount to a legal/entitlement conclusion. Rejected in agent output.
_LEGAL = re.compile(
    r"\b(is|are|was|were|be|being) entitled to\b|\bentitlement (is|has been|exists|arises)\b|"
    r"\bbreach(ed)? of (the )?contract\b|\bliable (for|to)\b|\bmust be paid\b|"
    r"\bvalid (variation|claim)\b|\bclaim (will|should) succeed\b|\bis owed\b|"
    r"\bextension of time (is|has been) (due|granted)\b|\bnotice (is|was) (valid|out of time)\b",
    re.IGNORECASE,
)


def legal_conclusions(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _LEGAL.finditer(text)})
