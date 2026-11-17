"""Stored PDF reports for completed case analyses."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.graphics import renderPDF
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
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

_INK = colors.HexColor("#183241")
_MUTED = colors.HexColor("#617784")
_LINE = colors.HexColor("#dce5e8")
_PALE = colors.HexColor("#f3f7f8")
_BLUE = colors.HexColor("#4e879d")
_GREEN = colors.HexColor("#5f947d")
_AMBER = colors.HexColor("#d2a35a")
_RED = colors.HexColor("#bb716a")
_GREY = colors.HexColor("#a8b5ba")
_STATUS_COLORS = {
    "parsed": _GREEN,
    "quarantined": _AMBER,
    "blocked": _RED,
    "failed": _RED,
    "unsupported": _GREY,
    "pending": _BLUE,
}


@dataclass(frozen=True)
class StoredReport:
    version: str
    filename: str
    data: bytes


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _paragraph(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text or "—")).replace("\n", "<br/>"), style)


def _source_text(refs: list, documents: dict[str, str]) -> str:
    labels = []
    for ref in refs[:2]:
        name = documents.get(
            ref.document_id or "", "User provided" if ref.user_authored else "Source"
        )
        location = ref.page_ref or ref.visual_region
        label = f"{name}{f' · {location}' if location else ''}"
        labels.append(label)
    return "; ".join(labels) or "Source not recorded"


class EvidenceStatusChart(Flowable):
    def __init__(self, counts: list[tuple[str, int]]) -> None:
        super().__init__()
        self.counts = counts
        self.width = 174 * mm
        self.height = 104

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return self.width, self.height

    def draw(self) -> None:
        if not self.counts:
            self.canv.setFillColor(_MUTED)
            self.canv.setFont("Helvetica", 9)
            self.canv.drawString(0, self.height / 2, "No evidence was recorded for this case.")
            return

        drawing = Drawing(92, 92)
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 0, 0, 92, 92
        pie.data = [count for _, count in self.counts]
        pie.labels = [""] * len(self.counts)
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 1
        for index, (status, _) in enumerate(self.counts):
            pie.slices[index].fillColor = _STATUS_COLORS.get(status, _GREY)
        drawing.add(pie)
        renderPDF.draw(drawing, self.canv, 0, 6)

        labels = {
            "parsed": "Ready to read",
            "quarantined": "Needs checking",
            "blocked": "Blocked",
            "failed": "Could not read",
            "unsupported": "Unsupported file",
            "pending": "Waiting",
        }
        y = self.height - 18
        self.canv.setFont("Helvetica", 9)
        for status, count in self.counts:
            self.canv.setFillColor(_STATUS_COLORS.get(status, _GREY))
            self.canv.rect(108, y - 2, 9, 9, fill=1, stroke=0)
            self.canv.setFillColor(_INK)
            self.canv.drawString(123, y, f"{labels.get(status, status.title())}: {count}")
            y -= 18


class CandidateOverviewChart(Flowable):
    def __init__(self, candidates: list, documents: dict[str, str]) -> None:
        super().__init__()
        self.candidates = candidates[:3]
        self.documents = documents
        self.width = 174 * mm
        self.height = max(32, len(self.candidates) * 48)

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return self.width, self.height

    def draw(self) -> None:
        if not self.candidates:
            self.canv.setFillColor(_MUTED)
            self.canv.setFont("Helvetica", 9)
            self.canv.drawString(0, max(4, self.height / 2), "No possible changes were found.")
            return

        for index, candidate in enumerate(self.candidates):
            top = self.height - index * 48
            y = top - 42
            self.canv.setFillColor(_PALE)
            self.canv.roundRect(0, y, self.width, 42, 5, fill=1, stroke=0)
            self.canv.setFillColor(_BLUE)
            self.canv.roundRect(0, y, 4, 42, 2, fill=1, stroke=0)
            self.canv.setFillColor(_INK)
            self.canv.setFont("Helvetica-Bold", 9)
            self.canv.drawString(12, top - 15, _truncate(candidate.title, 44))
            self.canv.setFillColor(_MUTED)
            self.canv.setFont("Helvetica", 8)
            detail = candidate.proposed_scope_delta or candidate.alleged_original_scope or (
                "The change is not described in the records."
            )
            self.canv.drawString(12, top - 28, _truncate(detail, 95))
            source = _source_text(candidate.evidence, self.documents)
            self.canv.setFont("Helvetica", 7)
            self.canv.drawString(12, top - 38, f"Source: {_truncate(source, 90)}")


class TimelineDiagram(Flowable):
    def __init__(self, events: list, documents: dict[str, str]) -> None:
        super().__init__()
        self.events = sorted(
            events, key=lambda item: (item.event_date is None, item.event_date or "")
        )[:5]
        self.documents = documents
        self.width = 174 * mm
        self.height = max(30, len(self.events) * 48)

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return self.width, self.height

    def draw(self) -> None:
        if not self.events:
            self.canv.setFillColor(_MUTED)
            self.canv.setFont("Helvetica", 9)
            self.canv.drawString(0, self.height / 2, "No events were recorded.")
            return

        marker_x = 8
        self.canv.setStrokeColor(_LINE)
        self.canv.setLineWidth(1.5)
        self.canv.line(marker_x, 8, marker_x, self.height - 8)
        for index, event in enumerate(self.events):
            top = self.height - index * 48
            center = top - 10
            self.canv.setFillColor(_BLUE)
            self.canv.circle(marker_x, center, 4, fill=1, stroke=0)
            self.canv.setFillColor(_INK)
            self.canv.setFont("Helvetica-Bold", 8)
            event_date = (
                event.event_date.strftime("%d %b %Y") if event.event_date else "Date not stated"
            )
            self.canv.drawString(20, top - 12, event_date)
            self.canv.setFont("Helvetica", 8.5)
            self.canv.drawString(90, top - 12, _truncate(event.description, 105))
            source = _source_text(event.evidence, self.documents)
            if source == "Source not recorded" and event.document_id:
                source = (
                    f"{self.documents.get(event.document_id, 'Source')} · "
                    f"{event.page_ref or 'page not recorded'}"
                )
            self.canv.setFillColor(_MUTED)
            self.canv.setFont("Helvetica", 7)
            self.canv.drawString(90, top - 27, f"Source: {_truncate(source, 100)}")


class ReviewChecklistDiagram(Flowable):
    def __init__(self, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self.items = items[:5]
        self.width = 174 * mm
        self.height = max(28, len(self.items) * 34)

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return self.width, self.height

    def draw(self) -> None:
        if not self.items:
            self.canv.setFillColor(_MUTED)
            self.canv.setFont("Helvetica", 9)
            self.canv.drawString(0, self.height / 2, "No open questions were recorded.")
            return

        for index, (kind, text) in enumerate(self.items):
            top = self.height - index * 34
            y = top - 28
            self.canv.setFillColor(_PALE)
            self.canv.roundRect(0, y, self.width, 28, 4, fill=1, stroke=0)
            self.canv.setStrokeColor(_AMBER)
            self.canv.roundRect(8, y + 8, 12, 12, 2, fill=0, stroke=1)
            self.canv.setFillColor(_BLUE)
            self.canv.setFont("Helvetica-Bold", 7)
            self.canv.drawString(28, top - 12, _truncate(kind.upper(), 16))
            self.canv.setFillColor(_INK)
            self.canv.setFont("Helvetica", 8.5)
            self.canv.drawString(100, top - 12, _truncate(text, 100))


def _truncate(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _review_items(case: CaseState) -> list[tuple[str, str]]:
    return (
        [("Question", item.question) for item in case.reviewer_questions]
        + [("Missing file", item.description) for item in case.missing_evidence]
        + [("Mismatch", item.description) for item in case.contradictions]
    )


def build_report_pdf(case: CaseState, project: Project, version: str) -> bytes:
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            alignment=TA_LEFT,
            textColor=_INK,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=_INK,
            spaceBefore=7,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(name="ReportBody", parent=styles["BodyText"], fontSize=9.5, leading=13)
    )
    styles.add(
        ParagraphStyle(
            name="Kicker",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=_BLUE,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Metric",
            parent=styles["BodyText"],
            fontSize=8,
            leading=11,
            alignment=TA_LEFT,
            textColor=_INK,
        )
    )
    body = styles["ReportBody"]
    document_names = {item.document_id: item.filename for item in case.evidence}
    evidence_counts = Counter(_value(item.parser_status) for item in case.evidence)
    ordered_statuses = ["parsed", "quarantined", "blocked", "failed", "unsupported", "pending"]
    chart_counts = [
        (status, evidence_counts[status]) for status in ordered_statuses if evidence_counts[status]
    ]

    def footer(canvas, document) -> None:  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setStrokeColor(_LINE)
        canvas.line(16 * mm, 15 * mm, A4[0] - 16 * mm, 15 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_MUTED)
        canvas.drawString(16 * mm, 9 * mm, "DRAFT · CHECK THE SOURCE FILES")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=20 * mm,
        title=f"Variation evidence report - {case.title}",
        author="Fieldnote",
    )
    review_items = _review_items(case)
    if case.plain_english_summary:
        summary_text = case.plain_english_summary.text
        summary_refs = [
            ref
            for event in case.events
            if event.event_id in case.plain_english_summary.event_ids
            for ref in event.evidence
        ] + [
            ref
            for candidate in case.candidates
            if candidate.candidate_id in case.plain_english_summary.candidate_ids
            for ref in candidate.evidence
        ]
    elif case.candidates:
        summary_text = (
            f"The AI found {len(case.candidates)} possible change(s) to check. "
            "Please check each one against the source files."
        )
        summary_refs = []
    elif case.events:
        summary_text = "The files contain events to review. Check the timeline and questions below."
        summary_refs = []
    else:
        summary_text = "No clear possible change was found. Check the missing details below."
        summary_refs = []

    story = [
        _paragraph("FIELDNOTE · CASE SNAPSHOT", styles["Kicker"]),
        _paragraph(_truncate(case.title, 110), styles["ReportTitle"]),
        _paragraph(
            f"{_truncate(project.name, 70)} · {version} · Draft for a person to check",
            body,
        ),
        _paragraph("What the files show", styles["SectionHeading"]),
    ]
    summary_card = Table(
        [[_paragraph(_truncate(summary_text, 550), body)]],
        colWidths=[178 * mm],
    )
    summary_card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _PALE),
                ("BOX", (0, 0), (-1, -1), 0.8, _LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.extend([summary_card, Spacer(1, 4)])
    if summary_refs:
        story.append(
            _paragraph(
                f"Based on: {_truncate(_source_text(summary_refs, document_names), 160)}", body
            )
        )

    ready_files = sum(
        1
        for item in case.evidence
        if _value(item.parser_status) == "parsed" and item.duplicate_of is None
    )
    metric_cells = [
        (str(len(case.evidence)), "Files"),
        (str(ready_files), "Ready to read"),
        (str(len(case.candidates)), "Possible changes"),
        (str(len(review_items)), "Things to check"),
    ]
    metric_table = Table(
        [
            [
                Paragraph(
                    f"<font size='18'><b>{escape(value)}</b></font><br/>{escape(label)}",
                    styles["Metric"],
                )
                for value, label in metric_cells
            ]
        ],
        colWidths=[44.5 * mm] * 4,
    )
    metric_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend(
        [
            Spacer(1, 5),
            metric_table,
            _paragraph("Your files", styles["SectionHeading"]),
            EvidenceStatusChart(chart_counts),
            _paragraph("Possible changes to check", styles["SectionHeading"]),
            CandidateOverviewChart(case.candidates, document_names),
        ]
    )
    if len(case.candidates) > 3:
        story.append(
            _paragraph(
                f"Showing 3 of {len(case.candidates)} possible changes. See the case for all.",
                body,
            )
        )

    story.extend(
        [
            PageBreak(),
            _paragraph("WHAT HAPPENED", styles["Kicker"]),
            _paragraph("A short timeline", styles["ReportTitle"]),
            TimelineDiagram(case.events, document_names),
            _paragraph("What needs checking", styles["SectionHeading"]),
            ReviewChecklistDiagram(review_items),
        ]
    )
    if len(case.events) > 5 or len(review_items) > 5:
        story.append(
            _paragraph(
                "This short report shows up to 5 events and 5 checks. "
                "See the case for all details.",
                body,
            )
        )
    story.extend(
        [
            Spacer(1, 6),
            _paragraph(
                "Draft only. This report does not decide who is right or give legal advice. "
                "Check every item against the source files.",
                body,
            ),
        ]
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


class ReportService:
    def __init__(self, store: ObjectStore, projects: ProjectService, cases: CaseService) -> None:
        self.store, self.projects, self.cases = store, projects, cases

    def generate(self, tenant_id: str, case_id: str) -> StoredReport:
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
        object_path = paths.report(tenant_id, case_id, version, "pdf")
        filename = f"fieldnote-{case_id}-{version}.pdf"
        data = build_report_pdf(case, project, version)
        try:
            self.store.put(object_path, data, if_generation_match=0)
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
            self.store.delete(object_path)
            raise
        return StoredReport(version, filename, data)

    def latest(self, tenant_id: str, case_id: str) -> StoredReport:
        case = self.cases.get(tenant_id, case_id)
        if not case.report_refs:
            raise not_found("Report")
        version = case.report_refs[-1]
        object_path = paths.report(tenant_id, case_id, version, "pdf")
        try:
            data = self.store.get(object_path).data
        except ObjectNotFound:
            raise not_found("Report") from None
        return StoredReport(version, f"fieldnote-{case_id}-{version}.pdf", data)
