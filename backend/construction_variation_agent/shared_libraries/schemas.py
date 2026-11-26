"""Strict shared data contracts (API, worker, ADK agents, report engine).

Provenance rule (Doc 01 section 6): every material factual item carries an
EvidenceReference (document_id + page/offset) or is explicitly user-authored.
Unsupported inference must become a ReviewerQuestion, not a fact.
Confidence is model-assessed and UNCALIBRATED until independently measured.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .constants import SCHEMA_VERSION
from .types import (
    CaseStage,
    ContractForm,
    DocumentCategory,
    EventType,
    EvidenceBasis,
    JobStatus,
    ParserStatus,
    ReviewStatus,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{2,62}$")]
ShortText = Annotated[str, StringConstraints(max_length=500, strip_whitespace=True)]
LongText = Annotated[str, StringConstraints(max_length=5000, strip_whitespace=True)]
Quote = Annotated[str, StringConstraints(max_length=1000)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0, description="Model-assessed, uncalibrated")]
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceReference(Strict):
    document_id: Identifier | None = None
    page_ref: ShortText | None = None
    offset_start: int | None = Field(default=None, ge=0)
    offset_end: int | None = Field(default=None, ge=0)
    quoted_span: Quote | None = None
    visual_region: ShortText | None = None
    user_authored: bool = False
    document_name: ShortText | None = None
    classification: ShortText | None = None

    @model_validator(mode="after")
    def _provenance(self) -> EvidenceReference:
        if self.user_authored:
            return self
        if not self.document_id:
            raise ValueError("evidence reference needs document_id or user_authored=true")
        if not (self.page_ref or self.offset_start is not None):
            raise ValueError("evidence reference needs a page_ref or offset")
        if (
            self.offset_start is not None
            and self.offset_end is not None
            and self.offset_end < self.offset_start
        ):
            raise ValueError("offset_end before offset_start")
        return self


class PartyRef(Strict):
    name: ShortText
    role: ShortText | None = None


class ContractInfo(Strict):
    form: ContractForm = ContractForm.UNKNOWN
    description: ShortText | None = None
    jurisdiction: ShortText | None = None


class ProjectCreate(Strict):
    name: ShortText = Field(min_length=1)
    reference: ShortText | None = None
    client: ShortText | None = None
    site_address: ShortText | None = None
    contract: ContractInfo = ContractInfo()


class Project(ProjectCreate):
    project_id: Identifier
    tenant_id: Identifier
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str
    updated_at: datetime = Field(default_factory=utcnow)


DEFAULT_CASE_OBJECTIVE = (
    "Reconcile and extract all variation events, scope changes, instructions, delays, quantities, "
    "and price consequences evidenced across all uploaded case documents into an audit-ready factual record."
)


class CaseCreate(Strict):
    project_id: Identifier
    title: ShortText = Field(min_length=1)
    alleged_change: LongText = Field(default=DEFAULT_CASE_OBJECTIVE)
    parties: list[PartyRef] = Field(default_factory=list, max_length=20)
    contract: ContractInfo = ContractInfo()
    key_dates: dict[ShortText, date] = Field(default_factory=dict, max_length=30)


class DocumentMetadata(Strict):
    document_id: Identifier
    tenant_id: Identifier
    case_id: Identifier
    filename: ShortText
    mime_type: ShortText
    size_bytes: int = Field(ge=0)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    recorded_event_date: date | None = None  # date ON the document, not upload time
    event_date_unknown: bool = False
    uploaded_at: datetime = Field(default_factory=utcnow)
    uploaded_by: str
    origin: ShortText | None = None
    page_count: int | None = Field(default=None, ge=0)
    parser_status: ParserStatus = ParserStatus.QUARANTINED
    category: DocumentCategory | None = None
    object_generation: int | None = None
    duplicate_of: Identifier | None = None
    retention_until: date | None = None


class TimelineEvent(Strict):
    event_id: Identifier
    event_type: EventType
    event_date: date | None = None
    description: LongText
    parties: list[ShortText] = Field(default_factory=list)
    references: list[ShortText] = Field(default_factory=list)  # e.g. instruction/quote refs
    amount: float | None = None
    currency: str | None = None
    document_id: Identifier | None = None
    page_ref: ShortText | None = None
    quoted_span: Quote | None = None
    visual_region: ShortText | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    basis: EvidenceBasis = EvidenceBasis.EXPLICITLY_EVIDENCED
    confidence: Confidence = 0.0
    uncertainties: list[ShortText] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW

    @model_validator(mode="after")
    def _checks(self) -> TimelineEvent:
        if self.currency is not None and not _CURRENCY.match(self.currency):
            raise ValueError("currency must be an ISO-4217 code")
        if self.amount is not None and self.currency is None:
            raise ValueError("amount requires currency")
        return self


class VariationCandidate(Strict):
    candidate_id: Identifier
    title: ShortText
    alleged_original_scope: LongText | None = None
    proposed_scope_delta: LongText | None = None
    related_instruction_refs: list[ShortText] = Field(default_factory=list)
    quantity_text: ShortText | None = None
    amount: float | None = None
    currency: str | None = None
    event_ids: list[Identifier] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    contradictions: list[Identifier] = Field(default_factory=list)
    missing_evidence: list[Identifier] = Field(default_factory=list)
    basis: EvidenceBasis = EvidenceBasis.AI_INFERRED
    confidence: Confidence = 0.0
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    # Always false: the product never asserts contractual entitlement (Doc 01 s6).
    entitlement_asserted: Literal[False] = False


class DocumentRelationship(Strict):
    relationship_id: Identifier
    document_ids: list[Identifier] = Field(min_length=2)
    relation: ShortText
    shared_references: list[ShortText] = Field(default_factory=list)
    rationale: LongText
    basis: EvidenceBasis = EvidenceBasis.AI_INFERRED
    confidence: Confidence = 0.0


class Contradiction(Strict):
    contradiction_id: Identifier
    kind: Literal["date", "amount", "reference", "scope", "other"]
    description: LongText
    evidence: list[EvidenceReference] = Field(min_length=1)
    detected_by: Literal["deterministic_rule", "agent"] = "deterministic_rule"


class MissingEvidence(Strict):
    missing_id: Identifier
    description: LongText
    expected_document_kind: DocumentCategory | None = None
    related_event_ids: list[Identifier] = Field(default_factory=list)


class ReviewerQuestion(Strict):
    question_id: Identifier
    question: LongText
    related_event_ids: list[Identifier] = Field(default_factory=list)
    related_candidate_ids: list[Identifier] = Field(default_factory=list)


class PlainEnglishSummary(Strict):
    text: Annotated[str, StringConstraints(min_length=1, max_length=600, strip_whitespace=True)]
    event_ids: list[Identifier] = Field(default_factory=list)
    candidate_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _brief(self) -> PlainEnglishSummary:
        if len(self.text.split()) > 70:
            raise ValueError("plain English summary must be 70 words or fewer")
        return self


# ------------------------------------------------------------- Upgraded Full Analysis Models (Part 3 & 4)
class DocumentRegisterItem(Strict):
    document_id: Identifier
    filename: ShortText
    category: ShortText
    document_date: str | None = None
    upload_date: str | None = None
    parsing_status: str = "parsed"
    page_count: int | None = None
    events_extracted_count: int = 0
    related_variations: list[Identifier] = Field(default_factory=list)
    source_availability: str = "ready_to_read"
    processing_errors: list[str] = Field(default_factory=list)


class CompleteChronologyEvent(Strict):
    event_id: Identifier
    event_date: str | None = None
    event_time: str | None = None
    event_type: ShortText
    description: LongText
    parties: list[ShortText] = Field(default_factory=list)
    document_id: Identifier
    document_name: ShortText
    page_ref: ShortText
    quoted_span: LongText | None = None
    visual_region: ShortText | None = None
    related_variation_id: Identifier | None = None
    evidence_classification: ShortText = "factual_record"
    verification_status: str = "verified_from_evidence"


class OriginalPosition(Strict):
    work_description: LongText | None = None
    quantity: float | None = None
    quantity_unit: ShortText | None = None
    specification: LongText | None = None
    quotation_ref: ShortText | None = None
    quoted_amount: float | None = None
    currency: str | None = None
    supporting_documents: list[EvidenceReference] = Field(default_factory=list)


class ChangedPosition(Strict):
    work_description: LongText | None = None
    added_quantity: float | None = None
    revised_total_quantity: float | None = None
    quantity_unit: ShortText | None = None
    specification_change: LongText | None = None
    instruction_ref: ShortText | None = None
    instruction_date: str | None = None
    revised_quotation_ref: ShortText | None = None
    revised_quoted_amount: float | None = None
    currency: str | None = None
    supporting_documents: list[EvidenceReference] = Field(default_factory=list)


class QuantityProgressPosition(Strict):
    item_description: ShortText
    original_quantity: float | None = None
    additional_requested: float | None = None
    total_proposed: float | None = None
    recorded_installed: float | None = None
    remaining_not_evidenced: float | None = None
    completion_evidence_status: ShortText = "Not evidenced"
    calculation_note: LongText | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class CommercialPosition(Strict):
    original_quoted_amount: float | None = None
    revised_quoted_amount: float | None = None
    calculated_difference: float | None = None
    difference_label: ShortText = (
        "Calculated difference between quoted totals — not an established payable variation amount"
    )
    currency: str | None = "GBP"
    quotation_references: list[ShortText] = Field(default_factory=list)
    commercial_acceptance_status: ShortText = "Commercial acceptance not established in reviewed evidence"
    payment_status: ShortText = "No payment or valuation records in reviewed documents"
    evidence: list[EvidenceReference] = Field(default_factory=list)


class UnresolvedItem(Strict):
    category: ShortText
    description: LongText
    reviewer_action: LongText
    related_event_ids: list[Identifier] = Field(default_factory=list)


class VariationPosition(Strict):
    variation_id: Identifier
    variation_title: ShortText
    project_name: ShortText
    alleged_change: LongText
    parties: list[ShortText] = Field(default_factory=list)
    instruction_refs: list[ShortText] = Field(default_factory=list)
    evidence_status: ShortText
    original_position: OriginalPosition = OriginalPosition()
    changed_position: ChangedPosition = ChangedPosition()
    quantity_progress: QuantityProgressPosition | None = None
    commercial_position: CommercialPosition | None = None
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)
    confidence: Confidence = 0.0


class ReviewItem(Strict):
    review_id: Identifier
    related_variation_id: Identifier | None = None
    category: Literal[
        "date_verification",
        "scope_verification",
        "quantity_verification",
        "commercial_verification",
        "instruction_verification",
        "work_completion_verification",
        "document_relationship_verification",
        "missing_evidence",
        "contradictory_evidence",
        "other",
    ] = "other"
    issue_title: ShortText
    description: LongText
    why_requires_verification: LongText
    supporting_documents: list[EvidenceReference] = Field(default_factory=list)
    suggested_question: LongText
    suggested_action: LongText
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    status: Literal[
        "pending_review",
        "in_progress",
        "confirmed",
        "corrected",
        "rejected",
        "more_info_required",
        "unresolved",
    ] = "pending_review"
    assigned_reviewer: str | None = None
    comments: list[ShortText] = Field(default_factory=list)
    decision: str | None = None
    decision_reason: str | None = None
    decision_timestamp: str | None = None


class AnalysisMetadata(Strict):
    engine_version: str = "2.0"
    model_id: str | None = None
    analysis_timestamp: datetime = Field(default_factory=utcnow)
    job_id: Identifier = "job-default"
    documents_uploaded: int = 0
    documents_analysed: int = 0
    events_count: int = 0
    variations_count: int = 0
    contradictions_count: int = 0
    review_items_count: int = 0
    missing_evidence_count: int = 0


class ReportReadiness(Strict):
    ready_for_review: bool = True
    blockers: list[ShortText] = Field(default_factory=list)
    advisory_notes: list[ShortText] = Field(default_factory=list)


class ReviewDecision(Strict):
    """Reviewer amendment. 'Accepted' only admits an item into the DRAFT dossier."""

    review_id: Identifier
    target_type: Literal["event", "candidate"]
    target_id: Identifier
    decision: ReviewStatus
    corrections: dict[str, str | float | None] = Field(default_factory=dict)
    reason: ShortText | None = None
    note: LongText | None = None
    reviewer_id: str
    decided_at: datetime = Field(default_factory=utcnow)
    based_on_revision: int

    @model_validator(mode="after")
    def _reason_required(self) -> ReviewDecision:
        if self.decision in (ReviewStatus.REJECTED, ReviewStatus.EDITED) and not self.reason:
            raise ValueError("a reason is required to reject or edit a finding")
        return self


class AnalysisResult(Strict):
    schema_version: str = SCHEMA_VERSION
    job_id: Identifier
    case_id: Identifier
    events: list[TimelineEvent] = Field(default_factory=list)
    candidates: list[VariationCandidate] = Field(default_factory=list)
    relationships: list[DocumentRelationship] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    reviewer_questions: list[ReviewerQuestion] = Field(default_factory=list)
    plain_english_summary: PlainEnglishSummary | None = None
    model_id: str | None = None
    # Enhanced Part 3 sections
    analysis_metadata: AnalysisMetadata | None = None
    case_information: dict[str, Any] = Field(default_factory=dict)
    document_register: list[DocumentRegisterItem] = Field(default_factory=list)
    executive_summary: dict[str, Any] = Field(default_factory=dict)
    variation_positions: list[VariationPosition] = Field(default_factory=list)
    original_scope: list[dict[str, Any]] = Field(default_factory=list)
    alleged_changes: list[dict[str, Any]] = Field(default_factory=list)
    extracted_facts: list[dict[str, Any]] = Field(default_factory=list)
    identified_events: list[TimelineEvent] = Field(default_factory=list)
    cross_document_relationships: list[DocumentRelationship] = Field(default_factory=list)
    complete_chronology: list[CompleteChronologyEvent] = Field(default_factory=list)
    commercial_analysis: list[CommercialPosition] = Field(default_factory=list)
    work_progress_analysis: list[QuantityProgressPosition] = Field(default_factory=list)
    duplicate_records: list[dict[str, Any]] = Field(default_factory=list)
    review_checklist: list[ReviewItem] = Field(default_factory=list)
    review_decisions: list[dict[str, Any]] = Field(default_factory=list)
    analysis_limitations: list[str] = Field(default_factory=list)
    report_readiness: ReportReadiness | None = None

    def to_full_dossier_dict(self) -> dict[str, Any]:
        """Output the complete, 20-section Part 3 master schema dictionary."""
        return {
            "analysis_metadata": self.analysis_metadata.model_dump(mode="json")
            if self.analysis_metadata
            else {},
            "case_information": self.case_information,
            "document_register": [d.model_dump(mode="json") for d in self.document_register],
            "executive_summary": self.executive_summary
            or ({"text": self.plain_english_summary.text} if self.plain_english_summary else {}),
            "variation_position": [v.model_dump(mode="json") for v in self.variation_positions],
            "original_scope": self.original_scope,
            "alleged_changes": self.alleged_changes,
            "extracted_facts": self.extracted_facts,
            "identified_events": [e.model_dump(mode="json") for e in (self.identified_events or self.events)],
            "cross_document_relationships": [r.model_dump(mode="json") for r in (self.cross_document_relationships or self.relationships)],
            "complete_chronology": [c.model_dump(mode="json") for c in self.complete_chronology],
            "commercial_analysis": [c.model_dump(mode="json") for c in self.commercial_analysis],
            "work_progress_analysis": [w.model_dump(mode="json") for w in self.work_progress_analysis],
            "contradictions": [c.model_dump(mode="json") for c in self.contradictions],
            "duplicate_records": self.duplicate_records,
            "missing_evidence": [m.model_dump(mode="json") for m in self.missing_evidence],
            "review_checklist": [r.model_dump(mode="json") for r in self.review_checklist],
            "review_decisions": self.review_decisions,
            "analysis_limitations": self.analysis_limitations,
            "report_readiness": self.report_readiness.model_dump(mode="json")
            if self.report_readiness
            else {},
        }


class ErrorResult(Strict):
    code: str
    message: str
    correlation_id: str
    retryable: bool = False


class CaseAnalysisRequest(Strict):
    """Authenticated, backend-built context. Agents must never invent these IDs."""

    tenant_id: Identifier
    case_id: Identifier
    job_id: Identifier
    correlation_id: str
    document_ids: list[Identifier] = Field(min_length=1, max_length=200)


class CaseState(Strict):
    schema_version: str = SCHEMA_VERSION
    case_id: Identifier
    tenant_id: Identifier
    project_id: Identifier
    revision: int = 1
    stage: CaseStage = CaseStage.DRAFT
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str
    title: ShortText
    alleged_change: LongText
    parties: list[PartyRef] = Field(default_factory=list)
    contract: ContractInfo = ContractInfo()
    key_dates: dict[ShortText, date] = Field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    evidence: list[DocumentMetadata] = Field(default_factory=list)
    events: list[TimelineEvent] = Field(default_factory=list)
    candidates: list[VariationCandidate] = Field(default_factory=list)
    relationships: list[DocumentRelationship] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    reviewer_questions: list[ReviewerQuestion] = Field(default_factory=list)
    plain_english_summary: PlainEnglishSummary | None = None
    reviews: list[ReviewDecision] = Field(default_factory=list)
    active_job_id: Identifier | None = None
    report_refs: list[str] = Field(default_factory=list)
    # Enhanced Part 3 & 4 fields
    analysis_metadata: AnalysisMetadata | None = None
    case_information: dict[str, Any] = Field(default_factory=dict)
    document_register: list[DocumentRegisterItem] = Field(default_factory=list)
    executive_summary: dict[str, Any] = Field(default_factory=dict)
    variation_positions: list[VariationPosition] = Field(default_factory=list)
    original_scope: list[dict[str, Any]] = Field(default_factory=list)
    alleged_changes: list[dict[str, Any]] = Field(default_factory=list)
    extracted_facts: list[dict[str, Any]] = Field(default_factory=list)
    complete_chronology: list[CompleteChronologyEvent] = Field(default_factory=list)
    commercial_analysis: list[CommercialPosition] = Field(default_factory=list)
    work_progress_analysis: list[QuantityProgressPosition] = Field(default_factory=list)
    duplicate_records: list[dict[str, Any]] = Field(default_factory=list)
    review_checklist: list[ReviewItem] = Field(default_factory=list)
    review_decisions: list[dict[str, Any]] = Field(default_factory=list)
    analysis_limitations: list[str] = Field(default_factory=list)
    report_readiness: ReportReadiness | None = None


class JobRecord(Strict):
    job_id: Identifier
    tenant_id: Identifier
    case_id: Identifier
    status: JobStatus = JobStatus.QUEUED
    stage: ShortText | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    requested_by: str
    correlation_id: str
    failure_code: str | None = None
    failure_message: str | None = None
    attempt: int = 0
