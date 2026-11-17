# Implementation Assessment — Construction Variation Evidence Agent

Status: pre-implementation assessment, 30 Oct 2026. Sources: the four approval-draft documents dated 20 Oct 2026 (extracted copies in `docs/source_specs/`) and the master implementation prompt.

## 1. Existing repository and reuse
Greenfield: no repository, `pyproject.toml` or `package.json` existed. Nothing is reused. Verified toolchain: Python 3.12, Node 22, `google-adk` 2.13.0, FastAPI, Pydantic 2.14. Terraform/TFLint are not installed in the build sandbox, so IaC checks run in CI.

## 2. Requirements extracted from the four documents
**Doc 01 Product & Engineering.** 7-day MVP scope (journeys J1–J5); only GCS + BigQuery persist customer data; one `state.json` per case with `ifGenerationMatch`; 10 API endpoints; error shape `{code,message,correlation_id,retryable}`; idempotency key on every write; `expected_revision` on review PATCH; 5-step agent workflow; confidence is model-assessed and **uncalibrated**; gates: ≥95% citation validity, ≥90% explicit-date accuracy, 0 cross-tenant leaks, 100% human approval before external communication; 40 cases / 150 documents.
**Doc 02 DevOps.** Modules foundation/identity/storage/analytics/compute/queue/security/observability/delivery; dev/staging/prod in distinct projects; Cloud Run web, api, worker; no Cloud SQL/VM/always-on agent; GitHub OIDC WIF; digest-pinned promotion; SLO *proposals* (99.5%, p95 GET <1.5 s, p95 analysis <8 min); alerts (>2% failed jobs, queue age, spend anomaly, 429s, denials); verify model-endpoint geography before customer data.
**Doc 03 Pricing.** Admin-only versioned catalogue; snapshots at checkout; BigQuery usage ledger; counters from GCS entitlement state with CAS; idempotent webhook reconciliation; prices are hypotheses, not approved tariffs.
**Doc 04 BA/UAT.** UAT-01…08; Sev1 (cross-tenant leak, corrupted case, unsupported binding legal conclusion) blocks launch; Sev2 (wrong citation, lost correction, wrong dates, report omission) blocks pilot; thresholds (≥15 interviews, ≥2 pilot-ready orgs, ≥80% actionable statements) are *proposed gates*; demo is "Project Alpha Variation 014".

**Where the prompt extends the documents:** Doc 01 describes a single bounded agent; the prompt specifies a root coordinator plus five sub-agents. I implement the prompt but keep parsing, routing metadata and validation deterministic so LLM stage count does not erode the 8-minute p95 proposal or token budgets (a measured risk, §9).

## 3. Proposed architecture
Browser (React/Vite) → Cloud Run **web** + **api** (FastAPI) → signed upload to tenant-scoped GCS → Cloud Tasks → Cloud Run **worker** → ADK workflow (Gemini) → deterministic evidence engine → GCS versioned state (`ifGenerationMatch`) → BigQuery append-only audit/usage → review → GCS reports. Cloud access goes through narrow adapters (`ObjectStore`, `AuditSink`, `TaskQueue`, `BillingProvider`) with local implementations for dev/test (clearly labelled, refused in staging/production) and GCS/BigQuery/Cloud Tasks implementations for production.
**ADK sessions:** ADK's `DatabaseSessionService` (SQL) and `VertexAiSessionService` (Agent Engine storage) both violate the storage rule, so the worker uses `InMemorySessionService` and rebuilds minimal context from GCS on every job. ADK state is never authoritative.
**Tenant paths** are built only in `app/storage/paths.py` with strict ID validation; neither agents nor request bodies choose paths.

## 4. ADK agent hierarchy
`construction_variation_coordinator` (root, exposes `root_agent`) → sequential pipeline: **intake** → **document_extraction** (optional bounded per-document fan-out merged by Python) → **cross_reference** → **variation_analysis** → **review_preparation**. Each is an `LlmAgent` with its own `prompt.py`, schema-constrained output, restricted tools and peer/parent transfer disabled. Parsing, validation, chronology and persistence are plain Python outside agent authority; failed validation fails the job regardless of LLM output.

## 5. Backend/frontend integration
TanStack Query against `/v1`; bearer JWT + `X-Tenant-ID`; every write sends `Idempotency-Key`; review PATCH sends `expected_revision` and handles 409 with reload/rebase; job page polls `/v1/jobs/{id}` showing real stage names (no fabricated percentages); client types generated from OpenAPI.

## 6. Data contracts
GCS: `cases/{tenant}/{case}/state.json`, `uploads/{tenant}/{case}/{uuid}`, `reports/{tenant}/{case}/{version}.{pdf,docx}` + `manifest.json`; additions in the same bucket family: `projects/`, `tenants/{tenant}/members.json`, `users/{hash}/memberships.json`, `jobs/`, `idempotency/`, `entitlements/`, `pricing/`. BigQuery (partitioned on event timestamp): `audit_events`, `job_events`, `agent_events`, `usage_events`, `billing_reconciliation`, `model_executions`. No raw document text or raw prompts in logs or BigQuery.

## 7. Infrastructure
Terraform modules/envs exactly as Doc 02; separate projects per environment; Workload Identity Federation; Cloud Tasks retry/rate limits with DLQ strategy; budgets and alerts; digest-pinned promotion and traffic-split rollback.

## 8. Milestones
M1 Foundation → M2 Ingestion → M3 ADK agents → M4 Evidence engine → M5 Review/reporting → M6 Cloud integration → M7 Quality/release → M8 Commercial readiness. The Doc 01 §7 seven-day MVP mapping is the initial experimental target, tracked in `docs/STATUS.md`.

## 9. Risks and unresolved decisions
1. **Data residency (blocking for customers).** The prompt configures Google AI Studio (`GOOGLE_GENAI_USE_VERTEXAI=FALSE`); Doc 02 requires verifying model-endpoint geography before customer data is sent. AI Studio is for synthetic/dev data only; production should likely use Vertex AI in a UK/EU region. Decision needed.
2. **Model identifier** cannot be verified against the live Gemini API from the sandbox; a startup check is built, live verification is outstanding.
3. **Identity provider** unspecified. Provider-agnostic OIDC verification is built; the HS256 dev issuer is honoured only when `APP_ENV` is `local`/`test`.
4. **Malware scanning:** no managed GCP scanner; ClamAV job vs third-party undecided. Uploads stay quarantined until scanned.
5. **Scanned handwriting accuracy** is an open assumption (Doc 01 §9); Gemini multimodal OCR default, Document AI optional.
6. **Latency/cost** of five LLM stages vs the 8-minute p95 proposal: measure before any SLA.
7. **Open product assumptions** (Doc 01 §9): max file size, supported contract forms, retention, UK region, reviewer roles, branded reports — all configurable, defaults flagged.
8. **Billing provider** not selected: mock adapter only; mock mode never reports a real payment.
9. **Business validation** (interviews, willingness to pay, pilots) is human work; software only records it.
10. Cloud deployment, quality gates and UAT cannot be verified in the sandbox and stay marked *not verified* until evidenced.
