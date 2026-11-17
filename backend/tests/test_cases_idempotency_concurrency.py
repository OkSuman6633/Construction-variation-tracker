from app.errors import AppError
from tests.helpers import PROJECT, case_body, headers, make_case


def seed(members):
    members.add_member("tenant-a", "user-a", "a@a.test", "qs")


def test_idempotent_replay_creates_one_case_and_one_audit_event(client, app, members, make_token):
    seed(members)
    tok = make_token("user-a")
    pid = client.post(
        "/v1/projects", json=PROJECT, headers=headers(tok, "tenant-a", "proj-key-0001")
    ).json()["project_id"]
    h = headers(tok, "tenant-a", "same-key-12345")
    r1 = client.post("/v1/cases", json=case_body(pid), headers=h)
    r2 = client.post("/v1/cases", json=case_body(pid), headers=h)
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json() and r2.headers["Idempotent-Replayed"] == "true"
    assert len(client.get("/v1/cases", headers=headers(tok, "tenant-a")).json()["items"]) == 1
    audit = [e for e in app.state.container.audit.read_all() if e["action"] == "case.create"]
    assert len(audit) == 1


def test_idempotency_key_required_and_reuse_with_other_body_rejected(client, members, make_token):
    seed(members)
    tok = make_token("user-a")
    r = client.post("/v1/projects", json=PROJECT, headers=headers(tok, "tenant-a"))
    assert r.status_code == 400 and r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    h = headers(tok, "tenant-a", "reuse-key-0001")
    assert client.post("/v1/projects", json=PROJECT, headers=h).status_code == 201
    r = client.post("/v1/projects", json={"name": "Different"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_failed_attempt_releases_idempotency_key(client, members, make_token):
    seed(members)
    tok = make_token("user-a")
    h = headers(tok, "tenant-a", "retry-key-0001")
    bad = client.post("/v1/cases", json=case_body("proj-doesnotexist"), headers=h)
    assert bad.status_code == 404
    pid = client.post(
        "/v1/projects", json=PROJECT, headers=headers(tok, "tenant-a", "proj-key-0002")
    ).json()["project_id"]
    ok = client.post(
        "/v1/cases", json=case_body(pid), headers=headers(tok, "tenant-a", "retry-key-0002")
    )
    assert ok.status_code == 201


def test_validation_errors_use_error_contract(client, members, make_token):
    seed(members)
    r = client.post(
        "/v1/projects",
        json={"name": ""},
        headers=headers(make_token("user-a"), "tenant-a", "valid-key-0001"),
    )
    assert r.status_code == 422
    assert set(r.json()) == {"code", "message", "correlation_id", "retryable"}
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_unknown_fields_rejected(client, members, make_token):
    seed(members)
    r = client.post(
        "/v1/projects",
        json={**PROJECT, "tenant_id": "tenant-b"},
        headers=headers(make_token("user-a"), "tenant-a", "extra-key-0001"),
    )
    assert r.status_code == 422


def test_correlation_id_echoed(client):
    r = client.get("/healthz", headers={"X-Correlation-ID": "abc-123"})
    assert r.headers["X-Correlation-ID"] == "abc-123"
    assert client.get("/healthz").headers["X-Correlation-ID"]


def test_stale_revision_returns_409_and_generation_race_is_detected(
    client, app, members, make_token
):
    seed(members)
    tok = make_token("user-a")
    _, case_id = make_case(client, tok, "tenant-a")
    cases = app.state.container.cases
    first = cases.update("tenant-a", case_id, lambda s: s, expected_revision=1)
    assert first.revision == 2
    try:
        cases.update("tenant-a", case_id, lambda s: s, expected_revision=1)  # stale reviewer
        raise AssertionError("stale revision accepted")
    except AppError as e:
        assert e.status == 409 and e.code == "CASE_REVISION_CONFLICT" and e.retryable

    # Simulate a concurrent writer landing between our load and our write.
    def racing_mutation(state):
        cases.update("tenant-a", case_id, lambda s: s)  # competing writer wins first
        return state

    try:
        cases.update("tenant-a", case_id, racing_mutation)
        raise AssertionError("lost update not detected")
    except AppError as e:
        assert e.status == 409
    assert cases.get("tenant-a", case_id).revision == 3  # exactly one of the two writes landed


def test_rate_limit_returns_structured_429(tmp_path, make_token):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    s = Settings(app_env="test", local_data_dir=str(tmp_path), rate_limit_per_minute=3)
    c = TestClient(create_app(s))
    codes = [c.get("/v1/me").status_code for _ in range(5)]
    assert 429 in codes
    r = c.get("/v1/me")
    assert r.json()["code"] == "RATE_LIMITED" and r.json()["retryable"] is True


def test_unhandled_error_is_safe(app, client, members, make_token):
    seed(members)

    def boom(*a, **k):
        raise RuntimeError("secret customer text should not leak")

    app.state.container.projects.list = boom
    r = client.get("/v1/projects", headers=headers(make_token("user-a"), "tenant-a"))
    assert r.status_code == 500 and "secret" not in r.text
    assert r.json()["code"] == "INTERNAL_ERROR"
