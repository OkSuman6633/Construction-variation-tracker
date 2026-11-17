from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from construction_variation_agent.shared_libraries.schemas import CaseCreate, ProjectCreate

from ..auth import TenantContext
from ..deps import Container, get_container, get_tenant
from ..services.idempotency import fingerprint, run_idempotent

router = APIRouter(prefix="/v1")


def _json(status: int, body: dict[str, Any], replayed: bool) -> JSONResponse:
    resp = JSONResponse(body, status_code=status)
    if replayed:
        resp.headers["Idempotent-Replayed"] = "true"
    return resp


@router.post("/projects", status_code=201)
def create_project(
    data: ProjectCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> JSONResponse:
    ctx.require("project:write")

    def handler() -> tuple[int, dict[str, Any]]:
        p = c.projects.create(ctx.tenant_id, ctx.user_id, data)
        c.audit_event(ctx, action="project.create", object_type="project", object_id=p.project_id)
        return 201, p.model_dump(mode="json")

    fp = fingerprint("POST", request.url.path, data.model_dump(mode="json"))
    return _json(*run_idempotent(c.store, ctx.tenant_id, idempotency_key, fp, handler))


@router.get("/projects")
def list_projects(
    ctx: TenantContext = Depends(get_tenant), c: Container = Depends(get_container)
) -> dict[str, Any]:
    ctx.require("case:read")
    return {"items": [p.model_dump(mode="json") for p in c.projects.list(ctx.tenant_id)]}


@router.get("/projects/{project_id}")
def get_project(
    project_id: str, ctx: TenantContext = Depends(get_tenant), c: Container = Depends(get_container)
) -> dict[str, Any]:
    ctx.require("case:read")
    return c.projects.get(ctx.tenant_id, project_id).model_dump(mode="json")


@router.post("/cases", status_code=201)
def create_case(
    data: CaseCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> JSONResponse:
    ctx.require("case:create")

    def handler() -> tuple[int, dict[str, Any]]:
        s = c.cases.create(ctx.tenant_id, ctx.user_id, data)
        c.audit_event(
            ctx,
            action="case.create",
            object_type="case",
            object_id=s.case_id,
            object_version=s.revision,
        )
        return 201, {"case_id": s.case_id, "revision": s.revision}

    fp = fingerprint("POST", request.url.path, data.model_dump(mode="json"))
    return _json(*run_idempotent(c.store, ctx.tenant_id, idempotency_key, fp, handler))


@router.get("/cases")
def list_cases(
    project_id: str | None = None,
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    ctx.require("case:read")
    return {"items": [s.model_dump(mode="json") for s in c.cases.list(ctx.tenant_id, project_id)]}


@router.get("/cases/{case_id}")
def get_case(
    case_id: str, ctx: TenantContext = Depends(get_tenant), c: Container = Depends(get_container)
) -> dict[str, Any]:
    ctx.require("case:read")
    return c.cases.get(ctx.tenant_id, case_id).model_dump(mode="json")
