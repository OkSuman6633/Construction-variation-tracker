# Build status (honest ledger)

VERIFIED = demonstrated by an automated check in this repo. BUILT = code exists, not exercised against the real service. NOT STARTED = absent.
Current checks: **75 tests passing; ruff and mypy clean (72 source files).**

## Built on Google ADK (Milestone 3) - mechanics VERIFIED, Gemini NOT run
| Item | Status | Evidence |
|---|---|---|
| Root coordinator + 5 specialised agents (own agent.py/prompt.py/__init__.py) | VERIFIED | tests/test_agent_contracts.py, test_adk_pipeline.py |
| Real ADK execution (Runner, SequentialAgent, LoopAgent, tools, state, callbacks) | VERIFIED with scripted model | 20 pipeline tests |
| All specified tool families registered; no dead tools; no scope/path params | VERIFIED | contract tests |
| Budgets, timeouts, stage guards fail closed | VERIFIED | runaway-loop / no-submit tests |
| Per-document context isolation; no cross-stage text replay | VERIFIED | isolation tests |
| Citation re-verification, invented-amount and legal-language rejection | VERIFIED | pipeline tests |
| Prompt-injection treated as data | VERIFIED (scripted) | multiple_variations test |
| Tracing without raw content | VERIFIED | trace test |
| Real Gemini call / model id / residency | NOT VERIFIED | scripts/check_model.py, scripts/run_profile.py ready |

## Ingestion and jobs (Milestones 2, 4, 6 - partial)
| Item | Status |
|---|---|
| Signed-URL request, local dev upload, registration with SHA-256, MIME sniff, size cap, duplicate detection | VERIFIED (local store) |
| Parsers: PDF, DOCX, XLSX, EML (images registered, not extracted) | VERIFIED (DOCX/EML/PDF in API tests; XLSX untested) |
| Scan gate (quarantine until scanned) | VERIFIED; no production scanner exists |
| Idempotent analyse, duplicate-active-job dedupe, replay-safe worker, retry/503 mapping | VERIFIED |
| Deterministic engine: chronology, duplicates, date/amount conflicts, missing references, candidate linking | VERIFIED |
| Foundation: auth, tenancy, CAS 409, idempotency, error contract | VERIFIED (Milestone 1) |
| GCS, BigQuery, Cloud Tasks, OIDC verifier, signed URLs | BUILT, unverified |

## Not started
Review PATCH + revision-conflict UI flow (M5), exports/PDF-DOCX reports (M5), React frontend, Terraform, GitHub Actions, pricing/entitlements/usage ledger (M8), 40-case/150-document corpus (have 4 profiles / 21 documents), golden-dataset evaluation, UAT-01..08 as executable scripts, BigQuery agent-event writer.

See KNOWN_LIMITATIONS.md for limits and decisions needed.
