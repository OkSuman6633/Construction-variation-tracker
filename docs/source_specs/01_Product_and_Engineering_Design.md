Product & Engineering Design

Construction Variation Evidence Agent

EXPERIMENT 3 | CONSTRUCTION VARIATION EVIDENCE AGENT | APPROVAL DRAFT | 20 OCTOBER 2026

# 1. Product mission and intended outcome

Build a secure, evidence-led application for UK construction subcontractors and commercial teams to assemble the factual record behind possible variation claims. A variation may arise from changed scope, instructions, delays, disruption or price consequences; the product organises evidence and produces a reviewable chronology, not an automatic contractual entitlement or legal determination.

Why: evidence is dispersed among instructions, emails, site diaries, images, quotation revisions and programme documents. The result is time-consuming manual reconstruction, missing context and avoidable commercial disputes. The application makes source-backed preparation faster and traceable.

MVP persona: subcontractor commercial manager, quantity surveyor, contracts administrator and approved external reviewer. Buyer and legal-account roles are distinct. No independent legal advice, adjudication, contract notification or final claim submission is performed by the agent.

# 2. Exact MVP scope, exclusions and customer experience

| Included in 7-day MVP | Explicitly out of scope |
| --- | --- |
| Secure case creation, file upload, metadata and source preview | Direct email/WhatsApp/ERP integrations |
| Evidence classification and dated chronology | Full programme critical-path analysis |
| Instruction/change references and amount extraction | Automated legal or contractual entitlement determination |
| User-edited event chronology and issue notes | Autonomous contract notices or claims submissions |
| Uncertainty flags, citations, PDF/DOCX evidence dossier | Certified quantity surveying / legal conclusions |
| Review, approval and report export | Complex multi-party claims negotiations |

User journey J1: sign in, choose tenant, create project/case, record parties, project identifiers, governing form if known (JCT/NEC/bespoke), jurisdiction, key dates and the alleged change.

J2: upload PDF, DOCX, XLSX, EML (where parsable), JPG/PNG; attach author/date/source type; mark unknown data. Enforce file-size and malware-scan policy; no implied authenticity verification.

J3: request analysis; display queued/working/completed/needs-review/failed; users can close browser and return. System never treats LLM claims as facts without source references.

J4: inspect event timeline and variation candidates; click to underlying page/paragraph/image; correct document dates, links, amount and confidence; record reviewer identity and decision.

J5: export labelled DRAFT evidence pack, chronology, issue list, assumptions, exceptions, source register and completeness checklist. Explicitly warn that contractual notification deadlines and entitlement remain professional review responsibilities.

# 3. Reference-aligned architecture and key flows

Adapted from Google Cloud's single-agent ADK and Cloud Run architecture, with a bounded extraction agent, deterministic validators and review controls. Source: https://docs.cloud.google.com/architecture/single-agent-ai-system-adk-cloud-run.

Browser -> HTTPS Cloud Run web/API -> authorised signed upload to tenant-scoped GCS -> Cloud Tasks -> Cloud Run worker -> Gemini/optional Document AI -> JSON schemas -> deterministic evidence engine -> GCS versioned case state -> BigQuery append-only event ledger -> review -> report object in GCS.

Only Cloud Storage and BigQuery persist customer data. BigQuery is NOT used for high-frequency transactional mutations. Each active case state is one GCS JSON object updated using ifGenerationMatch optimistic concurrency. Event ingestion to BigQuery is asynchronous and query freshness must not be treated as state authority. Cloud Tasks provides at-least-once delivery; job steps require idempotency keys and deduplication.

# 4. Storage contract and information model

| Object | Storage and essential fields |
| --- | --- |
| Case manifest | GCS cases/{tenant}/{case}/state.json: case_id, tenant_id, revision, stage, timestamps, review_status, artifact_refs |
| Evidence source | GCS uploads/{tenant}/{case}/{uuid}: SHA256, filename, mime, recorded_event_date, received_at, origin, page count |
| Extracted event | GCS event ledger plus BigQuery: event_id, date, event_type, parties, document_id, page_ref, quoted_span, confidence |
| Variation candidate | candidate_id, alleged instruction, proposed scope delta, amount/currency if present, evidence_refs, contradictions, gaps |
| Audit event | BigQuery append-only: correlation_id, action, actor_id, tenant_id, object_version, outcome, model_version, timestamp |
| Export | GCS reports/{tenant}/{case}/{version}.pdf/docx, manifest, report_hash, reviewer_status |

Never store raw customer document contents in ordinary logs or unscoped model prompts. Enforce retention and erasure, subject to legal holds explicitly agreed with customers. GCS state update conflicts return HTTP 409 with a reload or rebase option.

# 5. API endpoints and error contract

| Method/path | Purpose / outcome |
| --- | --- |
| POST /v1/cases | Create case, 201 + case_id |
| GET /v1/cases/{id} | Return tenant-authorised case state |
| POST /v1/cases/{id}/upload-url | Short-lived restricted signed URL, checksum, media limits |
| POST /v1/cases/{id}/evidence | Register verified uploaded object metadata |
| POST /v1/cases/{id}/analyse | 202 job_id; reject duplicate active request safely |
| GET /v1/jobs/{id} | Stage, progress, failures and trace ID |
| GET /v1/cases/{id}/timeline | Evidence-linked events, candidates, unknowns |
| PATCH /v1/cases/{id}/reviews/{review_id} | Reviewer amendments, expected_revision required |
| POST /v1/cases/{id}/exports | Generate draft pack, 202 job_id |
| GET /v1/cases/{id}/exports | Retrieve authorised signed download URL |

API security: every route verifies authenticated identity, tenant membership and case ownership. Every write accepts idempotency key; errors use {code,message,correlation_id,retryable}. Never expose bucket paths as proof of authority.

# 6. Agent and validation workflow

Step 1 classify file type, extract text/layout, identify document date versus upload timestamp, and preserve page/section coordinates. Step 2 infer probable event candidates and cross-document references. Step 3 normalize dates, document identifiers, parties and currency, preserving uncertainty. Step 4 run deterministic rules for chronology, duplicates, apparent contradictions and missing references. Step 5 assemble source-backed candidate summary with reviewer prompts.

Strict schemas: every factual item needs document_id + page/offset or user-authored provenance; unsupported inference becomes a question, not a fact. Define confidence as model-assessed and uncalibrated until independently measured. Do not extrapolate entitlement, delay quantum or causation.

Adversarial controls: treat embedded document instructions as untrusted content; reject prompt injection, cross-tenant references, malicious file types, duplicate task invocation and forged confidence. Enforce token, cost and tool-step budgets.

# 7. Engineering ownership and 7-day plan

| Day | Developers' deliverable | Proof of completion |
| --- | --- | --- |
| 1 | Repo, types, auth, project/case UI | Local tests and signed-in create case |
| 2 | Uploads, SHA256, MIME controls, GCS state CAS | Tenant isolation tests |
| 3 | Cloud Tasks job lifecycle and extraction adapters | Synthetic document processing |
| 4 | Event normalisation, timeline, evidence linking | Golden fixture comparison |
| 5 | Reviewer corrections and export generation | Review and draft pack demo |
| 6 | E2E, security and adversarial regression | Acceptance and negative test evidence |
| 7 | Staging walkthrough, fixed defects, release candidate | Signed QA checklist and demo URL |

Development team owns application code, schemas, extraction prompt/evaluation fixtures, unit/contract tests, dependency pinning and API documentation. DevOps owns Terraform, cloud identities, infrastructure CI/CD and production deployment; no hidden manual provisioning.

# 8. Acceptance, quality gates and known limitations

MVP target: on held-out labelled data, at least 95% event-to-source citation validity, at least 90% event-date extraction accuracy where dates are explicit, zero cross-tenant data leaks, and 100% human approval before any final external communication. Measure false positives and abstentions separately.

Test minimum: 40 synthetic cases spanning 150 documents, including conflicting dates, multiple amendments, handwritten scanned note, photographs with uncertain timestamps, missing instruction, repeat-upload and irrelevant documents. Targets are gates to prove, not existing results.

Reviewer must be able to trace every material assertion to the uploaded evidence. Unknown or contradictory items must be labelled. No automatic submission, advice or financial recovery claims. Production deployment requires explicit security and BA sign-off.

# 9. References and open assumptions

Google Cloud ADK reference: https://docs.cloud.google.com/architecture/single-agent-ai-system-adk-cloud-run

Cloud Run workload types: https://docs.cloud.google.com/run/docs/ai-agents

Assumptions to confirm: max file size, supported contract forms, customer evidence retention, UK region requirements, acceptable scanned-image accuracy, reviewer roles and whether reports require client-branded layout.

Confidential working design | Customer data must not be used without authorisation | Page