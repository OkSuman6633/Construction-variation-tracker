# Construction Variation Evidence Agent

Evidence-preparation SaaS for UK construction subcontractors/QS teams. It produces a **source-backed DRAFT chronology and dossier for human review**. It never determines entitlement, gives legal advice, issues notices or submits claims.

Specs: `docs/source_specs/` (four approval-draft documents). Plan and risks: `docs/00_IMPLEMENTATION_ASSESSMENT.md`. Honest progress ledger: `docs/STATUS.md`.

## Local development (backend)

Local mode uses email/password accounts and one SQLite database file for account,
organisation, project, case, evidence (BLOBs) and audit data. Set a private
`AUTH_DEV_HS256_SECRET` and a Gemini model/API key in `.env`; the model key stays on
the backend and is never sent to the browser.

```bash
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
if (-not (Test-Path .env) -and -not (Test-Path ..\.env)) { Copy-Item ..\.env.example .env }
# If .env already exists, do not overwrite it; verify APP_ENV=local,
# STORAGE_BACKEND=sqlite, AUDIT_BACKEND=sqlite and SCANNER_BACKEND=dev_noop.
# Set AUTH_DEV_HS256_SECRET (at least 32 random bytes) and GEMINI_API_KEY.
# For existing filesystem-backed data, migrate it before starting the API:
python -m scripts.migrate_local_to_sqlite
uvicorn app.main:create_app --factory --reload
```

The database is created at `backend\.local-data\cvea.db`. Local `dev_noop` scanner
mode does **not** scan for malware; use only with trusted files on a development machine.
Existing local objects and audit events are copied idempotently; the source files are retained.
Uploaded files are stored as SQLite BLOBs locally and use the same object-store interface as GCS
in deployment. When both `backend\.env` and the repository-root `.env` exist, the root `.env`
takes precedence.
Staging/production still require GCS, BigQuery, a real scanner and OIDC. Relative
`SQLITE_DATABASE_PATH` values are resolved from the backend directory, so the database location
does not change with the terminal's current directory. The backend logs the resolved SQLite
path at startup.

## Local development (frontend)
Start the backend as above, then in a second PowerShell terminal:
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173`, create an account, create an organisation, then add projects,
variation cases and documents. Organisation owners/admins can add teammates; a new account's
temporary password is shown once and must be changed at first sign-in. The UI supports
projects, cases, evidence upload, team access, analysis progress and source-backed results.
When an analysis reaches **Needs review**, generate a PDF report from the case page. It includes
the evidence-status pie chart, candidate confidence visualization, draft chronology, citations
and review checklist. Reports are versioned and stored with the case, and the latest report can
be downloaded again after restarting the backend.

For a cloud deployment, configure OIDC using `frontend/oidc.env.example`; local password accounts
are disabled outside local/test.

## ADK agent system
Real Google ADK 2.13 multi-agent pipeline (see `docs/ADK_ARCHITECTURE.md`): coordinator -> intake -> extraction loop -> cross-reference -> variation analysis -> review preparation -> plain-English report summary, followed by a deterministic validation engine. Agents interpret evidence; deterministic code verifies citations, dates and amounts, and controls persistence.
```bash
python -m scripts.check_model            # offline config check (add --live to call Gemini)
python -m scripts.run_profile synthetic_case_basic   # real Gemini on a SYNTHETIC profile
pytest                                   # includes the scripted-model ADK pipeline tests
```
To test the five-stage agent in ADK Web with directly uploaded files, start this separate,
temporary sandbox from `backend`:
```powershell
adk web --host 127.0.0.1 --port 8127 `
  --session_service_uri memory:// `
  --artifact_service_uri memory:// `
  --memory_service_uri memory:// construction_variation_agent
```
Open `http://127.0.0.1:8127`, select the coordinator, attach PDF, DOCX, XLSX, EML, JPEG or PNG
files, and send the message. Each invocation gets a random in-memory scope limited to those
attachments; it does not read organization/case data or save results to the Fieldnote SQLite
database. Uploads are limited to 25 MB per file, 100 MB total and 200 files. PDFs with pages
without extractable text are rejected so scanned content is not silently omitted. This local,
unauthenticated ADK Web server bypasses the app's malware scanner: keep it bound to localhost
and use only trusted/synthetic files. Its memory session/artifact services avoid persisting
uploads across a restart.

For persistent organization cases, authenticated uploads, malware scanning, deterministic final
validation and downloadable PDF reports, use the Fieldnote frontend/backend flow below. The
ADK CLI `run` and `api_server` entry points still do not create the required runtime.
API flow: `POST /v1/cases/{id}/upload-url` -> `PUT` upload -> `POST /v1/cases/{id}/evidence` -> `POST /v1/cases/{id}/analyse` (202) -> `GET /v1/jobs/{id}` -> `GET /v1/cases/{id}/timeline`.

## Storage rule
Local/test uses SQLite for persisted application objects, uploaded BLOBs, local accounts and audit
events. Staging/production continue to use GCS for authoritative state/files and BigQuery for
audit/usage. ADK's SQL and Vertex session services are deliberately not used.
