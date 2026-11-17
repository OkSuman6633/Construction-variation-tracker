from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from ..auth import Principal, TenantContext
from ..deps import Container, get_container, get_principal, get_tenant
from ..errors import AppError
from ..schemas_api import LocalLogin, LocalSignup, MemberCreate, OrganisationCreate, PasswordChange
from ..services.audit import AuditEvent
from ..services.idempotency import fingerprint, run_idempotent

router = APIRouter()


def _json(status: int, body: dict[str, Any], replayed: bool) -> JSONResponse:
    response = JSONResponse(body, status_code=status)
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return response


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/me")
def me(p: Principal = Depends(get_principal)) -> dict[str, str | None]:
    return {"user_id": p.user_id, "email": p.email}


@router.post("/v1/auth/signup", status_code=201)
def local_signup(data: LocalSignup, c: Container = Depends(get_container)) -> dict[str, Any]:
    if c.local_auth is None:
        raise AppError("LOCAL_AUTH_DISABLED", "Local password sign-in is disabled.", 404)
    return c.local_auth.register(data.name, data.email, data.password)


@router.post("/v1/auth/login")
def local_login(data: LocalLogin, c: Container = Depends(get_container)) -> dict[str, Any]:
    if c.local_auth is None:
        raise AppError("LOCAL_AUTH_DISABLED", "Local password sign-in is disabled.", 404)
    return c.local_auth.authenticate(data.email, data.password)


@router.get("/v1/auth/mode")
def auth_mode(c: Container = Depends(get_container)) -> dict[str, bool]:
    return {"local_password_enabled": c.local_auth is not None}


@router.post("/v1/auth/change-password")
def change_password(
    data: PasswordChange,
    p: Principal = Depends(get_principal),
    c: Container = Depends(get_container),
) -> dict[str, str]:
    if c.local_auth is None:
        raise AppError("LOCAL_AUTH_DISABLED", "Local password sign-in is disabled.", 404)
    c.local_auth.change_password(p.user_id, p.email, data.current_password, data.new_password)
    return {"status": "updated"}


@router.get("/v1/organisations")
def organisations(
    p: Principal = Depends(get_principal), c: Container = Depends(get_container)
) -> dict[str, list[dict[str, str]]]:
    return {"organisations": c.members.organisations_for(p.user_id)}


@router.post("/v1/organisations", status_code=201)
def create_organisation(
    data: OrganisationCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    p: Principal = Depends(get_principal),
    c: Container = Depends(get_container),
) -> JSONResponse:
    def handler() -> tuple[int, dict[str, Any]]:
        organisation = c.members.create_organisation(p.user_id, p.email, data.name)
        c.audit.append(
            AuditEvent(
                action="organisation.create",
                actor_id=p.user_id,
                tenant_id=organisation.tenant_id,
                object_type="organisation",
                object_id=organisation.tenant_id,
            )
        )
        return 201, {
            "tenant_id": organisation.tenant_id,
            "name": organisation.name,
            "role": "owner",
        }

    user_scope = "usr-" + hashlib.sha256(p.user_id.encode()).hexdigest()[:16]
    fp = fingerprint("POST", request.url.path, data.model_dump(mode="json"))
    return _json(
        *run_idempotent(c.store, user_scope, idempotency_key, fp, handler)
    )


@router.get("/v1/organisations/{tenant_id}/members")
def list_members(
    tenant_id: str,
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    if tenant_id != ctx.tenant_id:
        raise AppError("FORBIDDEN", "You do not have permission to do this.", 403)
    ctx.require("member:manage")
    return {"members": [m.model_dump() for m in c.members.list_members(ctx.tenant_id)]}


@router.post("/v1/organisations/{tenant_id}/members", status_code=201)
def create_member(
    tenant_id: str,
    data: MemberCreate,
    ctx: TenantContext = Depends(get_tenant),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    if tenant_id != ctx.tenant_id:
        raise AppError("FORBIDDEN", "You do not have permission to do this.", 403)
    ctx.require("member:manage")
    if c.local_auth is None:
        raise AppError("LOCAL_AUTH_DISABLED", "Local teammate accounts are disabled.", 404)
    user, temporary_password = c.local_auth.create_teammate(data.email)
    c.members.add_member(ctx.tenant_id, user.user_id, user.email, data.role, user.name)
    c.audit_event(
        ctx,
        action="organisation.member.add",
        object_type="user",
        object_id=user.user_id,
        outcome="success",
    )
    return {
        "member": {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "role": data.role,
        },
        "temporary_password": temporary_password,
    }
