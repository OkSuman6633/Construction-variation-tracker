Business Model & Configurable Pricing

Construction Variation Evidence Agent

EXPERIMENT 3 | CONSTRUCTION VARIATION EVIDENCE AGENT | APPROVAL DRAFT | 20 OCTOBER 2026

# 1. Commercial product definition

Sell a supervised construction variation evidence-preparation service. Customer uploads an existing project evidence pack; the application produces a traceable draft chronology, referenced candidate changes, unresolved contradictions and a reviewer-ready dossier. It does not certify contractual entitlement, quantify disruption claims or replace a quantity surveyor or solicitor.

Primary market hypothesis: UK subcontractors and specialist contractors regularly handling variation evidence. Secondary: QS consultancies and commercial administration teams. Validate frequency, number of packs, typical preparation time and whether customers prefer per-case payments or recurring team access.

# 2. Configurable pricing hypotheses, not approved tariffs

| Tier | Proposed price | Included example limits | Buyer |
| --- | --- | --- | --- |
| Trial | £0 / invite only | 1 synthetic/sample case, 10 documents, no customer legal export | Evaluate |
| Case | £299 per case | 1 project variation case, 50 documents, 1 reviewer | Occasional user |
| Team | £499 per month | 5 cases/month, 3 seats, 500 documents pooled | Small contractor |
| Professional | £1,299 per month | 20 cases/month, 10 seats, priority review controls | QS / commercial teams |
| Enterprise | Quoted | Custom usage, SSO, contractual security controls | Multi-project operators |

Commercial figures are initial test prices, not market-validated. Billing currency GBP, VAT treatment configurable; do not assume mandatory VAT registration threshold or legal applicability without accountant advice. Test actual willingness to pay before production rollout.

# 3. Product-controlled configuration and entitlements

Admin-only versioned plan catalogue: plan_id, effective_from/to, currency, unit_price, billable_event, case_allowance, document_allowance, seat_count, max_file_size, export_enabled, manual_review_limit, overage_policy and VAT treatment field. Never rely on frontend gating only.

Pricing snapshots captured at checkout; no retroactive repricing. Usage ledger event_id + tenant + case + billable_unit + timestamp + source_hash stored in BigQuery; near-real-time counters calculated from GCS account entitlement state with optimistic concurrency. Billing integration must be idempotent and reconcile to provider webhooks and the ledger.

Administrative operations: create draft tariff, review, activate future date, retire, preview customer entitlement, manual adjustment with approval and complete audit trail. Pricing changes must not require redeploying code.

# 4. Cost-to-serve and unit-economic sensitivity

Example per case (assumption only): model/document processing £6–£35, storage/compute £2–£10, support and QA £15–£80, payment fee and disputed outputs variable. At £299 per case, direct contribution could vary materially based on file volume and reviewer effort; do not treat token-only cost as gross margin.

Scenario: 10 paying cases/month at £299 yields £2,990 monthly revenue before delivery, sales, fixed engineering, insurance, refunds and taxes. 5 Team subscriptions at £499 yields £2,495 MRR, but retention is unproven.

Measure CAC per qualified paid account, trial-to-paid, case frequency, cost per accepted export, median manual review minutes, refund rate, churn, gross margin after delivery labour and percentage of customers using 2+ cases.

# 5. Market validation and decision gates

Interview at least 15 relevant firms across specialist contractors, QS practices and commercial managers; test actual current method and samples, timing and costs. Target at least 2 paid pilot commitments or equivalent signed purchase intents before scaling spend.

A go decision requires: agreed scope and disclaimers, repeated workflows, a buyer who owns budget, successful expert review of reports, defensible contribution model and a viable route to acquire customers. Pivot to managed evidence-preparation services if self-service adoption or subscription frequency is weak.

Principal risks: low trust in AI evidence, confidentiality, errors in chronology, contract-form differences, sparse source data, one-off demand and overreliance on price-sensitive subcontractors. Contracts and liability limits need qualified UK solicitor review; tax and VAT by accountant.

# 6. Approval matrix

| Decision | Owner | Approval evidence |
| --- | --- | --- |
| Tier names and pilot prices | Product/Commercial | Interview findings and actual quotations |
| Billing/usage implementation | Engineering | Tested reconciliation, idempotency and refunds |
| Terms, disclaimers, processing agreement | Legal / sponsor | Reviewed signed templates |
| Production acquisition spend | Sponsor | Paid pilot signal and measurable CAC ceiling |

Confidential working design | Customer data must not be used without authorisation | Page