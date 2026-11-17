import time
from collections.abc import Callable

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.helpers import AUD, ISS, SECRET


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        local_data_dir=str(tmp_path),
        auth_dev_hs256_secret=SECRET,
        auth_issuer=ISS,
        auth_audience=AUD,
        rate_limit_per_minute=10_000,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_token() -> Callable[..., str]:
    def _make(sub="user-a", email=None, aud=AUD, iss=ISS, exp_in=3600, secret=SECRET, alg="HS256"):
        claims = {"sub": sub, "aud": aud, "iss": iss, "exp": int(time.time()) + exp_in}
        if email:
            claims["email"] = email
        return jwt.encode(claims, secret, algorithm=alg)

    return _make


@pytest.fixture
def members(app):
    return app.state.container.members
