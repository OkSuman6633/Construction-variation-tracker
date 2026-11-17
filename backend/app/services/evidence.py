"""Evidence upload and registration: checksum, MIME sniffing, duplicates, scan gate, parsing."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any

from construction_variation_agent.shared_libraries.constants import ALLOWED_UPLOAD_MIME
from construction_variation_agent.shared_libraries.schemas import DocumentMetadata
from construction_variation_agent.shared_libraries.types import CaseStage, ParserStatus

from ..auth import TenantContext
from ..config import Settings
from ..errors import AppError
from ..parsing.parsers import ParseError, parse_document
from ..parsing.sniff import sniff_mime
from ..schemas_api import RegisterEvidence, UploadUrlRequest
from ..storage import paths
from ..storage.base import ObjectNotFound, ObjectStore, PreconditionFailed
from .cases import CaseService, new_id
from .scanner import Scanner

SIGNED_URL_TTL = timedelta(minutes=10)


def safe_filename(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    return re.sub(r"[\x00-\x1f\x7f]", "", base).strip()[:200] or "document"


class EvidenceService:
    def __init__(
        self, store: ObjectStore, cases: CaseService, scanner: Scanner, settings: Settings
    ) -> None:
        self.store, self.cases, self.scanner, self.s = store, cases, scanner, settings

    def request_upload(
        self, ctx: TenantContext, case_id: str, req: UploadUrlRequest
    ) -> dict[str, Any]:
        self.cases.get(ctx.tenant_id, case_id)  # 404 if not this tenant's case
        if req.mime_type not in ALLOWED_UPLOAD_MIME:
            raise AppError("UNSUPPORTED_MEDIA_TYPE", "This file type is not supported.", 415)
        if req.size_bytes > self.s.max_upload_bytes:
            raise AppError(
                "FILE_TOO_LARGE", f"Files are limited to {self.s.max_upload_bytes} bytes.", 413
            )
        document_id = new_id("doc")
        path = paths.upload(ctx.tenant_id, case_id, document_id)
        out: dict[str, Any] = {
            "document_id": document_id,
            "method": "PUT",
            "max_bytes": req.size_bytes,
            "expires_in_seconds": int(SIGNED_URL_TTL.total_seconds()),
        }
        signer = getattr(self.store, "signed_upload_url", None)
        if signer:
            url, headers = signer(path, req.mime_type, req.size_bytes, SIGNED_URL_TTL)
            out.update(upload_url=url, headers=headers)
        else:  # local dev adapter: upload through the API itself
            out.update(upload_url=f"/v1/cases/{case_id}/uploads/{document_id}", headers={})
        return out

    def put_local(self, ctx: TenantContext, case_id: str, document_id: str, data: bytes) -> None:
        if self.s.storage_backend not in {"local", "sqlite"}:
            raise AppError("NOT_FOUND", "Resource not found.", 404)
        self.cases.get(ctx.tenant_id, case_id)
        if len(data) > self.s.max_upload_bytes:
            raise AppError("FILE_TOO_LARGE", "File too large.", 413)
        try:
            self.store.put(
                paths.upload(ctx.tenant_id, case_id, document_id), data, if_generation_match=0
            )
        except PreconditionFailed:
            raise AppError(
                "UPLOAD_EXISTS", "This upload slot has already been used.", 409
            ) from None

    def register(self, ctx: TenantContext, case_id: str, req: RegisterEvidence) -> DocumentMetadata:
        state = self.cases.get(ctx.tenant_id, case_id)
        if state.stage == CaseStage.ANALYSING:
            raise AppError(
                "ANALYSIS_IN_PROGRESS", "Evidence cannot change while analysis is running.", 409
            )
        if any(d.document_id == req.document_id for d in state.evidence):
            raise AppError(
                "DOCUMENT_ALREADY_REGISTERED", "This document is already registered.", 409
            )
        path = paths.upload(ctx.tenant_id, case_id, req.document_id)
        try:
            obj = self.store.get(path)
        except ObjectNotFound:
            raise AppError(
                "UPLOAD_NOT_FOUND", "No uploaded file was found for this document id.", 422
            ) from None
        data = obj.data
        digest = hashlib.sha256(data).hexdigest()
        reject: AppError | None = None
        sniffed = sniff_mime(data)
        if len(data) > self.s.max_upload_bytes:
            reject = AppError("FILE_TOO_LARGE", "File too large.", 413)
        elif digest != req.sha256:
            reject = AppError(
                "CHECKSUM_MISMATCH", "The uploaded file does not match the declared SHA-256.", 422
            )
        elif sniffed is None or sniffed not in ALLOWED_UPLOAD_MIME or sniffed != req.mime_type:
            reject = AppError(
                "UNSUPPORTED_MEDIA_TYPE", "File content does not match an allowed type.", 415
            )
        if reject:
            self.store.delete(path)  # never keep rejected bytes
            raise reject

        assert sniffed is not None  # guaranteed by the type check above
        status, pages, scan_note = ParserStatus.QUARANTINED, None, None
        scan = self.scanner.scan(data, sniffed)
        scan_note = scan.detail
        if scan.scanned and not scan.clean:
            status = ParserStatus.BLOCKED
        elif scan.scanned and scan.clean:
            try:
                parsed = parse_document(req.document_id, sniffed, data)
                pages = len(parsed.pages)
                has_text = sum(len(p.text) for p in parsed.pages) > 0
                is_image = sniffed.startswith("image/")
                status = ParserStatus.PARSED if has_text or is_image else ParserStatus.UNSUPPORTED
            except ParseError:
                status = ParserStatus.FAILED

        dup = next((d.document_id for d in state.evidence if d.sha256 == digest), None)
        meta = DocumentMetadata(
            document_id=req.document_id,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            filename=safe_filename(req.filename),
            mime_type=sniffed,
            size_bytes=len(data),
            sha256=digest,
            recorded_event_date=req.recorded_event_date,
            event_date_unknown=req.event_date_unknown,
            uploaded_by=ctx.user_id,
            origin=req.origin,
            page_count=pages,
            parser_status=status,
            object_generation=obj.generation,
            duplicate_of=dup,
        )

        def mutate(s):  # type: ignore[no-untyped-def]
            s.evidence.append(meta)
            return s

        self.cases.update(ctx.tenant_id, case_id, mutate)
        self._note = scan_note
        return meta
