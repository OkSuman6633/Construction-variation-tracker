"""EvidenceProvider backed by a synthetic case profile (dev, tests, golden evaluation).

All profile content is FICTIONAL. Production uses the storage-backed provider in
app/analysis/provider.py instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime import ParsedDocument, ParsedPage

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def load_profile(name: str) -> dict[str, Any]:
    return json.loads((PROFILES_DIR / f"{name}.json").read_text(encoding="utf-8"))


class ProfileEvidenceProvider:
    def __init__(self, profile: dict[str, Any], review: dict[str, Any] | None = None) -> None:
        self.profile = profile
        self._docs = {d["document_id"]: d for d in profile["documents"]}
        self._review = review or {}

    @property
    def document_ids(self) -> list[str]:
        return list(self._docs)

    def case_context(self) -> dict[str, Any]:
        return self.profile["case_context"]

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "document_id": d["document_id"],
                "filename": d["filename"],
                "mime_type": d["mime_type"],
                "recorded_event_date": d.get("recorded_event_date"),
                "origin": d.get("origin"),
                "page_count": len(d["pages"]),
                "parser_status": "parsed",
            }
            for d in self.profile["documents"]
        ]

    def get_document(self, document_id: str) -> ParsedDocument:
        d = self._docs[document_id]
        return ParsedDocument(
            document_id,
            tuple(ParsedPage(p["page_ref"], p["text"]) for p in d["pages"]),
            {"source": "synthetic_profile", "text_layer": True},
        )

    def existing_events(self) -> list[dict[str, Any]]:
        return []

    def review_summary(self) -> dict[str, Any]:
        return self._review or {
            "accepted": 0,
            "edited": 0,
            "rejected": 0,
            "corrections": [],
            "rejected_items": [],
            "approved_references": [],
        }
