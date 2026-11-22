from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.config import Settings
from app.errors import AppError
from app.main import create_app, resolve_local_path
from app.services.cases import CaseService, ProjectService
from app.services.reports import ReportService
from app.storage.sqlite import SQLiteObjectStore
from construction_variation_agent.shared_libraries.schemas import (
    CaseCreate,
    DocumentMetadata,
    EvidenceReference,
    MissingEvidence,
    PlainEnglishSummary,
    ProjectCreate,
    ReviewerQuestion,
    TimelineEvent,
    VariationCandidate,
)
from construction_variation_agent.shared_libraries.types import (
    CaseStage,
    EventType,
    ParserStatus,
)
from tests.helpers import case_body, headers


def _completed_case(store, tenant_id="tenant-a"):
    projects = ProjectService(store)
    cases = CaseService(store, projects)
    project = projects.create(
        tenant_id,
        "user-a",
        ProjectCreate(name="Synthetic report project", reference="TEST-REPORT"),
    )
    case = cases.create(
        tenant_id,
        "user-a",
        CaseCreate.model_validate(case_body(project.project_id)),
    )
    document_id = "doc-report-001"
    reference = EvidenceReference(
        document_id=document_id,
        page_ref="page_1",
        quoted_span="Install 8 additional metres of drainage.",
    )
    parsed_document = DocumentMetadata(
        document_id=document_id,
        tenant_id=tenant_id,
        case_id=case.case_id,
        filename="synthetic-instruction.pdf",
        mime_type="application/pdf",
        size_bytes=1234,
        sha256="a" * 64,
        uploaded_by="user-a",
        page_count=1,
        parser_status=ParserStatus.PARSED,
    )
    quarantined_document = DocumentMetadata(
        document_id="doc-report-002",
        tenant_id=tenant_id,
        case_id=case.case_id,
        filename="unscanned-costs.pdf",
        mime_type="application/pdf",
        size_bytes=567,
        sha256="b" * 64,
        uploaded_by="user-a",
        parser_status=ParserStatus.QUARANTINED,
    )
    event = TimelineEvent(
        event_id="evt-report-001",
        event_type=EventType.INSTRUCTION_ISSUED,
        event_date=date(2026, 11, 12),
        description="An instruction directs additional drainage installation.",
        document_id=document_id,
        page_ref="page_1",
        quoted_span=reference.quoted_span,
        evidence=[reference],
        confidence=0.95,
    )
    candidate = VariationCandidate(
        candidate_id="cand-report-001",
        title="Additional drainage",
        proposed_scope_delta="Install 8 additional metres of drainage.",
        related_instruction_refs=["TEST-REPORT"],
        evidence=[reference],
        confidence=0.85,
    )
    missing = MissingEvidence(
        missing_id="missing-report-001",
        description="The original scope drawing is not included.",
    )

    def complete(state):
        state.stage = CaseStage.NEEDS_REVIEW
        state.evidence.extend([parsed_document, quarantined_document])
        state.events.append(event)
        state.candidates.append(candidate)
        state.missing_evidence.append(missing)
        return state

    completed = cases.update(tenant_id, case.case_id, complete)
    return projects, cases, completed


def test_relative_local_paths_are_independent_of_launch_directory(tmp_path, monkeypatch):
    from app.main import BACKEND_ROOT

    monkeypatch.chdir(tmp_path)
    assert (
        resolve_local_path(".local-data/cvea.db")
        == (BACKEND_ROOT / ".local-data" / "cvea.db").resolve()
    )
    absolute = tmp_path / "custom.db"
    assert resolve_local_path(absolute) == absolute.resolve()


def test_settings_env_files_are_anchored_to_backend_and_repository():
    from app.config import BACKEND_ROOT as SETTINGS_BACKEND_ROOT
    from app.config import REPOSITORY_ROOT

    assert Settings.model_config["env_file"] == (
        SETTINGS_BACKEND_ROOT / ".env",
        REPOSITORY_ROOT / ".env",
    )


def test_case_analysis_and_pdf_survive_sqlite_app_recreation(tmp_path, monkeypatch):
    backend_root = tmp_path / "backend"
    first_launch = tmp_path / "first-launch"
    second_launch = tmp_path / "second-launch"
    backend_root.mkdir()
    first_launch.mkdir()
    second_launch.mkdir()
    monkeypatch.setattr("app.main.BACKEND_ROOT", backend_root)
    settings = Settings(
        app_env="test",
        storage_backend="sqlite",
        audit_backend="sqlite",
        local_data_dir=".local-data",
        sqlite_database_path=None,
    )
    monkeypatch.chdir(first_launch)
    first_app = create_app(settings)
    first = first_app.state.container
    _, _, case = _completed_case(first.store)
    expected_database = backend_root / ".local-data" / "cvea.db"
    assert first.store.path == expected_database

    first_report = first.reports.generate("tenant-a", case.case_id)
    assert first_report.version == "v1"
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(first_report.data)).pages
    )
    assert len(PdfReader(BytesIO(first_report.data)).pages) >= 2
    assert "FIELDNOTE" in pdf_text
    assert "Additional drainage" in pdf_text
    assert "VARIATION POSITION" in pdf_text
    assert "CHRONOLOGY" in pdf_text

    docx_report = first.reports.latest("tenant-a", case.case_id, format="docx")
    assert docx_report.filename.endswith(".docx")
    assert len(docx_report.data) > 1000

    monkeypatch.chdir(second_launch)
    second_app = create_app(settings)
    second = second_app.state.container
    assert second.store.path == expected_database
    assert [item.project_id for item in second.projects.list("tenant-a")] == [case.project_id]
    persisted_case = second.cases.get("tenant-a", case.case_id)
    assert persisted_case.stage == CaseStage.NEEDS_REVIEW
    assert persisted_case.events[0].description == case.events[0].description
    assert persisted_case.candidates[0].title == "Additional drainage"
    assert persisted_case.report_refs == ["v1"]
    assert [item.case_id for item in second.cases.list("tenant-a")] == [case.case_id]
    latest = second.reports.latest("tenant-a", case.case_id)
    assert latest.data == first_report.data


def test_report_includes_all_events_and_findings_without_arbitrary_caps(tmp_path):
    store = SQLiteObjectStore(tmp_path / "large-case.db")
    projects, cases, case = _completed_case(store)

    def add_many_findings(state):
        state.events = [
            state.events[0].model_copy(
                update={
                    "event_id": f"evt-{index:04d}",
                    "description": "Additional drainage work was recorded in the site records. " * 3,
                }
            )
            for index in range(1, 15)
        ]
        state.candidates = [
            state.candidates[0].model_copy(
                update={
                    "candidate_id": f"cand-{index:04d}",
                    "title": f"Possible change {index}",
                    "proposed_scope_delta": "Additional work is described in the records. " * 3,
                }
            )
            for index in range(1, 6)
        ]
        state.reviewer_questions = [
            ReviewerQuestion(
                question_id=f"qst-{index:04d}",
                question="Please check the source files and confirm this detail. " * 3,
            )
            for index in range(1, 10)
        ]
        state.plain_english_summary = PlainEnglishSummary(
            text="The files describe possible changes. A person should check the sources and questions."
        )
        return state

    large_case = cases.update("tenant-a", case.case_id, add_many_findings)
    pdf = ReportService(store, projects, cases).generate("tenant-a", large_case.case_id)
    reader = PdfReader(BytesIO(pdf.data))
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) >= 2
    assert "DRAFT CHRONOLOGY OF EVENTS" in pdf_text
    assert "Additional drainage work was recorded" in pdf_text

    # Verify DOCX generation alongside PDF
    docx_rep = ReportService(store, projects, cases).latest(
        "tenant-a", large_case.case_id, format="docx"
    )
    assert docx_rep.filename.endswith(".docx")
    assert len(docx_rep.data) > 5000


def test_report_generation_requires_completed_analysis(tmp_path):
    store = SQLiteObjectStore(tmp_path / "not-ready.db")
    projects = ProjectService(store)
    cases = CaseService(store, projects)
    project = projects.create(
        "tenant-a",
        "user-a",
        ProjectCreate(name="Synthetic report project"),
    )
    case = cases.create(
        "tenant-a",
        "user-a",
        CaseCreate.model_validate(case_body(project.project_id)),
    )
    reports = ReportService(store, projects, cases)

    with pytest.raises(AppError) as error:
        reports.generate("tenant-a", case.case_id)
    assert error.value.code == "ANALYSIS_NOT_READY"


def test_authenticated_report_routes_generate_and_download_pdf(app, client, members, make_token):
    members.add_member("tenant-a", "user-a", "a@a.test", "qs")
    _, _, case = _completed_case(app.state.container.store)
    auth_headers = headers(make_token("user-a"), "tenant-a")

    generated = client.post(f"/v1/cases/{case.case_id}/report", headers=auth_headers)
    assert generated.status_code == 200
    assert generated.headers["content-type"] == "application/pdf"
    assert generated.headers["x-report-version"] == "v1"
    assert generated.content.startswith(b"%PDF")

    downloaded = client.get(f"/v1/cases/{case.case_id}/report", headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == generated.content
    detail = client.get(f"/v1/cases/{case.case_id}", headers=auth_headers)
    assert detail.json()["report_refs"] == ["v1"]
