"""Application factory."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from construction_variation_agent.model_config import resolve_model

from .auth import TokenVerifier
from .config import Settings, get_settings
from .deps import Container
from .errors import AppError
from .middleware import RequestGuardMiddleware
from .routers import core, evidence_jobs, projects_cases
from .services.audit import AuditSink, BigQueryAuditSink, JsonlAuditSink, SQLiteAuditSink
from .services.cases import CaseService, ProjectService
from .services.evidence import EvidenceService
from .services.jobs import JobService
from .services.local_auth import LocalAuthService
from .services.membership import MembershipService
from .services.queue import CloudTasksQueue, InlineThreadQueue, ManualQueue, TaskQueue
from .services.reports import ReportService
from .services.scanner import DevNoopScanner, Scanner, UnavailableScanner
from .services.task_auth import GoogleOidcTaskVerifier, NoopTaskVerifier, TaskVerifier
from .storage.base import ObjectStore
from .storage.gcs import GcsObjectStore
from .storage.local import LocalObjectStore
from .storage.sqlite import SQLiteObjectStore

log = logging.getLogger("cvea.api")
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def resolve_local_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return path.resolve()


def build_container(s: Settings) -> Container:
    s.assert_safe_for_env()
    store: ObjectStore
    if s.storage_backend == "gcs":
        if not s.gcs_bucket:
            raise RuntimeError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        store = GcsObjectStore(s.gcs_bucket, s.gcp_project)
    elif s.storage_backend == "sqlite":
        database_path = s.sqlite_database_path or str(Path(s.local_data_dir) / "cvea.db")
        resolved_database_path = resolve_local_path(database_path)
        store = SQLiteObjectStore(resolved_database_path)
        log.info("Local SQLite database: %s", resolved_database_path)
    else:
        store = LocalObjectStore(resolve_local_path(Path(s.local_data_dir) / "objects"))
    audit: AuditSink
    if s.audit_backend == "bigquery":
        if not (s.gcp_project and s.bigquery_dataset):
            raise RuntimeError("GCP_PROJECT and BIGQUERY_DATASET are required for bigquery audit")
        audit = BigQueryAuditSink(s.gcp_project, s.bigquery_dataset)
    elif s.audit_backend == "sqlite":
        database_path = s.sqlite_database_path or str(Path(s.local_data_dir) / "cvea.db")
        audit = SQLiteAuditSink(resolve_local_path(database_path))
    else:
        audit = JsonlAuditSink(
            resolve_local_path(Path(s.local_data_dir) / "audit" / "audit_events.jsonl")
        )
    projects = ProjectService(store)
    cases = CaseService(store, projects)
    reports = ReportService(store, projects, cases)
    scanner: Scanner = DevNoopScanner() if s.scanner_backend == "dev_noop" else UnavailableScanner()
    holder: dict[str, JobService] = {}

    def run_job(tenant_id: str, job_id: str) -> None:
        try:
            asyncio.run(holder["jobs"].execute(tenant_id, job_id))
        except Exception:  # noqa: BLE001
            log.error("background job crashed: %s", job_id, exc_info=True)

    queue: TaskQueue
    verifier: TaskVerifier = NoopTaskVerifier()
    if s.task_queue_backend == "cloud_tasks":
        if not (s.gcp_project and s.tasks_queue and s.worker_url and s.task_service_account):
            raise RuntimeError(
                "GCP_PROJECT, TASKS_QUEUE, WORKER_URL and TASK_SERVICE_ACCOUNT are required"
            )
        queue = CloudTasksQueue(
            s.gcp_project, s.gcp_location, s.tasks_queue, s.worker_url, s.task_service_account
        )
        verifier = GoogleOidcTaskVerifier(
            s.worker_url.rstrip("/") + "/internal/tasks/analyse", s.task_service_account
        )
    elif s.task_queue_backend == "manual":
        queue = ManualQueue()
    else:
        queue = InlineThreadQueue(run_job)

    def model_factory():  # type: ignore[no-untyped-def]
        key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
        return resolve_model(s.gemini_model, key, s.google_genai_use_vertexai)

    jobs = JobService(store, cases, queue, audit, s, model_factory)
    holder["jobs"] = jobs
    return Container(
        settings=s,
        store=store,
        audit=audit,
        verifier=TokenVerifier(s),
        members=MembershipService(store),
        local_auth=(
            LocalAuthService(store, s)
            if s.app_env in {"local", "test"} and s.auth_dev_hs256_secret is not None
            else None
        ),
        projects=projects,
        cases=cases,
        reports=reports,
        queue=queue,
        scanner=scanner,
        evidence=EvidenceService(store, cases, scanner, s),
        jobs=jobs,
        task_verifier=verifier,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    app = FastAPI(title="Construction Variation Evidence Agent API", version="0.1.0")
    app.state.container = build_container(s)
    app.add_middleware(
        RequestGuardMiddleware,
        per_minute=s.rate_limit_per_minute,
        max_body=s.max_json_body_bytes,
        max_upload=s.max_upload_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_allowed_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Correlation-ID",
            "X-Tenant-ID",
        ],
        expose_headers=["Idempotent-Replayed", "X-Correlation-ID"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(".".join(str(x) for x in e["loc"]) for e in exc.errors())
        return JSONResponse(
            AppError("VALIDATION_ERROR", f"Invalid request: {fields}", 422).body(), status_code=422
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log type only: messages/bodies may contain customer content.
        log.error("unhandled error: %s", type(exc).__name__)
        cid = request.headers.get("x-correlation-id", "-")[:100]
        body = {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "correlation_id": cid,
            "retryable": True,
        }
        return JSONResponse(body, status_code=500)

    app.include_router(core.router)
    app.include_router(projects_cases.router)
    app.include_router(evidence_jobs.router)
    return app
