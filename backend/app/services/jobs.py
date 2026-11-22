"""Analysis jobs: idempotent creation, replay-safe worker execution, controlled persistence."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from construction_variation_agent.shared_libraries.runtime import JobLimits
from construction_variation_agent.shared_libraries.schemas import (
    AnalysisResult,
    CaseAnalysisRequest,
    CaseState,
    JobRecord,
)
from construction_variation_agent.shared_libraries.types import (
    CaseStage,
    DocumentCategory,
    JobStatus,
    ParserStatus,
)

from ..analysis.provider import IntegrityError, StorageEvidenceProvider
from ..analysis.runner import AnalysisOutcome, run_analysis
from ..auth import TenantContext
from ..config import Settings
from ..errors import AppError, correlation_id_var, not_found
from ..storage import paths
from ..storage.base import ObjectNotFound, ObjectStore, PreconditionFailed
from .audit import AuditEvent, AuditSink
from .cases import CaseService, new_id
from .queue import TaskQueue

log = logging.getLogger("cvea.jobs")
TERMINAL = {JobStatus.NEEDS_REVIEW, JobStatus.COMPLETED, JobStatus.FAILED}


class RetryableJobError(Exception):
    """Raised to the worker endpoint so the queue redelivers (HTTP 503)."""


class JobService:
    def __init__(
        self,
        store: ObjectStore,
        cases: CaseService,
        queue: TaskQueue,
        audit: AuditSink,
        settings: Settings,
        model_factory: Callable[[], Any],
    ) -> None:
        self.store, self.cases, self.queue, self.audit = store, cases, queue, audit
        self.s, self.model_factory = settings, model_factory

    # ------------------------------------------------------------- storage helpers
    def _load(self, tenant_id: str, job_id: str) -> tuple[JobRecord, int]:
        try:
            obj = self.store.get(paths.job(tenant_id, job_id))
        except ObjectNotFound:
            raise not_found("Job") from None
        return JobRecord.model_validate_json(obj.data), obj.generation

    def get(self, ctx: TenantContext, job_id: str) -> JobRecord:
        return self._load(ctx.tenant_id, job_id)[0]

    def _save(self, job: JobRecord, generation: int) -> int:
        job.updated_at = datetime.now(UTC)
        return self.store.put(
            paths.job(job.tenant_id, job.job_id),
            job.model_dump_json().encode(),
            if_generation_match=generation,
        )

    def _audit(self, job: JobRecord, action: str, outcome: str = "success", **detail: Any) -> None:
        self.audit.append(
            AuditEvent(
                correlation_id=job.correlation_id,
                action=action,
                actor_id=job.requested_by,
                tenant_id=job.tenant_id,
                object_type="job",
                object_id=job.job_id,
                outcome=outcome,
                detail={
                    k: v
                    for k, v in detail.items()
                    if isinstance(v, str | int | float | bool | type(None))
                },
            )
        )

    @staticmethod
    def eligible_documents(case: CaseState) -> list[str]:
        return [
            d.document_id
            for d in case.evidence
            if d.parser_status == ParserStatus.PARSED and d.duplicate_of is None
        ]

    # ------------------------------------------------------------- API side
    def create(self, ctx: TenantContext, case_id: str) -> tuple[JobRecord, bool]:
        case = self.cases.get(ctx.tenant_id, case_id)
        if case.reviews:
            raise AppError(
                "ANALYSIS_LOCKED_AFTER_REVIEW",
                "Reviewer decisions exist; re-running analysis would overwrite them.",
                409,
            )
        if case.active_job_id:
            try:
                active, active_gen = self._load(ctx.tenant_id, case.active_job_id)
                if active.status in {JobStatus.QUEUED, JobStatus.WORKING}:
                    age = (datetime.now(UTC) - active.updated_at).total_seconds()
                    if age < self.s.job_timeout_seconds:
                        return active, True  # repeated request: no duplicate job, no duplicate charge
                    active.status = JobStatus.FAILED
                    active.failure_code = "JOB_TIMED_OUT"
                    active.failure_message = "Previous job did not complete within the timeout window."
                    self._save(active, active_gen)
            except AppError:
                pass
        docs = self.eligible_documents(case)
        if not docs:
            raise AppError(
                "NO_ANALYSABLE_EVIDENCE",
                "No scanned, readable, non-duplicate documents are available to analyse.",
                422,
            )
        job = JobRecord(
            job_id=new_id("job"),
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            requested_by=ctx.user_id,
            correlation_id=correlation_id_var.get(),
            stage="queued",
        )
        self.store.put(
            paths.job(ctx.tenant_id, job.job_id),
            job.model_dump_json().encode(),
            if_generation_match=0,
        )

        def mutate(s: CaseState) -> CaseState:
            s.active_job_id, s.stage = job.job_id, CaseStage.ANALYSING
            return s

        try:
            self.cases.update(ctx.tenant_id, case_id, mutate, expected_revision=case.revision)
        except AppError:
            self.store.delete(paths.job(ctx.tenant_id, job.job_id))
            raise
        self._audit(job, "job.queued", documents=len(docs))
        self.queue.enqueue(ctx.tenant_id, job.job_id)
        return job, False

    # ------------------------------------------------------------- worker side
    def _set_stage(self, tenant_id: str, job_id: str, stage: str) -> None:
        for _ in range(3):
            job, gen = self._load(tenant_id, job_id)
            job.stage = stage
            try:
                self._save(job, gen)
                return
            except PreconditionFailed:
                continue

    async def execute(self, tenant_id: str, job_id: str, attempt: int = 1) -> str:
        """Run a queued job. Returns 'done', 'skipped' or raises RetryableJobError.

        Safe against duplicate delivery: terminal jobs are skipped; a job claimed by another
        worker within the timeout window is skipped; a stale claim is reclaimed.
        """
        job, gen = self._load(tenant_id, job_id)
        if job.status in TERMINAL:
            return "skipped"
        fresh = (
            datetime.now(UTC) - job.updated_at
        ).total_seconds() < self.s.job_timeout_seconds + 60
        if job.status == JobStatus.WORKING and fresh and job.attempt >= attempt:
            return "skipped"
        job.status, job.attempt, job.stage = JobStatus.WORKING, attempt, "starting"
        try:
            gen = self._save(job, gen)
        except PreconditionFailed:
            return "skipped"  # another worker claimed it first
        self._audit(job, "job.started", attempt=attempt)

        case = self.cases.get(tenant_id, job.case_id)
        docs = self.eligible_documents(case)
        started = time.monotonic()
        try:
            request = CaseAnalysisRequest(
                tenant_id=tenant_id,
                case_id=job.case_id,
                job_id=job_id,
                correlation_id=job.correlation_id,
                document_ids=docs,
            )
            provider = StorageEvidenceProvider(self.store, case, docs)

            def sink(ev: dict[str, Any]) -> None:
                if ev.get("kind") == "stage_start":
                    self._set_stage(tenant_id, job_id, ev["stage"])

            outcome = await run_analysis(
                request,
                provider,
                model=self.model_factory(),
                limits=JobLimits(timeout_seconds=self.s.job_timeout_seconds),
                trace_sink=sink,
                attempt=attempt,
            )
        except IntegrityError:
            return self._fail(
                tenant_id,
                job_id,
                "INTEGRITY_ERROR",
                "A stored file failed its checksum.",
                False,
                attempt,
            )
        if not outcome.ok:
            f = outcome.failure
            assert f is not None
            return self._fail(tenant_id, job_id, f.code, f.message, f.retryable, attempt)
        assert outcome.result is not None and outcome.report is not None
        self._complete(tenant_id, job_id, outcome, time.monotonic() - started)
        return "done"

    def _complete(
        self, tenant_id: str, job_id: str, outcome: AnalysisOutcome, seconds: float
    ) -> None:
        result: AnalysisResult = outcome.result  # type: ignore[assignment]
        categories = {
            d["document_id"]: d["category"] for d in (outcome.intake or {}).get("documents", [])
        }
        job, gen = self._load(tenant_id, job_id)

        def mutate(s: CaseState) -> CaseState:
            s.events, s.candidates, s.relationships = (
                result.events,
                result.candidates,
                result.relationships,
            )
            s.contradictions, s.missing_evidence = result.contradictions, result.missing_evidence
            s.reviewer_questions = result.reviewer_questions
            s.plain_english_summary = result.plain_english_summary
            s.document_register = result.document_register
            s.variation_positions = result.variation_positions
            s.original_scope = result.original_scope
            s.alleged_changes = result.alleged_changes
            s.complete_chronology = result.complete_chronology
            s.commercial_analysis = result.commercial_analysis
            s.work_progress_analysis = result.work_progress_analysis
            s.review_checklist = result.review_checklist
            s.analysis_limitations = result.analysis_limitations
            s.report_readiness = result.report_readiness
            s.analysis_metadata = result.analysis_metadata
            for d in s.evidence:
                if d.document_id in categories:
                    d.category = DocumentCategory(categories[d.document_id])
            s.stage, s.active_job_id = CaseStage.NEEDS_REVIEW, None
            return s

        self.cases.update(
            tenant_id, job.case_id, mutate
        )  # CAS inside; stale -> 409 -> retry by queue
        job.status, job.stage = JobStatus.NEEDS_REVIEW, "needs_review"
        self._save(job, gen)
        rep = outcome.report
        assert rep is not None
        self._audit(
            job,
            "job.completed",
            model=result.model_id,
            events=len(result.events),
            candidates=len(result.candidates),
            contradictions=len(result.contradictions),
            dropped=len(rep.dropped),
            citation_validity=round(rep.citation_validity, 4),
            model_calls=outcome.model_calls,
            tool_calls=outcome.tool_calls,
            prompt_tokens=outcome.trace_summary["tokens"]["prompt"],
            output_tokens=outcome.trace_summary["tokens"]["output"],
            seconds=round(seconds, 2),
        )

    def _fail(
        self, tenant_id: str, job_id: str, code: str, message: str, retryable: bool, attempt: int
    ) -> str:
        job, gen = self._load(tenant_id, job_id)
        if retryable and attempt < self.s.max_job_attempts:
            job.status, job.stage = JobStatus.QUEUED, f"retry_pending:{code}"
            self._save(job, gen)
            self._audit(job, "job.retry_scheduled", "retry", failure_code=code, attempt=attempt)
            raise RetryableJobError(code)
        job.status, job.stage, job.failure_code, job.failure_message = (
            JobStatus.FAILED,
            "failed",
            code,
            message[:300],
        )
        self._save(job, gen)

        def mutate(s: CaseState) -> CaseState:
            s.active_job_id, s.stage = None, CaseStage.EVIDENCE_COLLECTION
            return s

        try:
            self.cases.update(tenant_id, job.case_id, mutate)
        except AppError:
            log.error("could not reset case after job failure")
        self._audit(job, "job.failed", "failure", failure_code=code, attempt=attempt)
        return "done"
