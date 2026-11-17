DevOps, Terraform & Production Release

Construction Variation Evidence Agent

EXPERIMENT 3 | CONSTRUCTION VARIATION EVIDENCE AGENT | APPROVAL DRAFT | 20 OCTOBER 2026

# 1. Mandate and platform boundaries

Provision and operate a staged, least-privilege, auditable environment for the Construction Variation Evidence Agent. Infrastructure is owned by DevOps; application and agent logic are owned by Developers. No Cloud SQL, PostgreSQL, unmanaged VM or always-on agent runtime.

Target stages: dev (rapid iteration and synthetic data), staging (production-like E2E), production (approved customers only). Distinct projects and storage; separate secrets and service accounts. No production customer data in development.

# 2. Terraform infrastructure modules

| Module | Resources and controls |
| --- | --- |
| foundation | Google APIs, labels, budgets and billing alert channels |
| identity | Per-service runtime service accounts, workload identity federation, scoped IAM |
| storage | Private GCS evidence/state/report buckets, retention, lifecycle, versioning, CORS signed uploads |
| analytics | BigQuery datasets/tables, partitioning by event timestamp, tenant filtering, IAM |
| compute | Cloud Run web, api, worker; scaling, concurrency, CPU/memory, execution service accounts |
| queue | Cloud Tasks queues, rate limits, retries, dead-letter handling strategy |
| security | Secret Manager, KMS where required, audit logs, optional Cloud Armor |
| observability | Alert policies, metrics, error budget dashboard, log exclusions/redaction |
| delivery | Artifact Registry, release service account, approvals and revision traffic split |

State backend: protected GCS Terraform state bucket per environment, with versioning, tightly scoped write permissions and serialised applies. Bootstrap state bucket through a reviewed one-time procedure, then manage under Terraform; no secrets in tfvars or plan logs.

# 3. Security architecture and incident controls

End users authenticated through configured identity provider; validate JWT audience and issuer server-side. IAM runtime accounts separated for API, worker and job execution. Signed URLs short-lived, content-type constrained and validated after upload. All bucket listings blocked from public access.

Threats: malicious attachments, prompt injection in contracts, accidental cross-tenant reads, replayed upload events, manipulated chronology, privilege escalation, leaked document contents in logs. Controls include upload scanner or quarantine gate, MIME sniffing, object checksums, tenant-prefixed resource authorisation, output schema validation and safe logging.

Configure regional resources according to documented data residency requirements. Verify model endpoint processing geography and data-handling terms before customer data is submitted. Sensitive or high-stakes personal records are excluded from pilot unless approved.

# 4. CI/CD and release contract

GitHub protected main branch, minimum one reviewer, required checks, CODEOWNERS for infra, dependency and secret scanning. GitHub OIDC Workload Identity Federation rather than long-lived service account JSON keys.

| Pipeline | Required jobs |
| --- | --- |
| pull request | ruff/format, mypy, pytest, API contract tests, IaC fmt/validate, tflint, security/dependency scans, Terraform plan |
| merge to main | Build digest-pinned containers, SBOM, integration tests and auto deploy DEV |
| release candidate | Deploy STAGING, execute browser E2E, cross-tenant isolation tests, extraction golden set and load smoke test |
| production promotion | Manual approval, immutable image digest, Terraform plan review, staged traffic shift, automated smoke and alert checks |
| rollback | Restore previous Cloud Run revision traffic and compatible configuration; do not rollback audit history |

Data schema and object format require versioned migrations; backwards compatibility mandatory across deployments. Protect state from simultaneous worker updates with GCS generation-match preconditions; duplicate Cloud Tasks deliveries cannot double-book actions.

# 5. Service levels, cost management and scaling

Pilot SLO proposals: 99.5% monthly availability for customer UI/API, p95 GET case < 1.5 s excluding cold starts, p95 async case analysis within 8 minutes for limited-size test packs. Calibrate with representative file size and queue demand; do not promise as SLA before measurement.

Set Cloud Run min instances 0 in dev and staging, consider 0 initially in production subject to latency; max instances and queue rate limits prevent invoice shock. Configure per-job token budget and customer quotas. Trigger alerts for daily spend anomalies, >2% failed jobs, queue age, access denials and excessive 429s.

Use BigQuery append-only event tables and GCS state snapshots. BigQuery costs scale with storage and queries; partition and set query cost ceilings. GCS active state is less convenient than OLTP but respects the user's data-store constraint.

# 6. Separate seven-day DevOps runbook

| Day | DevOps tasks | Exit evidence |
| --- | --- | --- |
| 1 | Org/project access, WIF, Terraform backend, budgets | terraform plan reviewed |
| 2 | Buckets, BigQuery, IAM, secret references | Policy test evidence |
| 3 | Cloud Run and Cloud Tasks, artifact registry | Synthetic async run |
| 4 | GitHub Actions and STAGING deployment | Green CI and deployed version |
| 5 | Dashboards, alerts, backups/retention, DR notes | Observability screenshot and restore test |
| 6 | Pen-test smoke, injection and tenant isolation support | Security gate |
| 7 | Release candidate, rollback drill, approval pack | No manual cloud drift and rollback proof |

Production readiness review: owner and on-call named, documented incidents and escalation, retention and deletion process, customer support escalation, RTO/RPO assessment, backup restore exercise, security testing, BA UAT sign-off, financial budget limit and explicit go/no-go record.

# 7. Deployment commands and environment configuration

Recommended Terraform workflow (illustrative; adapt to actual module layout): terraform -chdir=infra/envs/dev init; terraform -chdir=infra/envs/dev plan -out=tfplan; terraform -chdir=infra/envs/dev apply tfplan. Use CI for staging/production applies; no developer workstation production applies.

Publish containers to Artifact Registry through GitHub WIF, deploy immutable image digest to dev, then promote SAME digest to staging and production. Run migrations/object schema checks before traffic changes. After deployment run GET /healthz, auth/tenant negative tests, upload synthetic fixture and full case analysis through report export.

Reference documentation: https://cloud.google.com/docs/terraform ; https://docs.cloud.google.com/run/docs/ai-agents

Confidential working design | Customer data must not be used without authorisation | Page