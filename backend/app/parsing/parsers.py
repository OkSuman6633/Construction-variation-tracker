"""Deterministic document parsing adapters (no LLM). Produces page-referenced text.

page_ref conventions: PDF "page_N"; DOCX "section_N"; XLSX "sheet_<name>"; EML "message_body".
Images are accepted and registered but NOT text-extracted in this version (OCR/vision adapter
outstanding): they return zero pages with diagnostics, and intake routes them accordingly.
"""

from __future__ import annotations

import email
import io
import re
from email import policy

from construction_variation_agent.shared_libraries.runtime import ParsedDocument, ParsedPage

from .sniff import DOCX, XLSX

MAX_PAGES = 500
SECTION_CHARS = 3000


class ParseError(Exception):
    pass


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def parse_pdf(document_id: str, data: bytes) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("encrypted PDF")
        pages = []
        for i, p in enumerate(reader.pages[:MAX_PAGES], 1):
            pages.append(ParsedPage(f"page_{i}", _clean(p.extract_text() or "")))
    except (PyPdfError, ValueError, KeyError) as exc:
        raise ParseError(f"unreadable PDF: {type(exc).__name__}") from exc
    with_text = sum(1 for p in pages if len(p.text) > 20)
    diag = {
        "parser": "pypdf",
        "pages": len(pages),
        "pages_with_text": with_text,
        "text_layer": with_text > 0,
        "needs_ocr": with_text < len(pages),
    }
    return ParsedDocument(document_id, tuple(pages), diag)


def parse_docx(document_id: str, data: bytes) -> ParsedDocument:
    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"unreadable DOCX: {type(exc).__name__}") from exc
    chunks = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            chunks.append(" | ".join(c.text.strip() for c in row.cells))
    sections, cur = [], ""
    for line in chunks:
        if len(cur) + len(line) > SECTION_CHARS and cur:
            sections.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        sections.append(cur)
    pages = tuple(ParsedPage(f"section_{i}", _clean(s)) for i, s in enumerate(sections, 1))
    return ParsedDocument(
        document_id, pages, {"parser": "python-docx", "sections": len(pages), "text_layer": True}
    )


def parse_xlsx(document_id: str, data: bytes) -> ParsedDocument:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"unreadable XLSX: {type(exc).__name__}") from exc
    pages = []
    for ws in wb.worksheets[:50]:
        rows = []
        for n, row in enumerate(ws.iter_rows(values_only=True)):
            if n >= 2000:
                break
            if any(c is not None for c in row):
                rows.append(" | ".join("" if c is None else str(c) for c in row))
        pages.append(
            ParsedPage(f"sheet_{re.sub(r'[^A-Za-z0-9_-]', '_', ws.title)}", "\n".join(rows))
        )
    return ParsedDocument(
        document_id, tuple(pages), {"parser": "openpyxl", "sheets": len(pages), "text_layer": True}
    )


def parse_eml(document_id: str, data: bytes) -> ParsedDocument:
    try:
        msg = email.message_from_bytes(data, policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"unreadable EML: {type(exc).__name__}") from exc
    head = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h])
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    text = _clean(f"{head}\n\n{body}")
    return ParsedDocument(
        document_id,
        (ParsedPage("message_body", text),),
        {"parser": "email", "text_layer": True, "attachments_not_parsed": attachments},
    )


def parse_image(document_id: str, data: bytes) -> ParsedDocument:
    from PIL import Image, ImageOps

    diag: dict = {
        "parser": "pillow",
        "text_layer": False,
        "needs_ocr": False,
        "note": "image is supplied directly to the document-extraction model for visual review",
    }
    try:
        with Image.open(io.BytesIO(data)) as im:
            diag["size"] = list(im.size)
            exif = im.getexif()
            dt = exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
            if dt:
                diag["exif_datetime"] = str(dt)
                diag["timestamp_reliability"] = "unverified (EXIF can be edited or missing)"
            image = ImageOps.exif_transpose(im).convert("RGB")
            image.thumbnail((4096, 4096))
            encoded = io.BytesIO()
            image.save(encoded, format="JPEG", quality=88, optimize=True)
            image_data = encoded.getvalue()
            if len(image_data) > 15 * 1024 * 1024:
                raise ParseError("image exceeds the model input size limit after normalization")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, ParseError):
            raise
        raise ParseError(f"unreadable image: {type(exc).__name__}") from exc
    diag["model_input_bytes"] = len(image_data)
    return ParsedDocument(
        document_id,
        (ParsedPage("image_1", ""),),
        diag,
        mime_type="image/jpeg",
        image_bytes=image_data,
    )


def parse_document(document_id: str, mime: str, data: bytes) -> ParsedDocument:
    if mime == "application/pdf":
        return parse_pdf(document_id, data)
    if mime == DOCX:
        return parse_docx(document_id, data)
    if mime == XLSX:
        return parse_xlsx(document_id, data)
    if mime == "message/rfc822":
        return parse_eml(document_id, data)
    if mime in {"image/jpeg", "image/png"}:
        return parse_image(document_id, data)
    raise ParseError("unsupported type")
