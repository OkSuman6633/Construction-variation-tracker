"""The ONLY place object names are constructed.

IDs are validated against a strict pattern so neither request bodies nor agents
can influence a path (no traversal, no cross-tenant prefixes).
"""

import hashlib
import re

from ..errors import AppError

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
_VERSION = re.compile(r"^v[0-9]{1,6}$")


def _ident(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise AppError("INVALID_ID", f"Invalid {label}.", 400)
    return value


def case_state(tenant: str, case: str) -> str:
    return f"cases/{_ident(tenant, 'tenant')}/{_ident(case, 'case')}/state.json"


def case_prefix(tenant: str) -> str:
    return f"cases/{_ident(tenant, 'tenant')}/"


def upload(tenant: str, case: str, document: str) -> str:
    t, c, d = _ident(tenant, "tenant"), _ident(case, "case"), _ident(document, "document")
    return f"uploads/{t}/{c}/{d}"


def report(tenant: str, case: str, version: str, ext: str) -> str:
    if not _VERSION.match(version) or ext not in {"pdf", "docx"}:
        raise AppError("INVALID_ID", "Invalid report version.", 400)
    return f"reports/{_ident(tenant, 'tenant')}/{_ident(case, 'case')}/{version}.{ext}"


def report_manifest(tenant: str, case: str) -> str:
    return f"reports/{_ident(tenant, 'tenant')}/{_ident(case, 'case')}/manifest.json"


def project(tenant: str, project_id: str) -> str:
    return f"projects/{_ident(tenant, 'tenant')}/{_ident(project_id, 'project')}.json"


def project_prefix(tenant: str) -> str:
    return f"projects/{_ident(tenant, 'tenant')}/"


def members(tenant: str) -> str:
    return f"tenants/{_ident(tenant, 'tenant')}/members.json"


def organisation(tenant: str) -> str:
    return f"tenants/{_ident(tenant, 'tenant')}/organisation.json"


def job(tenant: str, job_id: str) -> str:
    return f"jobs/{_ident(tenant, 'tenant')}/{_ident(job_id, 'job')}.json"


def idempotency(tenant: str, key_hash: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", key_hash):
        raise AppError("INVALID_ID", "Invalid idempotency key.", 400)
    return f"idempotency/{_ident(tenant, 'tenant')}/{key_hash}.json"


def user_memberships(user_id: str) -> str:
    return f"users/{hashlib.sha256(user_id.encode()).hexdigest()}/memberships.json"


def local_user(email: str) -> str:
    email_key = hashlib.sha256(email.strip().casefold().encode()).hexdigest()
    return f"local-auth/users/{email_key}.json"
