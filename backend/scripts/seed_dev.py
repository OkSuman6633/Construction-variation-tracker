"""Seed a LOCAL dev store with two tenants and print dev tokens. Never for deployed envs."""

import time

import jwt

from app.config import get_settings
from app.main import build_container


def main() -> None:
    s = get_settings()
    if s.app_env not in {"local", "test"} or s.auth_dev_hs256_secret is None:
        raise SystemExit("Refusing: needs APP_ENV=local|test and AUTH_DEV_HS256_SECRET")
    c = build_container(s)
    c.members.add_member("tenant-alpha", "qs-alice", "alice@example.test", "qs")
    c.members.add_member("tenant-beta", "qs-bob", "bob@example.test", "qs")
    for user in ("qs-alice", "qs-bob"):
        tok = jwt.encode(
            {
                "sub": user,
                "aud": s.auth_audience,
                "iss": s.auth_issuer,
                "exp": int(time.time()) + 86400,
            },
            s.auth_dev_hs256_secret.get_secret_value(),
            algorithm="HS256",
        )
        print(f"{user}: {tok}")


if __name__ == "__main__":
    main()
