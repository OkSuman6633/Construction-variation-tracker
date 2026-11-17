"""MIME sniffing from magic bytes. The declared type is never trusted."""

from __future__ import annotations

import io
import zipfile

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sniff_mime(data: bytes) -> str | None:
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return None
        if any(n.startswith("word/") for n in names):
            return DOCX
        if any(n.startswith("xl/") for n in names):
            return XLSX
        return None
    text = data[:4096].decode("utf-8", errors="ignore").lower()
    if (
        text.count("\n") >= 2
        and sum(h in text for h in ("from:", "to:", "subject:", "date:", "message-id:")) >= 3
    ):
        return "message/rfc822"
    return None
