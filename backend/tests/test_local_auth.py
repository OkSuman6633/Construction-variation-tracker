from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.helpers import headers


def test_local_signup_org_teammate_login_and_password_change(client, settings):
    signup = client.post(
        "/v1/auth/signup",
        json={
            "name": "Owner One",
            "email": "OWNER@example.test",
            "password": "owner-password-123",
        },
    )
    assert signup.status_code == 201, signup.text
    owner_session = signup.json()
    assert owner_session["user"]["email"] == "owner@example.test"
    assert owner_session["user"]["must_change_password"] is False
    owner_token = owner_session["access_token"]

    organisation = client.post(
        "/v1/organisations",
        json={"name": "North Works"},
        headers={
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": "local-org-create-0001",
        },
    )
    assert organisation.status_code == 201, organisation.text
    tenant_id = organisation.json()["tenant_id"]

    added = client.post(
        f"/v1/organisations/{tenant_id}/members",
        json={"email": "qs@example.test", "role": "qs"},
        headers=headers(owner_token, tenant_id),
    )
    assert added.status_code == 201, added.text
    result = added.json()
    assert result["member"]["email"] == "qs@example.test"
    assert result["temporary_password"]

    member_login = client.post(
        "/v1/auth/login",
        json={"email": "qs@example.test", "password": result["temporary_password"]},
    )
    assert member_login.status_code == 200
    member_session = member_login.json()
    assert member_session["user"]["must_change_password"] is True
    changed = client.post(
        "/v1/auth/change-password",
        json={
            "current_password": result["temporary_password"],
            "new_password": "teammate-password-456",
        },
        headers={"Authorization": f"Bearer {member_session['access_token']}"},
    )
    assert changed.status_code == 200, changed.text

    relogin = client.post(
        "/v1/auth/login",
        json={"email": "qs@example.test", "password": "teammate-password-456"},
    )
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_change_password"] is False
    listed = client.get(
        f"/v1/organisations/{tenant_id}/members",
        headers=headers(owner_token, tenant_id),
    )
    assert listed.status_code == 200
    assert {member["email"] for member in listed.json()["members"]} == {
        "owner@example.test",
        "qs@example.test",
    }

    database = Path(settings.sqlite_database_path or Path(settings.local_data_dir) / "cvea.db")
    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"objects", "audit_events"} <= tables


def test_local_signup_rejects_duplicate_email_and_short_password(client):
    body = {"name": "Owner", "email": "owner@example.test", "password": "owner-password-123"}
    assert client.post("/v1/auth/signup", json=body).status_code == 201
    assert client.post("/v1/auth/signup", json=body).status_code == 409
    body["email"] = "other@example.test"
    body["password"] = "short"
    assert client.post("/v1/auth/signup", json=body).status_code == 422
