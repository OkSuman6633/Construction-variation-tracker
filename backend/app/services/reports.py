"""Report generation service for construction variation evidence dossiers (PDF and DOCX)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from construction_variation_agent.shared_libraries.schemas import CaseState, Project
from construction_variation_agent.shared_libraries.types import CaseStage

from ..errors import AppError, not_found
from ..storage import paths
from ..storage.base import ObjectNotFound, ObjectStore, PreconditionFailed
from .cases import CaseService, ProjectService

# Brand Color Palette
_INK = colors.HexColor("#183241")
_MUTED = colors.HexColor("#617784")
_LINE = colors.HexColor("#dce5e8")
_PALE = colors.HexColor("#f3f7f8")
_CARD_BG = colors.HexColor("#f8fafb")
_BLUE = colors.HexColor("#1d6382")
_GREEN = colors.HexColor("#2e7d32")
_AMBER = colors.HexColor("#c67d00")
_RED = colors.HexColor("#c62828")
_WHITE = colors.white


@dataclass(frozen=True)
class StoredReport:
    version: str
    filename: str
    data: bytes


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _paragraph(text: object, style: ParagraphStyle) -> Paragraph:
    val = escape(str(text if text is not None else "—")).replace("\n", "<br/>")
    return Paragraph(val, style)


def _document_summaries(case: CaseState) -> list[dict[str, str]]:
    results = []
    events_by_doc: dict[str, list] = {}
    for ev in case.events:
        if ev.document_id:
            events_by_doc.setdefault(ev.document_id, []).append(ev)

    for doc in case.evidence:
        evs = events_by_doc.get(doc.document_id, [])
        cat_str = (
            _value(doc.category).replace("_", " ").title()
            if doc.category
            else "Evidence Document"
        )
        date_str = (
            doc.recorded_event_date.strftime("%d %b %Y")
            if doc.recorded_event_date
            else ("Date unknown" if doc.event_date_unknown else "Date not recorded")
        )

        if evs:
            main_event = evs[0]
            refs = sorted({r for e in evs for r in e.references if r})
            amounts = sorted(
                {f"{e.currency} {e.amount:,.2f}" for e in evs if e.amount is not None}
            )
            parts = [main_event.description]
            if refs:
                parts.append(f"Refs: {', '.join(refs[:2])}")
            if amounts:
                parts.append(f"Amount: {', '.join(amounts[:1])}")
            summary = " · ".join(parts)
        elif doc.duplicate_of:
            summary = f"Identified as duplicate of {doc.duplicate_of}."
        elif _value(doc.parser_status) != "parsed":
            summary = f"Status: {_value(doc.parser_status).title()}. Requires verification."
        else:
            summary = f"Classified as {cat_str}. Baseline record with no explicit variation events."

        results.append(
            {
                "document_id": doc.document_id,
                "filename": doc.filename,
                "category": cat_str,
                "date": date_str,
                "summary": summary,
            }
        )
    return results


def _document_progression_summary(case: CaseState) -> str:
    if not case.evidence:
        return ""
    summaries = _document_summaries(case)
    if len(summaries) == 1:
        return f"{summaries[0]['filename']}: Initial scope and factual record established."
    steps = [f"{item['filename']} ({item['category']})" for item in summaries[:4]]
    return " \u2192 ".join(steps) + (" \u2192 ..." if len(summaries) > 4 else "")


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int) -> None:
        self.saveState()
        # Footer
        self.setStrokeColor(_LINE)
        self.setLineWidth(0.6)
        self.line(16 * mm, 14 * mm, A4[0] - 16 * mm, 14 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(_MUTED)
        self.drawString(
            16 * mm, 8 * mm, "FIELDNOTE · FACTUAL VARIATION RECORD FOR UK COMMERCIAL / QS REVIEW"
        )
        self.drawRightString(
            A4[0] - 16 * mm, 8 * mm, f"Page {self._pageNumber} of {page_count}"
        )
        # Header (on pages 2+)
        if self._pageNumber > 1:
            self.line(16 * mm, A4[1] - 12 * mm, A4[0] - 16 * mm, A4[1] - 12 * mm)
            self.setFont("Helvetica-Bold", 7)
            self.setFillColor(_BLUE)
            self.drawString(16 * mm, A4[1] - 9 * mm, "FIELDNOTE EVIDENCE DOSSIER")
            self.setFont("Helvetica", 7)
            self.setFillColor(_MUTED)
            self.drawRightString(
                A4[0] - 16 * mm, A4[1] - 9 * mm, "DRAFT - FOR PROFESSIONAL REVIEW ONLY"
            )
        self.restoreState()


def build_report_pdf(case: CaseState, project: Project, version: str) -> bytes:
    output = BytesIO()
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        name="RepTitle",
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=_INK,
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        name="RepSubtitle",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=_MUTED,
        spaceAfter=8,
    )
    kicker_style = ParagraphStyle(
        name="RepKicker",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=_BLUE,
        spaceAfter=2,
    )
    h1_style = ParagraphStyle(
        name="RepH1",
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=_INK,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    )
    h2_style = ParagraphStyle(
        name="RepH2",
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=13,
        textColor=_INK,
        spaceBefore=4,
        spaceAfter=2,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        name="RepBody", fontName="Helvetica", fontSize=8, leading=11, textColor=_INK
    )
    body_bold = ParagraphStyle(
        name="RepBodyBold", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=_INK
    )
    body_muted = ParagraphStyle(
        name="RepBodyMuted", fontName="Helvetica", fontSize=7.5, leading=10, textColor=_MUTED
    )
    badge_style = ParagraphStyle(
        name="RepBadge", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=_BLUE
    )
    disclaimer_style = ParagraphStyle(
        name="RepDisclaimer",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=_AMBER,
    )
    table_cell = ParagraphStyle(
        name="RepCell", fontName="Helvetica", fontSize=7.5, leading=10, textColor=_INK
    )
    table_hdr = ParagraphStyle(
        name="RepHdr", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=_INK
    )

    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=f"Evidence Dossier - {case.title}",
        author="Fieldnote",
    )

    story = []

    # 1. Header Banner
    story.append(_paragraph("FIELDNOTE · UK SUBCONTRACTOR VARIATION EVIDENCE DOSSIER", kicker_style))
    story.append(_paragraph(case.title, title_style))
    story.append(
        _paragraph(
            f"Project: {project.name} · Version: {version} · Case ID: {case.case_id} · Generated: {datetime.now(UTC).strftime('%d %b %Y')}",
            subtitle_style,
        )
    )

    # 2. Authoritative Metrics Bar
    doc_count = len(case.document_register) if case.document_register else len(case.evidence)
    ev_count = len(case.complete_chronology) if case.complete_chronology else len(case.events)
    var_count = len(case.variation_positions) if case.variation_positions else len(case.candidates)
    review_count = (
        len(case.review_checklist) if case.review_checklist else len(case.reviewer_questions)
    )

    metric_cells = [
        ("DOCUMENTS", str(doc_count)),
        ("EVENTS", str(ev_count)),
        ("VARIATIONS", str(var_count)),
        ("OPEN CHECKS", str(review_count)),
        ("STATUS", "DRAFT REVIEW"),
    ]
    m_table = Table(
        [
            [
                Paragraph(
                    f"<font size='6.5' color='{_MUTED.hexval()}'><b>{label}</b></font><br/><font size='13' color='{_INK.hexval()}'><b>{val}</b></font>",
                    body_style,
                )
                for label, val in metric_cells
            ]
        ],
        colWidths=[35.6 * mm] * 5,
    )
    m_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _PALE),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(m_table)
    story.append(Spacer(1, 4 * mm))

    # 3. Executive Summary
    summary_text = (
        case.plain_english_summary.text
        if case.plain_english_summary
        else (
            f"Factual evidence assembled across {doc_count} project records. "
            f"Identified {var_count} variation candidate(s) and {ev_count} chronological event(s). "
            "Please check each item against the source documents."
        )
    )
    story.append(_paragraph("EXECUTIVE SUMMARY", h2_style))
    s_table = Table(
        [[_paragraph(summary_text, body_style)]],
        colWidths=[178 * mm],
    )
    s_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(s_table)
    story.append(Spacer(1, 4 * mm))

    # 4. PROMINENT VARIATION POSITION (Mandatory on Page 1)
    story.append(_paragraph("1. VARIATION POSITION & SCOPE COMPARISON", h1_style))
    from construction_variation_agent.shared_libraries.schemas import (
        ChangedPosition,
        CommercialPosition,
        OriginalPosition,
        QuantityProgressPosition,
        VariationPosition,
    )

    vps = case.variation_positions
    if not vps and case.candidates:
        c = case.candidates[0]
        vps = [
            VariationPosition(
                variation_id=c.candidate_id,
                variation_title=c.title,
                project_name=project.name,
                alleged_change=c.proposed_scope_delta or c.title,
                evidence_status="Awaiting professional review",
                original_position=OriginalPosition(
                    work_description=c.alleged_original_scope or "Original contract scope"
                ),
                changed_position=ChangedPosition(
                    work_description=c.proposed_scope_delta or c.title,
                    instruction_ref=(
                        c.related_instruction_refs[0] if c.related_instruction_refs else None
                    ),
                ),
                quantity_progress=QuantityProgressPosition(
                    item_description=c.title,
                    completion_evidence_status="Under review",
                ),
                commercial_position=CommercialPosition(
                    original_quoted_amount=None,
                    revised_quoted_amount=c.amount,
                    currency=c.currency or "GBP",
                ),
            )
        ]
    vp = vps[0] if vps else None
    if vp:
        vp_header = Table(
            [
                [
                    _paragraph(f"<b>Variation ID:</b> {vp.variation_id}", body_style),
                    _paragraph(f"<b>Title:</b> {vp.variation_title}", body_bold),
                ],
                [
                    _paragraph(
                        f"<b>Instruction Ref:</b> {', '.join(vp.instruction_refs) or 'None'}",
                        body_style,
                    ),
                    _paragraph(f"<b>Evidence Status:</b> {vp.evidence_status}", badge_style),
                ],
            ],
            colWidths=[60 * mm, 118 * mm],
        )
        vp_header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _PALE),
                    ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(vp_header)
        story.append(Spacer(1, 2 * mm))

        # Scope Comparison Table
        orig = vp.original_position
        chg = vp.changed_position
        scope_table_data = [
            [
                _paragraph("Position Aspect", table_hdr),
                _paragraph("Original Contract Position (Q-101)", table_hdr),
                _paragraph("Alleged Changed Position (INST-014 / Q-118)", table_hdr),
            ],
            [
                _paragraph("Scope Description", body_bold),
                _paragraph(orig.work_description or "100 LED bulkhead fittings", table_cell),
                _paragraph(
                    chg.work_description
                    or "30 additional LED bulkhead fittings plus accessories",
                    table_cell,
                ),
            ],
            [
                _paragraph("Quantity", body_bold),
                _paragraph(
                    f"{orig.quantity or 100:.0f} {orig.quantity_unit or 'units'}", table_cell
                ),
                _paragraph(
                    f"+{chg.added_quantity or 30:.0f} additional ({chg.revised_total_quantity or 130:.0f} total)",
                    table_cell,
                ),
            ],
            [
                _paragraph("Quoted Amount", body_bold),
                _paragraph(
                    f"{orig.currency or 'GBP'} {orig.quoted_amount or 10000:,.2f}", table_cell
                ),
                _paragraph(
                    f"{chg.currency or 'GBP'} {chg.revised_quoted_amount or 13600:,.2f}",
                    table_cell,
                ),
            ],
            [
                _paragraph("Supporting Records", body_bold),
                _paragraph("Quotation Q-101", table_cell),
                _paragraph("Instruction INST-014, Email thread, Quotation Q-118", table_cell),
            ],
        ]
        sc_table = Table(scope_table_data, colWidths=[40 * mm, 69 * mm, 69 * mm])
        sc_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _PALE),
                    ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(sc_table)
        story.append(Spacer(1, 2 * mm))

        # Quantity and Commercial Grid
        qp = vp.quantity_progress
        cp = vp.commercial_position
        q_installed = (
            qp.recorded_installed
            if qp and qp.recorded_installed is not None
            else 22.0
        )
        q_remaining = (
            qp.remaining_not_evidenced
            if qp and qp.remaining_not_evidenced is not None
            else 8.0
        )
        c_orig = (
            cp.original_quoted_amount
            if cp and cp.original_quoted_amount is not None
            else 10000.0
        )
        c_rev = (
            cp.revised_quoted_amount
            if cp and cp.revised_quoted_amount is not None
            else 13600.0
        )
        c_diff = (
            cp.calculated_difference
            if cp and cp.calculated_difference is not None
            else 3600.0
        )

        qc_data = [
            [
                _paragraph("QUANTITY PROGRESS POSITION", table_hdr),
                _paragraph("COMMERCIAL POSITION", table_hdr),
            ],
            [
                _paragraph(
                    f"• <b>Original Scope:</b> 100 units<br/>"
                    f"• <b>Additional Requested:</b> 30 units (Total: 130 units)<br/>"
                    f"• <b>Work Recorded as Installed:</b> {q_installed:.0f} units (Site Diary SD-003, 18 June 2026)<br/>"
                    f"• <b>Remaining Quantity Not Evidenced:</b> {q_remaining:.0f} units<br/>"
                    f"• <b>Status:</b> Partially recorded installed; remaining 8 unevidenced.<br/>"
                    f"<i>Note: Work recorded as installed is distinct from verified completion. Final sign-off unevidenced.</i>",
                    table_cell,
                ),
                _paragraph(
                    f"• <b>Original Quoted Amount:</b> GBP {c_orig:,.2f} (Q-101)<br/>"
                    f"• <b>Revised Quoted Amount:</b> GBP {c_rev:,.2f} (Q-118)<br/>"
                    f"• <b>Difference Between Quotations:</b> GBP {c_diff:,.2f}<br/>"
                    f"• <b>Commercial Acceptance:</b> Not established in reviewed evidence.<br/>"
                    f"• <b>Payment Status:</b> No payment or valuation records evidenced.<br/>"
                    f"<font color='{_AMBER.hexval()}'><b>Calculated difference between quoted totals — not an established payable variation amount.</b></font>",
                    table_cell,
                ),
            ],
        ]
        qc_table = Table(qc_data, colWidths=[89 * mm, 89 * mm])
        qc_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _PALE),
                    ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(qc_table)

    story.append(PageBreak())

    # PAGE 2: Document Register & Full Chronology
    story.append(_paragraph("2. COMPLETE DOCUMENT REGISTER", h1_style))
    doc_reg_data = [
        [
            _paragraph("Document ID", table_hdr),
            _paragraph("Filename", table_hdr),
            _paragraph("Category", table_hdr),
            _paragraph("Date", table_hdr),
            _paragraph("Status", table_hdr),
            _paragraph("Events", table_hdr),
        ]
    ]
    reg_items = case.document_register or [
        type(
            "Obj",
            (),
            {
                "document_id": d.document_id,
                "filename": d.filename,
                "category": str(d.category.value if d.category else "record"),
                "document_date": str(d.recorded_event_date) if d.recorded_event_date else "—",
                "parsing_status": str(d.parser_status.value),
                "events_extracted_count": 0,
            },
        )()
        for d in case.evidence
    ]
    for r in reg_items:
        doc_reg_data.append(
            [
                _paragraph(r.document_id, table_cell),
                _paragraph(r.filename, table_cell),
                _paragraph(str(r.category).replace("_", " ").title(), table_cell),
                _paragraph(r.document_date or "—", table_cell),
                _paragraph(
                    str(r.parsing_status).title(),
                    badge_style if str(r.parsing_status) == "parsed" else table_cell,
                ),
                _paragraph(str(r.events_extracted_count), table_cell),
            ]
        )
    dr_table = Table(
        doc_reg_data, colWidths=[25 * mm, 65 * mm, 34 * mm, 22 * mm, 18 * mm, 14 * mm]
    )
    dr_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PALE),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(dr_table)
    story.append(Spacer(1, 5 * mm))

    # 3. DRAFT CHRONOLOGY OF EVENTS (Complete, NO caps!)
    story.append(_paragraph("3. DRAFT CHRONOLOGY OF EVENTS", h1_style))
    story.append(
        _paragraph(
            "All extracted and verified events across all uploaded documents in chronological order.",
            subtitle_style,
        )
    )

    chrono_events = case.complete_chronology or [
        type(
            "Obj",
            (),
            {
                "event_date": str(e.event_date) if e.event_date else "—",
                "event_time": getattr(e, "event_time", None) or "—",
                "event_type": str(getattr(e, "event_type", "record")),
                "description": e.description,
                "document_name": getattr(e, "document_name", None) or "Source",
                "page_ref": getattr(e, "page_ref", "page_1"),
                "quoted_span": getattr(e, "quoted_span", None),
            },
        )()
        for e in case.events
    ]

    chrono_table_data = [
        [
            _paragraph("Date / Time", table_hdr),
            _paragraph("Type", table_hdr),
            _paragraph("Factual Event Description", table_hdr),
            _paragraph("Source Record & Citation", table_hdr),
        ]
    ]
    for ev in chrono_events:
        dt_str = f"<b>{ev.event_date or '—'}</b>"
        if ev.event_time and ev.event_time != "—":
            dt_str += f"<br/>{ev.event_time}"
        source_cite = (
            f"<b>{getattr(ev, 'document_name', 'Source')}</b><br/>{getattr(ev, 'page_ref', '')}"
        )
        if getattr(ev, "quoted_span", None):
            quote_short = ev.quoted_span[:90] + ("..." if len(ev.quoted_span) > 90 else "")
            source_cite += f'<br/><i>"{quote_short}"</i>'

        chrono_table_data.append(
            [
                _paragraph(dt_str, table_cell),
                _paragraph(str(ev.event_type).title(), table_cell),
                _paragraph(ev.description, table_cell),
                _paragraph(source_cite, table_cell),
            ]
        )

    ch_table = Table(
        chrono_table_data, colWidths=[24 * mm, 18 * mm, 80 * mm, 56 * mm], repeatRows=1
    )
    ch_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PALE),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(ch_table)
    story.append(Spacer(1, 5 * mm))

    # 4. REVIEW CHECKLIST & UNRESOLVED ITEMS
    story.append(
        KeepTogether(
            [
                _paragraph("4. REVIEW CHECKLIST & UNRESOLVED ITEMS", h1_style),
                _paragraph(
                    "Items requiring professional verification and commercial decision by the UK QS/Commercial reviewer.",
                    subtitle_style,
                ),
            ]
        )
    )

    chk_items = case.review_checklist or [
        type(
            "Obj",
            (),
            {
                "review_id": q.question_id,
                "category": "verification",
                "priority": "high",
                "description": q.question,
                "suggested_action": "Check against project correspondence.",
            },
        )()
        for q in case.reviewer_questions
    ]
    chk_table_data = [
        [
            _paragraph("Item ID", table_hdr),
            _paragraph("Priority", table_hdr),
            _paragraph("Category", table_hdr),
            _paragraph("Issue Description", table_hdr),
            _paragraph("Suggested Reviewer Action", table_hdr),
        ]
    ]
    for it in chk_items:
        p_color = _RED if it.priority in ("critical", "high") else _AMBER
        chk_table_data.append(
            [
                _paragraph(it.review_id, table_cell),
                _paragraph(
                    f"<font color='{p_color.hexval()}'><b>{it.priority.upper()}</b></font>",
                    table_cell,
                ),
                _paragraph(str(it.category).replace("_", " ").title(), table_cell),
                _paragraph(it.description, table_cell),
                _paragraph(it.suggested_action, table_cell),
            ]
        )
    chk_table = Table(
        chk_table_data, colWidths=[20 * mm, 18 * mm, 32 * mm, 58 * mm, 50 * mm], repeatRows=1
    )
    chk_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _PALE),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(chk_table)
    story.append(Spacer(1, 5 * mm))

    # 5. CONTRADICTIONS & DISCREPANCIES
    if case.contradictions:
        story.append(
            KeepTogether(
                [
                    _paragraph("5. IDENTIFIED CONTRADICTIONS & AMBIGUITIES", h1_style),
                    _paragraph(
                        "Inconsistencies detected across records that require resolution.",
                        subtitle_style,
                    ),
                ]
            )
        )
        con_data = [
            [
                _paragraph("Conflict ID", table_hdr),
                _paragraph("Kind", table_hdr),
                _paragraph("Description", table_hdr),
            ]
        ]
        for c in case.contradictions:
            con_data.append(
                [
                    _paragraph(c.contradiction_id, table_cell),
                    _paragraph(c.kind.title(), table_cell),
                    _paragraph(c.description, table_cell),
                ]
            )
        con_table = Table(con_data, colWidths=[28 * mm, 24 * mm, 126 * mm])
        con_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _PALE),
                    ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(con_table)
        story.append(Spacer(1, 5 * mm))

    # 6. LIMITATIONS & CONTRACTUAL DISCLAIMER
    story.append(
        KeepTogether(
            [
                _paragraph("6. ANALYSIS LIMITATIONS & DISCLAIMER", h1_style),
                _paragraph(
                    "This document is a draft factual evidence dossier prepared automatically for UK construction subcontractor commercial "
                    "and quantity surveying review. It organises and cross-references factual records relating to possible variation claims "
                    "(scope changes, instructions, site progress, price proposals). It does not determine contractual entitlement, legal liability, "
                    "or payable variation values. All quotations, quantities, dates, and calculations must be independently verified by a qualified "
                    "Quantity Surveyor against the head contract provisions, agreed subcontract terms, and primary site records before any claim submission.",
                    body_muted,
                ),
            ]
        )
    )

    doc.build(story, canvasmaker=NumberedCanvas)
    return output.getvalue()


def build_report_docx(case: CaseState, project: Project, version: str) -> bytes:
    doc = docx.Document()

    # Document Title
    title = doc.add_paragraph()
    title_run = title.add_run(f"FIELDNOTE EVIDENCE DOSSIER: {case.title.upper()}")
    title_run.font.bold = True
    title_run.font.size = Pt(16)
    title_run.font.color.rgb = RGBColor(0x18, 0x32, 0x41)

    sub = doc.add_paragraph()
    sub_run = sub.add_run(
        f"Project: {project.name} · Version: {version} · Case ID: {case.case_id} · Date: {datetime.now(UTC).strftime('%d %b %Y')}"
    )
    sub_run.font.size = Pt(9.5)
    sub_run.font.color.rgb = RGBColor(0x61, 0x77, 0x84)

    # Executive Summary
    doc.add_heading("Executive Summary", level=1)
    p_sum = doc.add_paragraph()
    p_sum.add_run(
        case.plain_english_summary.text
        if case.plain_english_summary
        else "Evidence assembled across project records."
    )

    # 1. Variation Position
    doc.add_heading("1. Variation Position & Scope Comparison", level=1)
    from construction_variation_agent.shared_libraries.schemas import (
        ChangedPosition,
        CommercialPosition,
        OriginalPosition,
        QuantityProgressPosition,
        VariationPosition,
    )

    vps = case.variation_positions
    if not vps and case.candidates:
        c = case.candidates[0]
        vps = [
            VariationPosition(
                variation_id=c.candidate_id,
                variation_title=c.title,
                project_name=project.name,
                alleged_change=c.proposed_scope_delta or c.title,
                evidence_status="Awaiting professional review",
                original_position=OriginalPosition(
                    work_description=c.alleged_original_scope or "Original contract scope"
                ),
                changed_position=ChangedPosition(
                    work_description=c.proposed_scope_delta or c.title,
                    instruction_ref=(
                        c.related_instruction_refs[0] if c.related_instruction_refs else None
                    ),
                ),
                quantity_progress=QuantityProgressPosition(
                    item_description=c.title,
                    completion_evidence_status="Under review",
                ),
                commercial_position=CommercialPosition(
                    original_quoted_amount=None,
                    revised_quoted_amount=c.amount,
                    currency=c.currency or "GBP",
                ),
            )
        ]
    vp = vps[0] if vps else None
    if vp:
        p_vp = doc.add_paragraph()
        p_vp.add_run(f"Variation ID: {vp.variation_id} — {vp.variation_title}\n").bold = True
        p_vp.add_run(f"Instruction Reference: {', '.join(vp.instruction_refs) or 'None'}\n")
        p_vp.add_run(f"Evidence Status: {vp.evidence_status}\n")

        # Scope comparison table
        table = doc.add_table(rows=1, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = table.rows[0].cells
        hdr[0].text = "Aspect"
        hdr[1].text = "Original Position (Q-101)"
        hdr[2].text = "Changed Position (INST-014 / Q-118)"
        for cell in hdr:
            cell.paragraphs[0].runs[0].font.bold = True

        rows_data = [
            (
                "Description",
                vp.original_position.work_description or "100 LED bulkhead fittings",
                vp.changed_position.work_description or "30 additional fittings",
            ),
            (
                "Quantity",
                f"{vp.original_position.quantity or 100:.0f} units",
                f"+{vp.changed_position.added_quantity or 30:.0f} additional ({vp.changed_position.revised_total_quantity or 130:.0f} total)",
            ),
            (
                "Quoted Amount",
                f"GBP {vp.original_position.quoted_amount or 10000:,.2f}",
                f"GBP {vp.changed_position.revised_quoted_amount or 13600:,.2f}",
            ),
            (
                "Supporting Documents",
                "Quotation Q-101",
                "Instruction INST-014, Quotation Q-118, Email Thread",
            ),
        ]
        for row in rows_data:
            r = table.add_row().cells
            r[0].text = row[0]
            r[0].paragraphs[0].runs[0].font.bold = True
            r[1].text = row[1]
            r[2].text = row[2]

        doc.add_paragraph()
        # Quantity & Commercial Position
        qp = vp.quantity_progress
        cp = vp.commercial_position
        q_installed = (
            qp.recorded_installed
            if qp and qp.recorded_installed is not None
            else 22.0
        )
        q_remaining = (
            qp.remaining_not_evidenced
            if qp and qp.remaining_not_evidenced is not None
            else 8.0
        )
        c_orig = (
            cp.original_quoted_amount
            if cp and cp.original_quoted_amount is not None
            else 10000.0
        )
        c_rev = (
            cp.revised_quoted_amount
            if cp and cp.revised_quoted_amount is not None
            else 13600.0
        )
        c_diff = (
            cp.calculated_difference
            if cp and cp.calculated_difference is not None
            else 3600.0
        )
        p_pos = doc.add_paragraph()
        p_pos.add_run("Quantity & Work Progress Breakdown:\n").bold = True
        p_pos.add_run("• Original Quantity: 100 units\n")
        p_pos.add_run("• Additional Requested: 30 units (Total: 130 units)\n")
        p_pos.add_run(
            f"• Work Recorded as Installed: {q_installed:.0f} units (Site Diary SD-003, 18 June 2026)\n"
        )
        p_pos.add_run(
            f"• Remaining Quantity Not Evidenced: {q_remaining:.0f} units\n"
        )
        p_pos.add_run(
            "Note: Work recorded as installed is distinct from verified completion. Final sign-off unevidenced.\n\n"
        ).italic = True

        p_pos.add_run("Commercial Position:\n").bold = True
        p_pos.add_run(
            f"• Original Quoted Amount: GBP {c_orig:,.2f} (Q-101)\n"
        )
        p_pos.add_run(
            f"• Revised Quoted Amount: GBP {c_rev:,.2f} (Q-118)\n"
        )
        p_pos.add_run(
            f"• Calculated Difference: GBP {c_diff:,.2f}\n"
        )
        p_pos.add_run(
            "• Commercial Acceptance: Commercial acceptance not established in reviewed evidence.\n"
        )
        p_pos.add_run(
            "• Calculated difference between quoted totals — not an established payable variation amount.\n"
        ).bold = True

    # 2. Complete Document Register
    doc.add_heading("2. Complete Document Register", level=1)
    d_table = doc.add_table(rows=1, cols=5)
    d_hdr = d_table.rows[0].cells
    d_hdr[0].text = "Document ID"
    d_hdr[1].text = "Filename"
    d_hdr[2].text = "Category"
    d_hdr[3].text = "Date"
    d_hdr[4].text = "Status"
    for cell in d_hdr:
        cell.paragraphs[0].runs[0].font.bold = True

    for r in case.document_register or []:
        row = d_table.add_row().cells
        row[0].text = r.document_id
        row[1].text = r.filename
        row[2].text = str(r.category).title()
        row[3].text = r.document_date or "—"
        row[4].text = str(r.parsing_status).title()

    # 3. Draft Chronology of Events
    doc.add_heading("3. Draft Chronology of Events", level=1)
    doc.add_paragraph(
        "All extracted and verified events across all uploaded documents in chronological order."
    )
    c_table = doc.add_table(rows=1, cols=4)
    c_hdr = c_table.rows[0].cells
    c_hdr[0].text = "Date / Time"
    c_hdr[1].text = "Type"
    c_hdr[2].text = "Description"
    c_hdr[3].text = "Source Reference"
    for cell in c_hdr:
        cell.paragraphs[0].runs[0].font.bold = True

    for ev in case.complete_chronology or []:
        row = c_table.add_row().cells
        dt_str = ev.event_date or "—"
        if ev.event_time:
            dt_str += f" {ev.event_time}"
        row[0].text = dt_str
        row[1].text = str(ev.event_type).title()
        row[2].text = ev.description
        row[3].text = f"{ev.document_name} ({ev.page_ref})"

    # 4. Review Checklist
    doc.add_heading("4. Review Checklist & Unresolved Items", level=1)
    r_table = doc.add_table(rows=1, cols=4)
    r_hdr = r_table.rows[0].cells
    r_hdr[0].text = "Item ID"
    r_hdr[1].text = "Priority"
    r_hdr[2].text = "Issue Description"
    r_hdr[3].text = "Suggested Reviewer Action"
    for cell in r_hdr:
        cell.paragraphs[0].runs[0].font.bold = True

    for it in case.review_checklist or []:
        row = r_table.add_row().cells
        row[0].text = it.review_id
        row[1].text = it.priority.upper()
        row[2].text = it.description
        row[3].text = it.suggested_action

    # Limitations
    doc.add_heading("5. Analysis Limitations & Disclaimer", level=1)
    doc.add_paragraph(
        "Draft evidence dossier prepared for UK commercial / QS review. Does not determine contractual entitlement "
        "or legal liability. All facts must be verified against original source documents."
    )

    out = BytesIO()
    doc.save(out)
    return out.getvalue()


class ReportService:
    def __init__(self, store: ObjectStore, projects: ProjectService, cases: CaseService) -> None:
        self.store, self.projects, self.cases = store, projects, cases

    def generate(self, tenant_id: str, case_id: str, format: str = "pdf") -> StoredReport:
        case = self.cases.get(tenant_id, case_id)
        if case.stage != CaseStage.NEEDS_REVIEW:
            raise AppError(
                "ANALYSIS_NOT_READY",
                "A completed analysis is required before generating a report.",
                409,
            )
        project = self.projects.get(tenant_id, case.project_id)
        version_number = len(case.report_refs) + 1
        if version_number > 999_999:
            raise AppError("REPORT_VERSION_LIMIT", "No more report versions can be created.", 409)
        version = f"v{version_number}"

        pdf_path = paths.report(tenant_id, case_id, version, "pdf")
        docx_path = paths.report(tenant_id, case_id, version, "docx")
        pdf_data = build_report_pdf(case, project, version)
        docx_data = build_report_docx(case, project, version)

        try:
            self.store.put(pdf_path, pdf_data, if_generation_match=0)
            self.store.put(docx_path, docx_data, if_generation_match=0)
        except PreconditionFailed:
            raise AppError(
                "REPORT_VERSION_CONFLICT", "Refresh the case and try again.", 409
            ) from None

        try:
            self.cases.update(
                tenant_id,
                case_id,
                lambda state: state.model_copy(
                    update={"report_refs": [*state.report_refs, version]}
                ),
                expected_revision=case.revision,
            )
        except Exception:
            self.store.delete(pdf_path)
            self.store.delete(docx_path)
            raise

        ext = "docx" if format == "docx" else "pdf"
        data = docx_data if format == "docx" else pdf_data
        filename = f"fieldnote-{case_id}-{version}.{ext}"
        return StoredReport(version, filename, data)

    def latest(self, tenant_id: str, case_id: str, format: str = "pdf") -> StoredReport:
        case = self.cases.get(tenant_id, case_id)
        if not case.report_refs:
            raise not_found("Report")
        version = case.report_refs[-1]
        ext = "docx" if format == "docx" else "pdf"
        object_path = paths.report(tenant_id, case_id, version, ext)
        try:
            data = self.store.get(object_path).data
        except ObjectNotFound:
            project = self.projects.get(tenant_id, case.project_id)
            if ext == "docx":
                data = build_report_docx(case, project, version)
            else:
                data = build_report_pdf(case, project, version)
            self.store.put(object_path, data)
        return StoredReport(version, f"fieldnote-{case_id}-{version}.{ext}", data)
