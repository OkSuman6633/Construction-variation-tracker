import time

import jwt

from tests.helpers import AUD, ISS, PROJECT, SECRET, case_body, headers, make_case


def setup_two_tenants(members):
    members.add_member("tenant-a", "user-a", "a@a.test", "qs")
    members.add_member("tenant-b", "user-b", "b@b.test", "qs")


def test_missing_token_rejected(client):
    r = client.get("/v1/me")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    assert set(r.json()) == {"code", "message", "correlation_id", "retryable"}


def test_wrong_audience_issuer_expired_and_alg_none_rejected(client, make_token):
    for tok in (
        make_token(aud="other"),
        make_token(iss="evil"),
        make_token(exp_in=-10),
        make_token(secret="x" * 40),
        jwt.encode(
            {"sub": "u", "aud": AUD, "iss": ISS, "exp": int(time.time()) + 99},
            None,
            algorithm="none",
        ),
    ):
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


def test_hs256_refused_outside_local_and_test(tmp_path):
    from app.auth import TokenVerifier
    from app.config import Settings
    from app.errors import AppError

    s = Settings(
        app_env="dev",
        local_data_dir=str(tmp_path),
        auth_dev_hs256_secret=SECRET,
        auth_issuer=ISS,
        auth_audience=AUD,
    )
    tok = jwt.encode(
        {"sub": "u", "aud": AUD, "iss": ISS, "exp": int(time.time()) + 99},
        SECRET,
        algorithm="HS256",
    )
    try:
        TokenVerifier(s).verify(tok)
        raise AssertionError("HS256 accepted in dev")
    except AppError as e:
        assert e.status == 401


def test_unsafe_production_config_refused(tmp_path):
    from app.config import Settings

    s = Settings(app_env="production", local_data_dir=str(tmp_path))
    try:
        s.assert_safe_for_env()
        raise AssertionError("unsafe production config accepted")
    except RuntimeError as e:
        assert "STORAGE_BACKEND" in str(e) and "AUDIT_BACKEND" in str(e)


def test_non_member_gets_403_and_unknown_tenant_indistinguishable(client, members, make_token):
    setup_two_tenants(members)
    tok = make_token("user-a")
    a = client.get("/v1/projects", headers=headers(tok, "tenant-b"))
    b = client.get("/v1/projects", headers=headers(tok, "no-such-tenant"))
    c = client.get("/v1/projects", headers=headers(tok, "../../etc"))
    assert a.status_code == b.status_code == c.status_code == 403
    assert a.json()["message"] == b.json()["message"] == c.json()["message"]


def test_cross_tenant_case_and_project_access_denied(client, members, make_token):
    setup_two_tenants(members)
    ta, tb = make_token("user-a"), make_token("user-b")
    proj_a, case_a = make_case(client, ta, "tenant-a")
    # user-b, as a legitimate member of tenant-b, asks for tenant-a's IDs
    for path in (f"/v1/cases/{case_a}", f"/v1/projects/{proj_a}"):
        r = client.get(path, headers=headers(tb, "tenant-b"))
        assert r.status_code == 404
    assert client.get("/v1/cases", headers=headers(tb, "tenant-b")).json()["items"] == []
    # cannot create a case in tenant-b against tenant-a's project
    r = client.post(
        "/v1/cases", json=case_body(proj_a), headers=headers(tb, "tenant-b", "x-key-00001")
    )
    assert r.status_code == 404
    # and tenant-a still sees its case
    assert client.get(f"/v1/cases/{case_a}", headers=headers(ta, "tenant-a")).status_code == 200


def test_role_permissions_enforced_server_side(client, members, make_token):
    members.add_member("tenant-a", "viewer-1", None, "viewer")
    r = client.post(
        "/v1/projects",
        json=PROJECT,
        headers=headers(make_token("viewer-1"), "tenant-a", "viewer-key-01"),
    )
    assert r.status_code == 403


def test_organisations_lists_only_own_memberships(client, members, make_token):
    setup_two_tenants(members)
    r = client.get("/v1/organisations", headers={"Authorization": f"Bearer {make_token('user-a')}"})
    assert r.json()["organisations"] == [
        {"tenant_id": "tenant-a", "name": "tenant-a", "role": "qs"}
    ]


def test_user_can_create_and_list_an_organisation(client, make_token):
    token = make_token("new-owner")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-org-0001"}
    created = client.post(
        "/v1/organisations",
        json={"name": "North Works"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "North Works"
    assert created.json()["role"] == "owner"

    replay = client.post("/v1/organisations", json={"name": "North Works"}, headers=headers)
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == created.json()

    listed = client.get(
        "/v1/organisations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.json()["organisations"] == [created.json()]


def test_frontend_cors_preflight_allows_only_configured_origins(client):
    allowed = client.options(
        "/v1/organisations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"

    blocked = client.options(
        "/v1/organisations",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in blocked.headers
