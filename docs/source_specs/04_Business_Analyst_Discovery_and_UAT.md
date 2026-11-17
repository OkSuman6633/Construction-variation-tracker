Business Analyst Discovery & UAT

Construction Variation Evidence Agent

EXPERIMENT 3 | CONSTRUCTION VARIATION EVIDENCE AGENT | APPROVAL DRAFT | 20 OCTOBER 2026

# 1. BA mission and what is being sold

Assess whether UK construction subcontractors, QS teams and commercial managers value a document-driven evidence reconstruction application. The goal is not to solicit general AI interest. Identify the exact situations where an instruction, changed scope, disputed amount or missing evidence creates real commercial work.

The BA team is separate from software development. It owns buyer discovery, process mapping, evidence taxonomy, demonstration narrative, UAT acceptance and quantified business case. It does not design Terraform, code the system or promise legal entitlement.

# 2. Recruitment and discovery approach

Recruit 15–20 conversations across electrical/mechanical subcontractors, fit-out, groundworks, small general contractors and QS consultancies; aim for firms with repeat change-order admin and a named commercial owner. Preserve GDPR-compliant sourcing and suppression. Record company size and likely project volume with evidence rather than assumptions.

Opening approach: 'We are testing a tool that creates a source-linked chronology from variation instructions, emails, site records and quotations. We are not asking you to buy AI; we want to understand the evidence-preparation bottlenecks and whether the output would genuinely help your QS or contracts team.'

## Discovery questions

Describe the last change or disputed instruction that required a variation evidence pack. What triggered it?

Which documents and systems were involved, who assembled them, and how many hours elapsed?

What were the contractual notification and internal approval checkpoints? Which were missed or disputed?

Where were dates, scope, amount, authorship or source references inconsistent?

How often do cases arise per month/quarter, and what fraction are complex?

How are draft dossiers reviewed, issued and archived today?

What is the cost of document chasing, commercial review and rework?

Which artefacts could safely be used for a privacy-approved test?

How would you assess an acceptable output, and what would cause you not to trust it?

Would you pay per case, per user or for a managed service? Who signs off payment?

# 3. Evidence to capture and use-case segmentation

| Evidence type | BA must capture |
| --- | --- |
| Change instruction | Date, sender, reference, requested scope delta, acceptance status |
| Email / correspondence | Thread, parties, date, attachments and contradictions |
| Site diary / photo | Recorded date, location, authorship and uncertainty |
| Quote / cost build-up | Version, lines, assumptions, exclusions and author |
| Programme milestone | Baseline date, revised date, supporting document; no inferred critical-path effect |
| Review record | QS judgement, corrections, reason and whether evidence was sufficient |

Obtain consented, redacted copies or synthetic equivalents. Record data handling constraints. Do not request confidential live claims material over personal email or unsecured file sharing.

# 4. BA-led end-user journeys and demo

| Journey | Demo story | Evidence of success |
| --- | --- | --- |
| Create | QS starts Project Alpha Variation 014 | Case identity and controls correct |
| Upload | 6 documents with conflicting dates and revisions | All sources registered, duplicates recognised |
| Analyse | System extracts instruction and builds chronology | Events source-linked and unknowns explicit |
| Review | QS changes one date and rejects weak candidate | Correction persists with author/reason |
| Export | Draft evidence pack generated | Citations work, disputed fields labelled |
| Return | User revisits case after logout/login | No leakage, state preserved |

Five-minute presentation: 45 seconds problem; 45 seconds sample evidence; 90 seconds source-linked chronology; 60 seconds correction/review; 45 seconds draft report; 15 seconds feedback request. Do not suggest the tool validates contractual liability.

# 5. Value calculations and commercial test

Record measured historical time to collect sources, build timeline, cross-check references and write a dossier. Compare with supervised tool run including human corrections. Value = verified hours avoided x fully loaded staff rate; label as capacity released, not automatic cash saving.

Scenario only: 8 variation evidence packs/month, 5 current hours each, 2 assisted hours, £45 fully loaded cost/hour gives 24 hours capacity released or £1,080 capacity value/month; deduct software fees, review and error costs. Never claim actual ROI until observed.

For each interview record: pain intensity, volume, workflow repeatability, procurement path, budget owner, data-sharing feasibility, urgency, willingness to pay, objections and follow-up date. Separate 'interested' from signed pilot.

# 6. UAT scripts and defect triage

UAT-01 uploaded document yields correctly sourced chronology. UAT-02 documents with inconsistent dates expose conflict. UAT-03 unsupported change is clearly marked unknown. UAT-04 rejected candidate disappears from approved export but remains in audit. UAT-05 two reviewers editing stale revisions receive conflict. UAT-06 unauthorized tenant cannot access a case or signed URL. UAT-07 repeated analyse clicks do not duplicate jobs/billing. UAT-08 report prominently states draft, human-review required.

Severity 1: cross-tenant leak, corrupted case or unsupported binding legal conclusion; blocks launch. Severity 2: wrong source citation, lost correction, wrong dates or report omission; blocks customer pilot. Severity 3: cosmetic or non-blocking UX issue; track and fix. BA signs off journey and domain accuracy; security lead signs off controls.

# 7. BA presentation to sponsor and go/no-go

Deliver a 15-minute sponsor review: customer segments tested; 3 evidence-backed recent variation cases; current process map; pains ranked by measured cost/frequency; application demo observations; five most important product requirements; accuracy limitations; willingness-to-pay evidence; objections; recommended pivot/continue decision.

Acceptance threshold proposals: >=15 substantial interviews, >=2 pilot-ready organisations, at least one consented example or independently created gold-standard test case reviewed by QS, >=80% of high-priority output statements deemed actionable with source evidence, no critical unresolved UAT defects. Thresholds are proposed gates, not prior results.

Do not begin contractual or legal claim correspondence automatically. Contract-form-specific interpretations should be validated by qualified UK professionals before the product offers guidance beyond evidence assembly.

Confidential working design | Customer data must not be used without authorisation | Page