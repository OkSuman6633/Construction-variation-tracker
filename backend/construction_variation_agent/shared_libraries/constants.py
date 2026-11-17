"""Constants shared by the API, worker and ADK agents."""

SCHEMA_VERSION = "1.0"

ALLOWED_UPLOAD_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "message/rfc822",
    "image/jpeg",
    "image/png",
}

# Default only. Doc 01 lists max file size as an assumption to confirm; the real
# limit comes from the plan entitlement, never from this constant alone.
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024

DISCLAIMER_DRAFT = (
    "DRAFT evidence pack prepared with AI assistance. It is NOT legal advice, NOT a "
    "determination of contractual entitlement and NOT a notice or claim. Every finding "
    "requires review by a qualified professional. Contractual notification deadlines "
    "and entitlement remain the responsibility of the reviewing professional."
)

# ---- ADK session-state keys. Scope keys are written ONLY by the backend. ----
STATE_TENANT = "tenant_id"
STATE_CASE = "case_id"
STATE_JOB = "job_id"
STATE_CORRELATION = "correlation_id"
STATE_DOCUMENTS = "document_ids"
STATE_CASE_CONTEXT = "case_context"  # user-authored case facts supplied by the backend
STATE_INTAKE = "intake_result"
STATE_EXTRACTIONS = "extractions"  # {document_id: [event dicts]}
STATE_EXTRACT_CURSOR = "extraction_cursor"
STATE_EXTRACT_DONE = "extraction_done"
STATE_EXTRACT_SEQ = "extraction_event_seq"
STATE_CROSSREF = "cross_reference_result"
STATE_VARIATION = "variation_result"
STATE_REVIEW_PREP = "review_prep_result"
STATE_PLAIN_ENGLISH_SUMMARY = "plain_english_summary"

STAGES = (
    "intake",
    "document_extraction",
    "cross_reference",
    "variation_analysis",
    "review_preparation",
    "plain_english_report",
)

# Hard per-job budgets (fail closed). Overridable through JobLimits.
MAX_MODEL_CALLS = 400
MAX_TOOL_CALLS = 800
MAX_PAGES_PER_CALL = 4
MAX_CHARS_PER_PAGE = 6000
JOB_TIMEOUT_SECONDS = 900

GENERATE_TEMPERATURE = 0.0
