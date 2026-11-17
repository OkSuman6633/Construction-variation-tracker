"""Enumerations used by the shared schemas."""

from enum import StrEnum


class DocumentCategory(StrEnum):
    INSTRUCTION = "instruction"
    CORRESPONDENCE = "correspondence"
    SITE_DIARY = "site_diary"
    QUOTATION = "quotation"
    COST_BREAKDOWN = "cost_breakdown"
    PROGRAMME = "programme"
    PHOTOGRAPH = "photograph"
    REVISED_DRAWING = "revised_drawing"
    CONTRACT_RECORD = "contract_record"
    OTHER = "other"


class EventType(StrEnum):
    INSTRUCTION_ISSUED = "instruction_issued"
    SCOPE_CHANGE_REQUEST = "scope_change_request"
    QUANTITY_REVISION = "quantity_revision"
    QUOTATION_ISSUED = "quotation_issued"
    QUOTATION_REVISED = "quotation_revised"
    PROGRAMME_DATE_CHANGE = "programme_date_change"
    SITE_RECORD = "site_record"
    CORRESPONDENCE = "correspondence"
    OTHER = "other"


class EvidenceBasis(StrEnum):
    """How strongly a statement is supported. These states must never be merged."""

    EXPLICITLY_EVIDENCED = "explicitly_evidenced"
    AI_INFERRED = "ai_inferred_relationship"
    CONTRADICTED = "contradicted"
    MISSING = "missing_information"
    UNKNOWN = "unknown"
    USER_AUTHORED = "user_authored"


class ReviewStatus(StrEnum):
    """Deliberately no 'professionally_approved' state exists for AI output."""

    NEEDS_REVIEW = "needs_review"
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"


class CaseStage(StrEnum):
    DRAFT = "draft"
    EVIDENCE_COLLECTION = "evidence_collection"
    ANALYSING = "analysing"
    NEEDS_REVIEW = "needs_review"
    REVIEW_COMPLETE = "review_complete"
    EXPORT_READY = "export_ready"


class JobStatus(StrEnum):
    QUEUED = "queued"
    WORKING = "working"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class ParserStatus(StrEnum):
    PENDING = "pending"
    QUARANTINED = "quarantined"
    PARSED = "parsed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    BLOCKED = "blocked"  # scanner reported malware


class ContractForm(StrEnum):
    JCT = "jct"
    NEC = "nec"
    BESPOKE = "bespoke"
    UNKNOWN = "unknown"
