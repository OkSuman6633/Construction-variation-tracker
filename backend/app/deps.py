"""FastAPI dependencies: container, authenticated principal, tenant context."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request

from .auth import Principal, TenantContext, TokenVerifier
from .config import Settings
from .errors import AppError
from .services.audit import AuditEvent, AuditSink
from .services.cases import CaseService, ProjectService
from .services.evidence import EvidenceService
from .services.jobs import JobService
from .services.local_auth import LocalAuthService
from .services.membership import MembershipService
from .services.queue import TaskQueue
from .services.reports import ReportService
from .services.scanner import Scanner
from .services.task_auth import TaskVerifier
from .storage.base import ObjectStore


@dataclass
class Container:
    settings: Settings
    store: ObjectStore
    audit: AuditSink
    verifier: TokenVerifier
    members: MembershipService
    local_auth: LocalAuthService | None
    projects: ProjectService
    cases: CaseService
    reports: ReportService
    queue: TaskQueue
    scanner: Scanner
    evidence: EvidenceService
    jobs: JobService
    task_verifier: TaskVerifier

    def audit_event(self, ctx: TenantContext, **kw) -> None:  # type: ignore[no-untyped-def]
        self.audit.append(AuditEvent(actor_id=ctx.user_id, tenant_id=ctx.tenant_id, **kw))


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_principal(
    authorization: str | None = Header(default=None),
    c: Container = Depends(get_container),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("UNAUTHENTICATED", "Missing bearer token.", 401)
    return c.verifier.verify(authorization[7:].strip())


def get_tenant(
    x_tenant_id: str | None = Header(default=None),
    principal: Principal = Depends(get_principal),
    c: Container = Depends(get_container),
) -> TenantContext:
    if not x_tenant_id:
        raise AppError("TENANT_REQUIRED", "X-Tenant-ID header is required.", 400)
    return c.members.resolve(x_tenant_id, principal.user_id, principal.email)
