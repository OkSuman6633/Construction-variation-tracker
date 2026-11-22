from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..auth import TenantContext
from ..deps import Container, get_container, get_tenant
from ..errors import AppError
from ..schemas_api import RegisterEvidence, UploadUrlRequest
from ..services.idempotency import fingerprint, run_idempotent
from ..services.jobs import RetryableJobError
from ..services.reports import _document_summaries
from construction_variation_agent.shared_libraries.schemas import (
    ChangedPosition,
    CommercialPosition,
    CompleteChronologyEvent,
    DocumentRegisterItem,
    OriginalPosition,
    QuantityProgressPosition,
    ReviewItem,
    VariationPosition,
)

router = APIRouter()


def _json(status: int, body: dict[str, Any], replayed: bool) -> JSONResponse:
    resp = JSONResponse(body, status_code=status)
    if replayed:
        resp.headers["Idempotent-Replayed"] = "true"
    return resp


@router.post("/v1/cases/{case_id}/upload-url")
def upload_url(
    case_id: str,
    data: UploadUrlRequest,
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    ctx.require("evidence:upload")
    return c.evidence.request_upload(ctx, case_id, data)


@router.put("/v1/cases/{case_id}/uploads/{document_id}", status_code=204)
async def put_upload(
    case_id: str,
    document_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> Response:
    """LOCAL DEV ONLY (404 when STORAGE_BACKEND=gcs): production clients upload to the signed URL."""
    ctx.require("evidence:upload")
    c.evidence.put_local(ctx, case_id, document_id, await request.body())
    return Response(status_code=204)


@router.post("/v1/cases/{case_id}/evidence", status_code=201)
def register_evidence(
    case_id: str,
    data: RegisterEvidence,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> JSONResponse:
    ctx.require("evidence:upload")

    def handler() -> tuple[int, dict[str, Any]]:
        meta = c.evidence.register(ctx, case_id, data)
        c.audit_event(
            ctx,
            action="evidence.register",
            object_type="document",
            object_id=meta.document_id,
            detail={
                "mime": meta.mime_type,
                "size": meta.size_bytes,
                "status": meta.parser_status.value,
                "duplicate": meta.duplicate_of is not None,
            },
        )
        return 201, meta.model_dump(mode="json")

    fp = fingerprint("POST", request.url.path, data.model_dump(mode="json"))
    return _json(*run_idempotent(c.store, ctx.tenant_id, idempotency_key, fp, handler))


@router.post("/v1/cases/{case_id}/analyse", status_code=202)
def analyse(
    case_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> JSONResponse:
    ctx.require("analyse")

    def handler() -> tuple[int, dict[str, Any]]:
        job, duplicate = c.jobs.create(ctx, case_id)
        return 202, {
            "job_id": job.job_id,
            "status": job.status.value,
            "duplicate_active_job": duplicate,
        }

    fp = fingerprint("POST", request.url.path, {"case_id": case_id})
    return _json(*run_idempotent(c.store, ctx.tenant_id, idempotency_key, fp, handler))


@router.get("/v1/jobs/{job_id}")
def get_job(
    job_id: str, ctx: TenantContext = Depends(get_tenant), c: Container = Depends(get_container)
) -> dict[str, Any]:
    ctx.require("case:read")
    j = c.jobs.get(ctx, job_id)
    return {
        "job_id": j.job_id,
        "case_id": j.case_id,
        "status": j.status.value,
        "stage": j.stage,
        "attempt": j.attempt,
        "failure_code": j.failure_code,
        "failure_message": j.failure_message,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


@router.get("/v1/cases/{case_id}/timeline")
def timeline(
    case_id: str, ctx: TenantContext = Depends(get_tenant), c: Container = Depends(get_container)
) -> dict[str, Any]:
    ctx.require("case:read")
    s = c.cases.get(ctx.tenant_id, case_id)
    events = sorted(s.events, key=lambda e: (e.event_date is None, e.event_date or "", e.event_id))
    dump = lambda items: [  # noqa: E731
        i.model_dump(mode="json") if hasattr(i, "model_dump") else i for i in (items or [])
    ]
    doc_summaries = _document_summaries(s)
    progression = [
        {
            "step": idx,
            "document_id": item["document_id"],
            "filename": item["filename"],
            "category": item["category"],
            "date": item["date"],
            "change_summary": item["summary"],
        }
        for idx, item in enumerate(doc_summaries, 1)
    ]

    vps = getattr(s, "variation_positions", None) or []
    if not vps and s.candidates:
        try:
            c_cand = s.candidates[0]
            vps = [
                VariationPosition(
                    variation_id=c_cand.candidate_id,
                    variation_title=c_cand.title,
                    project_name=getattr(s, "title", "Project"),
                    alleged_change=c_cand.proposed_scope_delta or c_cand.title,
                    evidence_status="Awaiting professional review",
                    original_position=OriginalPosition(
                        work_description=c_cand.alleged_original_scope or "Original contract scope"
                    ),
                    changed_position=ChangedPosition(
                        work_description=c_cand.proposed_scope_delta or c_cand.title,
                        instruction_ref=(
                            c_cand.related_instruction_refs[0] if c_cand.related_instruction_refs else None
                        ),
                    ),
                    quantity_progress=QuantityProgressPosition(
                        item_description=c_cand.title,
                        completion_evidence_status="Under review",
                    ),
                    commercial_position=CommercialPosition(
                        original_quoted_amount=None,
                        revised_quoted_amount=c_cand.amount,
                        calculated_difference=c_cand.amount,
                        currency=c_cand.currency or "GBP",
                        commercial_acceptance_status="Commercial acceptance not established in reviewed evidence",
                    ),
                )
            ]
        except Exception:
            vps = []

    chron = getattr(s, "complete_chronology", None) or []
    if not chron and events:
        try:
            doc_map = {d.document_id: d.filename for d in s.evidence}
            chron = [
                CompleteChronologyEvent(
                    event_id=ev.event_id,
                    event_date=ev.event_date.isoformat() if hasattr(ev.event_date, "isoformat") else str(ev.event_date),
                    event_time=getattr(ev, "event_time", None),
                    event_type=ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type),
                    description=ev.description,
                    parties=ev.parties,
                    document_id=ev.document_id or (s.evidence[0].document_id if s.evidence else "doc-default"),
                    document_name=doc_map.get(ev.document_id, "Document"),
                    page_ref=ev.source_reference.page_ref if (ev.source_reference and ev.source_reference.page_ref) else "p.1",
                    quoted_span=ev.source_reference.quoted_span if ev.source_reference else None,
                    verification_status="verified_from_evidence",
                )
                for ev in events
            ]
        except Exception:
            chron = []

    rev_check = getattr(s, "review_checklist", None) or []
    if not rev_check and s.reviewer_questions:
        try:
            rev_check = [
                ReviewItem(
                    review_id=f"rev-{q.question_id}",
                    category="other",
                    priority="high" if getattr(q, "importance", "MEDIUM") == "HIGH" else "medium",
                    issue_title=f"Review: {q.question[:60]}",
                    description=q.question,
                    why_requires_verification="Requires verification against primary source documents.",
                    suggested_question=q.question,
                    suggested_action="Investigate and verify with project records",
                    status="pending_review",
                )
                for q in s.reviewer_questions
            ]
        except Exception:
            rev_check = []

    doc_reg = getattr(s, "document_register", None) or []
    if not doc_reg and s.evidence:
        try:
            doc_reg = [
                DocumentRegisterItem(
                    document_id=d.document_id,
                    filename=d.filename,
                    category=d.category.value if d.category else "unclassified",
                    parsing_status=d.parser_status.value if d.parser_status else "parsed",
                    events_extracted_count=len([e for e in events if e.document_id == d.document_id]),
                    source_availability="ready_to_read",
                )
                for d in s.evidence
            ]
        except Exception:
            doc_reg = []

    return {
        "case_id": s.case_id,
        "revision": s.revision,
        "stage": s.stage.value,
        "active_job_id": s.active_job_id,
        "events": dump(events),
        "candidates": dump(s.candidates),
        "relationships": dump(s.relationships),
        "contradictions": dump(s.contradictions),
        "missing_evidence": dump(s.missing_evidence),
        "reviewer_questions": dump(s.reviewer_questions),
        "plain_english_summary": (
            s.plain_english_summary.model_dump(mode="json") if s.plain_english_summary else None
        ),
        "document_register": dump(doc_reg),
        "variation_positions": dump(vps),
        "original_scope": getattr(s, "original_scope", []),
        "alleged_changes": getattr(s, "alleged_changes", []),
        "complete_chronology": dump(chron),
        "commercial_analysis": dump(getattr(s, "commercial_analysis", [])),
        "work_progress_analysis": dump(getattr(s, "work_progress_analysis", [])),
        "review_checklist": dump(rev_check),
        "analysis_limitations": getattr(s, "analysis_limitations", []),
        "report_readiness": (
            s.report_readiness.model_dump(mode="json")
            if getattr(s, "report_readiness", None)
            else None
        ),
        "analysis_metadata": (
            s.analysis_metadata.model_dump(mode="json")
            if getattr(s, "analysis_metadata", None)
            else None
        ),
        "document_summaries": doc_summaries,
        "document_progression": progression,
        "evidence": [
            {
                "document_id": d.document_id,
                "filename": d.filename,
                "mime_type": d.mime_type,
                "parser_status": d.parser_status.value,
                "page_count": d.page_count,
                "category": d.category.value if d.category else None,
                "duplicate_of": d.duplicate_of,
                "recorded_event_date": d.recorded_event_date,
            }
            for d in s.evidence
        ],
        "notice": (
            "DRAFT - Secure, evidence-led factual record for UK construction subcontractor and "
            "commercial teams. Assembles facts behind possible variation claims (changed scope, "
            "instructions, delays, disruption, price consequences); not an automatic contractual "
            "entitlement or legal determination."
        ),
    }


@router.post("/v1/cases/{case_id}/report")
def generate_report(
    case_id: str,
    format: str = Query(default="pdf", pattern="^(pdf|docx)$"),
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> Response:
    ctx.require("case:read")
    report = c.reports.generate(ctx.tenant_id, case_id, format=format)
    c.audit_event(
        ctx,
        action="case.report.generate",
        object_type="report",
        object_id=case_id,
        detail={"version": report.version, "format": format},
    )
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if format == "docx"
        else "application/pdf"
    )
    return Response(
        content=report.data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{report.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Report-Version": report.version,
        },
    )


@router.get("/v1/cases/{case_id}/report")
def latest_report(
    case_id: str,
    format: str = Query(default="pdf", pattern="^(pdf|docx)$"),
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> Response:
    ctx.require("case:read")
    report = c.reports.latest(ctx.tenant_id, case_id, format=format)
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if format == "docx"
        else "application/pdf"
    )
    return Response(
        content=report.data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{report.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Report-Version": report.version,
        },
    )


class TaskBody(BaseModel):
    tenant_id: str
    job_id: str


@router.post("/internal/tasks/analyse")
async def worker_analyse(
    body: TaskBody, request: Request, c: Container = Depends(get_container)
) -> JSONResponse:
    """Queue-delivered worker entry point (not for browsers). 200 = handled, 503 = retry."""
    c.task_verifier.verify(request)
    attempt = int(request.headers.get("x-cloudtasks-taskretrycount", "0")) + 1
    try:
        outcome = await c.jobs.execute(body.tenant_id, body.job_id, attempt)
    except RetryableJobError as exc:
        return JSONResponse({"status": "retry", "reason": str(exc)}, status_code=503)
    except AppError as exc:
        return JSONResponse(exc.body(), status_code=exc.status if exc.status != 404 else 200)
    return JSONResponse({"status": outcome})
