"""Build real DOCX / EML / PDF bytes from plain text for tests."""

import io


def make_docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def make_eml(text: str) -> bytes:
    lines = text.splitlines()
    head = [ln for ln in lines if ln.split(":")[0] in {"From", "To", "Date", "Subject"}][:4]
    body = [ln for ln in lines if ln not in head]
    return ("\n".join(head) + "\n\n" + "\n".join(body) + "\n").encode()


def make_pdf(text: str) -> bytes:
    """Minimal valid one-page text PDF (Helvetica, WinAnsi)."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = (
        "BT /F1 11 Tf 50 780 Td 14 TL\n"
        + "\n".join(f"({esc(ln)}) Tj T*" for ln in text.splitlines())
        + "\nET"
    )
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out
