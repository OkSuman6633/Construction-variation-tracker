"""Temporary, attachment-only runtime for testing the full agent pipeline in ADK Web."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import InvocationContext, SequentialAgent
from google.adk.events import Event
from google.genai import types

from .shared_libraries.constants import (
    ALLOWED_UPLOAD_MIME,
    DEFAULT_MAX_FILE_BYTES,
    STATE_CASE,
    STATE_CASE_CONTEXT,
    STATE_CORRELATION,
    STATE_CROSSREF,
    STATE_DOCUMENTS,
    STATE_EXTRACT_CURSOR,
    STATE_EXTRACT_DONE,
    STATE_EXTRACT_SEQ,
    STATE_EXTRACTIONS,
    STATE_INTAKE,
    STATE_JOB,
    STATE_PLAIN_ENGLISH_SUMMARY,
    STATE_REVIEW_PREP,
    STATE_TENANT,
    STATE_VARIATION,
)
from .shared_libraries.runtime import (
    JobRuntime,
    ParsedDocument,
    ScopeError,
    register_runtime,
    unregister_runtime,
)
from .shared_libraries.schemas import CaseAnalysisRequest

MAX_ADK_WEB_DOCUMENTS = 200
MAX_ADK_WEB_TOTAL_BYTES = 100 * 1024 * 1024
_RESET_STATE_KEYS = (
    STATE_TENANT,
    STATE_CASE,
    STATE_JOB,
    STATE_CORRELATION,
    STATE_DOCUMENTS,
    STATE_CASE_CONTEXT,
    STATE_INTAKE,
    STATE_EXTRACTIONS,
    STATE_EXTRACT_CURSOR,
    STATE_EXTRACT_DONE,
    STATE_EXTRACT_SEQ,
    STATE_CROSSREF,
    STATE_VARIATION,
    STATE_REVIEW_PREP,
    STATE_PLAIN_ENGLISH_SUMMARY,
)


class AdkWebUploadError(ValueError):
    """The current ADK Web message does not contain usable direct uploads."""


class AdkWebEvidenceProvider:
    """Read-only evidence access limited to files attached to this one ADK Web message."""

    def __init__(
        self, documents: dict[str, tuple[str, str, ParsedDocument]], prompt: str
    ) -> None:
        self._documents = documents
        self._prompt = prompt

    def case_context(self) -> dict[str, Any]:
        return {
            "title": "ADK Web temporary document review",
            "alleged_change": self._prompt
            or (
                "Prepare a source-backed draft chronology and identify potential variation "
                "evidence."
            ),
            "parties": [],
            "contract": {"form": "unknown", "description": None, "jurisdiction": None},
            "key_dates": {},
        }

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "document_id": document_id,
                "filename": filename,
                "mime_type": mime_type,
                "recorded_event_date": None,
                "origin": "ADK Web direct upload",
                "page_count": len(document.pages),
                "parser_status": "parsed",
            }
            for document_id, (filename, mime_type, document) in self._documents.items()
        ]

    def get_document(self, document_id: str) -> ParsedDocument:
        try:
            return self._documents[document_id][2]
        except KeyError as exc:
            raise ScopeError("document is not part of this temporary ADK Web analysis") from exc

    def existing_events(self) -> list[dict[str, Any]]:
        return []

    def review_summary(self) -> dict[str, Any]:
        return {
            "accepted": 0,
            "edited": 0,
            "rejected": 0,
            "corrections": [],
            "rejected_items": [],
            "approved_references": [],
        }


def _safe_filename(value: str | None, index: int, mime_type: str) -> str:
    filename = re.split(r"[\\/]", value or "")[-1].strip()
    if filename:
        return filename[:250]
    extension = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "message/rfc822": ".eml",
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }.get(mime_type, ".bin")
    return f"document-{index}{extension}"


def _inline_uploads(
    user_content: types.Content | None,
) -> tuple[list[tuple[str, str, bytes]], str]:
    if user_content is None:
        raise AdkWebUploadError("Attach one or more supported documents to this ADK Web message.")

    uploads: list[tuple[str, str, bytes]] = []
    text_parts: list[str] = []
    total_bytes = 0
    for part in user_content.parts or []:
        if part.text:
            text_parts.append(part.text)
        if part.file_data is not None:
            raise AdkWebUploadError(
                "Use ADK Web's direct file-upload control; remote file references are not accepted."
            )
        blob = part.inline_data
        if blob is None:
            continue
        data = blob.data
        if not isinstance(data, bytes) or not data:
            raise AdkWebUploadError("An attached file is empty or could not be read.")
        if len(data) > DEFAULT_MAX_FILE_BYTES:
            raise AdkWebUploadError("Each ADK Web upload must be 25 MB or smaller.")
        total_bytes += len(data)
        if total_bytes > MAX_ADK_WEB_TOTAL_BYTES:
            raise AdkWebUploadError("The total size of ADK Web uploads must be 100 MB or smaller.")
        if len(uploads) >= MAX_ADK_WEB_DOCUMENTS:
            raise AdkWebUploadError("Attach no more than 200 documents per ADK Web analysis.")
        from app.parsing.sniff import sniff_mime

        mime_type = sniff_mime(data)
        if mime_type not in ALLOWED_UPLOAD_MIME:
            filename = _safe_filename(
                blob.display_name, len(uploads) + 1, "application/octet-stream"
            )
            raise AdkWebUploadError(
                f"'{filename}' "
                "is not a supported PDF, DOCX, XLSX, EML, JPEG, or PNG file."
            )
        declared_type = blob.mime_type
        if declared_type and declared_type not in {mime_type, "application/octet-stream"}:
            filename = _safe_filename(blob.display_name, len(uploads) + 1, mime_type)
            raise AdkWebUploadError(
                f"The file type for '{filename}' "
                "does not match its contents."
            )
        uploads.append((blob.display_name or "", mime_type, data))

    if not uploads:
        raise AdkWebUploadError("Attach one or more supported documents to this ADK Web message.")
    return uploads, "\n".join(text_parts).strip()


def create_adk_web_runtime(
    user_content: types.Content | None,
) -> JobRuntime:
    from app.parsing.parsers import ParseError, parse_document

    uploads, prompt = _inline_uploads(user_content)
    parsed_documents: dict[str, tuple[str, str, ParsedDocument]] = {}
    for index, (display_name, mime_type, data) in enumerate(uploads, start=1):
        document_id = f"webdoc-{uuid.uuid4().hex}"
        filename = _safe_filename(display_name, index, mime_type)
        try:
            parsed = parse_document(document_id, mime_type, data)
        except ParseError as exc:
            raise AdkWebUploadError(f"Could not read '{filename}': {exc}.") from exc
        if parsed.diagnostics.get("needs_ocr"):
            raise AdkWebUploadError(
                f"'{filename}' has pages without extractable text. OCR the PDF first or upload "
                "those pages as images so the analysis can cover the full document."
            )
        if not parsed.image_bytes and not any(page.text.strip() for page in parsed.pages):
            raise AdkWebUploadError(f"'{filename}' contains no extractable text.")
        parsed_documents[document_id] = (filename, mime_type, parsed)

    tenant_id = f"webtenant-{uuid.uuid4().hex}"
    case_id = f"webcase-{uuid.uuid4().hex}"
    job_id = f"webjob-{uuid.uuid4().hex}"
    correlation_id = f"webcorr-{uuid.uuid4().hex}"
    provider = AdkWebEvidenceProvider(parsed_documents, prompt)
    request = CaseAnalysisRequest(
        tenant_id=tenant_id,
        case_id=case_id,
        job_id=job_id,
        correlation_id=correlation_id,
        document_ids=list(parsed_documents),
    )
    return JobRuntime(request=request, provider=provider)


def _session_state(runtime: JobRuntime) -> dict[str, Any]:
    request = runtime.request
    return {
        STATE_TENANT: request.tenant_id,
        STATE_CASE: request.case_id,
        STATE_JOB: request.job_id,
        STATE_CORRELATION: request.correlation_id,
        STATE_DOCUMENTS: list(request.document_ids),
        STATE_CASE_CONTEXT: runtime.provider.case_context(),
    }


def _input_error_event(agent_name: str, ctx: InvocationContext, message: str) -> Event:
    return Event(
        author=agent_name,
        invocation_id=ctx.invocation_id,
        turn_complete=True,
        content=types.Content(role="model", parts=[types.Part(text=message)]),
    )


class AdkWebSandboxAgent(SequentialAgent):
    """Register a per-message upload runtime for the normal six-stage agent sequence."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        try:
            runtime = create_adk_web_runtime(ctx.user_content)
        except AdkWebUploadError as exc:
            yield _input_error_event(self.name, ctx, str(exc))
            return

        state = ctx.session.state
        for key in _RESET_STATE_KEYS:
            state.pop(key, None)
        state.update(_session_state(runtime))
        ctx.agent_states.clear()
        register_runtime(runtime)
        try:
            async for event in super()._run_async_impl(ctx):
                yield event
        finally:
            unregister_runtime(runtime.request.job_id)
