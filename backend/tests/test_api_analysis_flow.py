"""API-level flow: upload -> register -> analyse -> queue -> ADK worker -> timeline."""

import copy
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from construction_variation_agent.shared_libraries.profile_provider import load_profile
from tests.helpers import AUD, ISS, PROJECT, SECRET, case_body, headers
from tests.support.files import make_docx, make_eml, make_pdf
from tests.support.scripted_model import ScriptedLlm

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def api(tmp_path):
    s = Settings(
        app_env="test",
        local_data_dir=str(tmp_path),
        auth_dev_hs256_secret=SECRET,
        auth_issuer=ISS,
        auth_audience=AUD,
        gemini_api_key=None,
        gemini_model=None,
        google_genai_use_vertexai=False,
        rate_limit_per_minute=100_000,
        scanner_backend="dev_noop",
        task_queue_backend="manual",
        max_upload_bytes=1_000_000,
    )
    app = create_app(s)
    return app, TestClient(app, raise_server_exceptions=False)


class Env:
    def __init__(self, app, client, make_token):
        self.app, self.client, self.c = app, client, app.state.container
        self.tok = make_token("user-a")
        self.c.members.add_member("tenant-a", "user-a", "a@a.test", "qs")
        self.k = 0

    def h(self, write=True):
        self.k += 1
        return headers(self.tok, "tenant-a", f"key-{self.k:08d}" if write else None)

    def case(self):
        p = self.client.post("/v1/projects", json=PROJECT, headers=self.h()).json()["project_id"]
        return self.client.post("/v1/cases", json=case_body(p), headers=self.h()).json()["case_id"]

    def upload(
        self, case_id, name, mime, data, rec_date=None, declared_sha=None, declared_mime=None
    ):
        r = self.client.post(
            f"/v1/cases/{case_id}/upload-url",
            headers=self.h(False),
            json={"filename": name, "mime_type": declared_mime or mime, "size_bytes": len(data)},
        )
        assert r.status_code == 200, r.text
        doc_id = r.json()["document_id"]
        up = self.client.put(r.json()["upload_url"], content=data, headers=self.h(False))
        assert up.status_code == 204, up.text
        body = {
            "document_id": doc_id,
            "filename": name,
            "mime_type": declared_mime or mime,
            "sha256": declared_sha or hashlib.sha256(data).hexdigest(),
            "origin": "test",
        }
        if rec_date:
            body["recorded_event_date"] = rec_date
        return doc_id, self.client.post(
            f"/v1/cases/{case_id}/evidence", json=body, headers=self.h()
        )


@pytest.fixture
def env(api, make_token):
    return Env(*api, make_token)


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def load_basic(env):
    """Upload the six basic-profile documents as real files; return (case_id, remapped profile)."""
    prof = load_profile("synthetic_case_basic")
    case_id = env.case()
    mapping, refs = {}, {}
    for d in prof["documents"]:
        text = d["pages"][0]["text"]
        if d["document_id"] == "doc-002":
            mime, data, ref = "message/rfc822", make_eml(text), "message_body"
        elif d["document_id"] == "doc-004":
            mime, data, ref = "application/pdf", make_pdf(text), "page_1"
        else:
            mime, data, ref = DOCX, make_docx(text), "section_1"
        did, r = env.upload(case_id, d["filename"], mime, data, d["recorded_event_date"])
        assert r.status_code == 201, r.text
        mapping[d["document_id"]], refs[d["document_id"]] = did, ref
    p = copy.deepcopy(prof)
    for d in p["documents"]:
        d["pages"][0]["page_ref"] = refs[d["document_id"]]
        d["document_id"] = mapping[d["document_id"]]
    ex = p["expected"]
    for e in ex["events"]:
        e["page_ref"], e["document_id"] = refs[e["document_id"]], mapping[e["document_id"]]
    for r in ex["relationships"]:
        r["documents"] = [mapping[x] for x in r["documents"]]
    for key in ("candidates", "missing_evidence"):
        for item in ex[key]:
            item["event_keys"] = [[mapping[a], b] for a, b in item["event_keys"]]
    return case_id, p


def attach_model(env, profile, mode="oracle"):
    env.c.jobs.model_factory = lambda: ScriptedLlm(
        profile=profile, mode=mode, extractor_sizes=[], system_texts=[], content_dump=[]
    )


async def run_queued(env):
    results = []
    for tenant, job in list(env.c.queue.tasks):
        results.append(await env.c.jobs.execute(tenant, job))
    return results


async def test_full_flow_upload_to_review_ready_timeline(env):
    case_id, profile = load_basic(env)
    attach_model(env, profile)
    r = env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h())
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job_id = r.json()["job_id"]
    assert env.client.get(f"/v1/jobs/{job_id}", headers=env.h(False)).json()["status"] == "queued"
    assert (
        env.client.get(f"/v1/cases/{case_id}", headers=env.h(False)).json()["stage"] == "analysing"
    )

    assert await run_queued(env) == ["done"]
    job = env.client.get(f"/v1/jobs/{job_id}", headers=env.h(False)).json()
    assert job["status"] == "needs_review" and job["failure_code"] is None

    t = env.client.get(f"/v1/cases/{case_id}/timeline", headers=env.h(False)).json()
    assert t["stage"] == "needs_review" and "not legal advice" in t["notice"].lower()
    assert len(t["events"]) == 8 and len(t["candidates"]) == 1 and len(t["relationships"]) == 2
    dates = [e["event_date"] for e in t["events"] if e["event_date"]]
    assert dates == sorted(dates)
    assert all(e["review_status"] == "needs_review" and e["evidence"] for e in t["events"])
    assert t["candidates"][0]["entitlement_asserted"] is False
    assert {d["category"] for d in t["evidence"]} >= {
        "instruction",
        "quotation",
    }  # persisted from intake

    audit = [e["action"] for e in env.c.audit.read_all()]
    for a in ("job.queued", "job.started", "job.completed"):
        assert a in audit
    done = next(e for e in env.c.audit.read_all() if e["action"] == "job.completed")
    assert (
        done["detail"]["citation_validity"] == 1.0 and done["detail"]["model"] == "scripted-oracle"
    )


async def test_repeated_analyse_does_not_duplicate_jobs_or_tasks(env):
    case_id, profile = load_basic(env)
    attach_model(env, profile)
    h = env.h()
    a = env.client.post(f"/v1/cases/{case_id}/analyse", headers=h)
    b = env.client.post(f"/v1/cases/{case_id}/analyse", headers=h)  # same key: replay
    c = env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h())  # new key while active
    assert a.json()["job_id"] == b.json()["job_id"] == c.json()["job_id"]
    assert b.headers["Idempotent-Replayed"] == "true" and c.json()["duplicate_active_job"] is True
    assert len(env.c.queue.tasks) == 1
    assert [e["action"] for e in env.c.audit.read_all()].count("job.queued") == 1


async def test_worker_is_replay_safe(env):
    case_id, profile = load_basic(env)
    attach_model(env, profile)
    job_id = env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h()).json()["job_id"]
    tenant, job = env.c.queue.tasks[0]
    assert await env.c.jobs.execute(tenant, job) == "done"
    assert await env.c.jobs.execute(tenant, job) == "skipped"  # duplicate delivery
    assert [e["action"] for e in env.c.audit.read_all()].count("job.completed") == 1


async def test_worker_http_endpoint_status_mapping(env):
    case_id, profile = load_basic(env)
    attach_model(env, profile, mode="no_submit_intake")  # retryable stage failure
    env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h())
    tenant, job = env.c.queue.tasks[0]
    for attempt in range(1, 3):
        r = env.client.post(
            "/internal/tasks/analyse",
            json={"tenant_id": tenant, "job_id": job},
            headers={"X-CloudTasks-TaskRetryCount": str(attempt - 1)},
        )
        assert r.status_code == 503  # queue should redeliver
    r = env.client.post(
        "/internal/tasks/analyse",
        json={"tenant_id": tenant, "job_id": job},
        headers={"X-CloudTasks-TaskRetryCount": "2"},
    )
    assert r.status_code == 200  # attempts exhausted: job marked failed, no more retries
    j = env.client.get(f"/v1/jobs/{job}", headers=env.h(False)).json()
    assert j["status"] == "failed" and j["failure_code"] == "STAGE_INCOMPLETE"
    case = env.client.get(f"/v1/cases/{case_id}", headers=env.h(False)).json()
    assert (
        case["stage"] == "evidence_collection"
        and case["active_job_id"] is None
        and case["events"] == []
    )


async def test_unconfigured_gemini_fails_job_with_clear_code(env):
    case_id, _ = load_basic(env)  # default model_factory: GEMINI_MODEL/KEY unset
    env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h())
    assert await run_queued(env) == ["done"]
    job_id = env.c.queue.tasks[0][1]
    j = env.client.get(f"/v1/jobs/{job_id}", headers=env.h(False)).json()
    assert j["status"] == "failed" and j["failure_code"] == "MODEL_NOT_CONFIGURED"


def test_checksum_mime_size_and_duplicates(env):
    case_id = env.case()
    data = make_docx("Instruction CVI-100 dated 1 July 2026")
    _, r = env.upload(case_id, "a.docx", DOCX, data, declared_sha="0" * 64)
    assert r.status_code == 422 and r.json()["code"] == "CHECKSUM_MISMATCH"
    _, r = env.upload(case_id, "evil.pdf", "application/pdf", b"MZ\x90\x00 not a pdf at all")
    assert r.status_code == 415
    r = env.client.post(
        f"/v1/cases/{case_id}/upload-url",
        headers=env.h(False),
        json={"filename": "x.exe", "mime_type": "application/x-msdownload", "size_bytes": 10},
    )
    assert r.status_code == 415
    r = env.client.post(
        f"/v1/cases/{case_id}/upload-url",
        headers=env.h(False),
        json={"filename": "big.pdf", "mime_type": "application/pdf", "size_bytes": 10**9},
    )
    assert r.status_code == 413
    id1, r1 = env.upload(case_id, "one.docx", DOCX, data)
    id2, r2 = env.upload(case_id, "two.docx", DOCX, data)
    assert r1.json()["duplicate_of"] is None and r2.json()["duplicate_of"] == id1
    assert r1.json()["parser_status"] == "parsed" and r1.json()["page_count"] == 1
    # duplicates are not analysed twice
    assert env.c.jobs.eligible_documents(env.c.cases.get("tenant-a", case_id)) == [id1]


def test_quarantine_until_scanned_without_a_scanner(tmp_path, make_token):
    s = Settings(
        app_env="test",
        local_data_dir=str(tmp_path),
        auth_dev_hs256_secret=SECRET,
        auth_issuer=ISS,
        auth_audience=AUD,
        task_queue_backend="manual",
        scanner_backend="none",
    )
    app = create_app(s)
    e = Env(app, TestClient(app), make_token)
    case_id = e.case()
    _, r = e.upload(case_id, "a.docx", DOCX, make_docx("Hello CVI-100 text"))
    assert r.json()["parser_status"] == "quarantined"
    r = e.client.post(f"/v1/cases/{case_id}/analyse", headers=e.h())
    assert r.status_code == 422 and r.json()["code"] == "NO_ANALYSABLE_EVIDENCE"


def test_image_registered_as_analysable_vision_evidence(env):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "PNG")
    case_id = env.case()
    _, r = env.upload(case_id, "site.png", "image/png", buf.getvalue())
    assert r.status_code == 201 and r.json()["parser_status"] == "parsed"
    assert r.json()["page_count"] == 1
    queued = env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h())
    assert queued.status_code == 202


def test_cross_tenant_cannot_touch_evidence_jobs_or_timeline(env, make_token):
    case_id, _ = load_basic(env)
    env.c.members.add_member("tenant-b", "user-b", "b@b.test", "qs")
    tb = make_token("user-b")
    hb = lambda key=None: headers(tb, "tenant-b", key)  # noqa: E731
    assert env.client.get(f"/v1/cases/{case_id}/timeline", headers=hb()).status_code == 404
    assert (
        env.client.post(f"/v1/cases/{case_id}/analyse", headers=hb("tb-key-00001")).status_code
        == 404
    )
    assert (
        env.client.post(
            f"/v1/cases/{case_id}/upload-url",
            headers=hb(),
            json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 5},
        ).status_code
        == 404
    )
    job = env.client.post(f"/v1/cases/{case_id}/analyse", headers=env.h()).json()["job_id"]
    assert env.client.get(f"/v1/jobs/{job}", headers=hb()).status_code == 404
    # tenant-b cannot register a document id that lives under tenant-a's prefix
    r = env.client.post(
        f"/v1/cases/{case_id}/evidence",
        headers=hb("tb-key-00002"),
        json={
            "document_id": "doc-000000000001",
            "filename": "x.pdf",
            "mime_type": "application/pdf",
            "sha256": "0" * 64,
        },
    )
    assert r.status_code == 404


def test_viewer_cannot_upload_or_analyse(env, make_token):
    env.c.members.add_member("tenant-a", "viewer-9", None, "viewer")
    case_id = env.case()
    hv = lambda key=None: headers(make_token("viewer-9"), "tenant-a", key)  # noqa: E731
    assert (
        env.client.post(f"/v1/cases/{case_id}/analyse", headers=hv("v-key-000001")).status_code
        == 403
    )
    assert (
        env.client.post(
            f"/v1/cases/{case_id}/upload-url",
            headers=hv(),
            json={"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 5},
        ).status_code
        == 403
    )


def test_local_upload_endpoint_hidden_on_gcs_backend_and_no_overwrite(env):
    case_id = env.case()
    r = env.client.post(
        f"/v1/cases/{case_id}/upload-url",
        headers=env.h(False),
        json={"filename": "a.docx", "mime_type": DOCX, "size_bytes": 50},
    )
    url = r.json()["upload_url"]
    assert env.client.put(url, content=b"first", headers=env.h(False)).status_code == 204
    assert (
        env.client.put(url, content=b"second", headers=env.h(False)).status_code == 409
    )  # create-only
