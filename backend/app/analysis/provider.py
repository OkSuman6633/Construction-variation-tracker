"""Storage-backed EvidenceProvider for ONE case. Verifies file integrity before parsing."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from construction_variation_agent.shared_libraries.runtime import ParsedDocument, ScopeError
from construction_variation_agent.shared_libraries.schemas import CaseState

from ..parsing.parsers import parse_document
from ..storage import paths
from ..storage.base import ObjectStore


class IntegrityError(Exception):
    """Stored bytes no longer match the registered SHA-256."""


class StorageEvidenceProvider:
    def __init__(self, store: ObjectStore, case: CaseState, document_ids: list[str]) -> None:
        self.store, self.case = store, case
        self._meta = {d.document_id: d for d in case.evidence}
        self._allowed = [d for d in document_ids if d in self._meta]
        self._cache: dict[str, ParsedDocument] = {}

    def case_context(self) -> dict[str, Any]:
        c = self.case
        return {
            "title": c.title,
            "alleged_change": c.alleged_change,
            "parties": [p.model_dump() for p in c.parties],
            "contract": c.contract.model_dump(mode="json"),
            "key_dates": {k: v.isoformat() for k, v in c.key_dates.items()},
        }

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "document_id": d,
                "filename": self._meta[d].filename,
                "mime_type": self._meta[d].mime_type,
                "recorded_event_date": (
                    rd.isoformat() if (rd := self._meta[d].recorded_event_date) else None
                ),
                "origin": self._meta[d].origin,
                "page_count": self._meta[d].page_count,
                "parser_status": self._meta[d].parser_status.value,
            }
            for d in self._allowed
        ]

    def get_document(self, document_id: str) -> ParsedDocument:
        if document_id not in self._allowed:
            raise ScopeError("document is not part of this authorised analysis")
        if document_id not in self._cache:
            meta = self._meta[document_id]
            obj = self.store.get(paths.upload(self.case.tenant_id, self.case.case_id, document_id))
            if hashlib.sha256(obj.data).hexdigest() != meta.sha256:
                raise IntegrityError(f"checksum mismatch for {document_id}")
            parsed = parse_document(document_id, meta.mime_type, obj.data)
            self._cache[document_id] = replace(
                parsed,
                mime_type=parsed.mime_type or meta.mime_type,
                image_bytes=parsed.image_bytes,
            )
        return self._cache[document_id]

    def existing_events(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self.case.events]

    def review_summary(self) -> dict[str, Any]:
        r = self.case.reviews
        by = lambda s: [x for x in r if x.decision.value == s]  # noqa: E731
        return {
            "accepted": len(by("accepted")),
            "edited": len(by("edited")),
            "rejected": len(by("rejected")),
            "corrections": [
                {"target_id": x.target_id, "corrections": x.corrections, "reason": x.reason}
                for x in by("edited")
            ],
            "rejected_items": [
                {"target_id": x.target_id, "reason": x.reason} for x in by("rejected")
            ],
            "approved_references": [],
        }
