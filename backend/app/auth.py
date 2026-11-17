"""Server-side JWT verification and tenant-scoped authorisation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from .config import Settings
from .errors import AppError, forbidden

_FULL = frozenset(
    {
        "case:create",
        "case:read",
        "evidence:upload",
        "analyse",
        "review",
        "export",
        "project:write",
        "usage:read",
    }
)
PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": _FULL | {"member:manage", "admin:pricing"},
    "admin": _FULL | {"member:manage", "admin:pricing"},
    "qs": _FULL,
    "external_reviewer": frozenset({"case:read", "review"}),
    "viewer": frozenset({"case:read"}),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str | None


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str
    email: str | None
    role: str

    def require(self, permission: str) -> None:
        if permission not in PERMISSIONS.get(self.role, frozenset()):
            raise forbidden()


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._jwks = PyJWKClient(settings.auth_jwks_url) if settings.auth_jwks_url else None

    def verify(self, token: str) -> Principal:
        s = self._s
        bad = AppError("UNAUTHENTICATED", "Invalid or expired token.", 401)
        try:
            alg = jwt.get_unverified_header(token).get("alg", "")
            options: Any = {"require": ["exp", "iss", "aud", "sub"]}
            if alg == "HS256":
                if s.app_env not in {"local", "test"} or s.auth_dev_hs256_secret is None:
                    raise bad
                claims = jwt.decode(
                    token,
                    s.auth_dev_hs256_secret.get_secret_value(),
                    algorithms=["HS256"],
                    audience=s.auth_audience,
                    issuer=s.auth_issuer,
                    options=options,
                )
            elif alg in {"RS256", "ES256"} and self._jwks is not None:
                key = self._jwks.get_signing_key_from_jwt(token).key
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256", "ES256"],
                    audience=s.auth_audience,
                    issuer=s.auth_issuer,
                    options=options,
                )
            else:
                raise bad  # includes alg=none and unsupported algorithms
        except AppError:
            raise
        except jwt.PyJWTError as exc:
            raise bad from exc
        return Principal(user_id=str(claims["sub"]), email=claims.get("email"))
