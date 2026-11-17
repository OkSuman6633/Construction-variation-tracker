SECRET = "test-secret-with-at-least-32-bytes-length!!"
ISS, AUD = "cvea-dev", "cvea-api"
PROJECT = {"name": "Project Alpha", "reference": "ALPHA-01"}


def headers(token: str, tenant: str, key: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant}
    if key:
        h["Idempotency-Key"] = key
    return h


def case_body(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "title": "Variation 014",
        "alleged_change": "Additional electrical units instructed on level 3.",
    }


def make_case(client, tok, tenant):
    p = client.post("/v1/projects", json=PROJECT, headers=headers(tok, tenant, "proj-key-0001"))
    assert p.status_code == 201, p.text
    c = client.post(
        "/v1/cases",
        json=case_body(p.json()["project_id"]),
        headers=headers(tok, tenant, "case-key-0001"),
    )
    assert c.status_code == 201, c.text
    return p.json()["project_id"], c.json()["case_id"]
