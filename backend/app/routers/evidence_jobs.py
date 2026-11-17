from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..auth import TenantContext
from ..deps import Container, get_container, get_tenant
from ..errors import AppError
from ..schemas_api import RegisterEvidence, UploadUrlRequest
from ..services.idempotency import fingerprint, run_idempotent
from ..services.jobs import RetryableJobError

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
    dump = lambda items: [i.model_dump(mode="json") for i in items]  # noqa: E731
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
        "notice": "DRAFT - AI-assisted findings for professional review. Not legal advice; no entitlement is determined.",
    }


@router.post("/v1/cases/{case_id}/report")
def generate_report(
    case_id: str,
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> Response:
    ctx.require("case:read")
    report = c.reports.generate(ctx.tenant_id, case_id)
    c.audit_event(
        ctx,
        action="case.report.generate",
        object_type="report",
        object_id=case_id,
        detail={"version": report.version},
    )
    return Response(
        content=report.data,
        media_type="application/pdf",
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
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> Response:
    ctx.require("case:read")
    report = c.reports.latest(ctx.tenant_id, case_id)
    return Response(
        content=report.data,
        media_type="application/pdf",
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
