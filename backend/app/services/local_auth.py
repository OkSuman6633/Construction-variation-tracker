"""Local-only email/password accounts for a single-machine SQLite deployment."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pydantic import BaseModel

from ..config import Settings
from ..errors import AppError
from ..storage import paths
from ..storage.base import ObjectNotFound, ObjectStore, PreconditionFailed

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_PASSWORD_BYTES_MAX = 128
_TOKEN_LIFETIME = timedelta(hours=12)


class LocalUser(BaseModel):
    user_id: str
    name: str
    email: str
    password_salt: str
    password_digest: str
    must_change_password: bool = False
    created_at: datetime


class LocalAuthService:
    def __init__(self, store: ObjectStore, settings: Settings) -> None:
        if settings.auth_dev_hs256_secret is None:
            raise RuntimeError("AUTH_DEV_HS256_SECRET is required for local password accounts")
        self.store, self.settings = store, settings
        self._secret = settings.auth_dev_hs256_secret.get_secret_value()
        if len(self._secret.encode("utf-8")) < 32:
            raise RuntimeError("AUTH_DEV_HS256_SECRET must contain at least 32 bytes")

    @staticmethod
    def _email(value: str) -> str:
        email = value.strip().casefold()
        if not _EMAIL.fullmatch(email):
            raise AppError("INVALID_EMAIL", "Enter a valid email address.", 422)
        return email

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password.encode("utf-8")) > _PASSWORD_BYTES_MAX:
            raise AppError("INVALID_PASSWORD", "Password must be 128 bytes or fewer.", 422)
        if len(password) < 12:
            raise AppError("INVALID_PASSWORD", "Password must be at least 12 characters.", 422)

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)

    def _load(self, email: str) -> tuple[LocalUser, int]:
        try:
            obj = self.store.get(paths.local_user(email))
        except ObjectNotFound:
            raise AppError("UNAUTHENTICATED", "Email or password is incorrect.", 401) from None
        return LocalUser.model_validate_json(obj.data), obj.generation

    def _save(self, user: LocalUser, generation: int) -> None:
        try:
            self.store.put(
                paths.local_user(user.email),
                user.model_dump_json().encode(),
                if_generation_match=generation,
            )
        except PreconditionFailed:
            raise AppError("CONFLICT", "The account changed; please retry.", 409, True) from None

    def _token(self, user: LocalUser) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": user.user_id,
                "email": user.email,
                "iss": self.settings.auth_issuer or "cvea-local",
                "aud": self.settings.auth_audience or "cvea-local",
                "iat": now,
                "exp": now + _TOKEN_LIFETIME,
            },
            self._secret,
            algorithm="HS256",
        )

    @staticmethod
    def _public(user: LocalUser) -> dict[str, Any]:
        return {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "must_change_password": user.must_change_password,
        }

    def session(self, user: LocalUser) -> dict[str, Any]:
        return {
            "access_token": self._token(user),
            "token_type": "bearer",
            "expires_in": int(_TOKEN_LIFETIME.total_seconds()),
            "user": self._public(user),
        }

    def register(self, name: str, email: str, password: str) -> dict[str, Any]:
        email = self._email(email)
        name = name.strip()
        if not name or len(name) > 200:
            raise AppError("INVALID_NAME", "Name must be between 1 and 200 characters.", 422)
        self._validate_password(password)
        salt = secrets.token_bytes(16)
        user = LocalUser(
            user_id=f"usr-{uuid.uuid4().hex[:16]}",
            name=name,
            email=email,
            password_salt=salt.hex(),
            password_digest=self._hash_password(password, salt).hex(),
            created_at=datetime.now(UTC),
        )
        try:
            self.store.put(
                paths.local_user(email), user.model_dump_json().encode(), if_generation_match=0
            )
        except PreconditionFailed:
            raise AppError("EMAIL_EXISTS", "An account already uses this email.", 409) from None
        return self.session(user)

    def authenticate(self, email: str, password: str) -> dict[str, Any]:
        email = self._email(email)
        self._validate_password(password)
        user, _ = self._load(email)
        try:
            salt = bytes.fromhex(user.password_salt)
            expected = bytes.fromhex(user.password_digest)
        except ValueError:
            raise AppError("UNAUTHENTICATED", "Email or password is incorrect.", 401) from None
        actual = self._hash_password(password, salt)
        if not hmac.compare_digest(actual, expected):
            raise AppError("UNAUTHENTICATED", "Email or password is incorrect.", 401)
        return self.session(user)

    def create_teammate(self, email: str) -> tuple[LocalUser, str | None]:
        email = self._email(email)
        try:
            user, _ = self._load(email)
            return user, None
        except AppError as exc:
            if exc.code != "UNAUTHENTICATED":
                raise
        temporary_password = secrets.token_urlsafe(18)
        salt = secrets.token_bytes(16)
        user = LocalUser(
            user_id=f"usr-{uuid.uuid4().hex[:16]}",
            name=email.split("@", maxsplit=1)[0],
            email=email,
            password_salt=salt.hex(),
            password_digest=self._hash_password(temporary_password, salt).hex(),
            must_change_password=True,
            created_at=datetime.now(UTC),
        )
        try:
            self.store.put(
                paths.local_user(email), user.model_dump_json().encode(), if_generation_match=0
            )
        except PreconditionFailed:
            user, _ = self._load(email)
            return user, None
        return user, temporary_password

    def change_password(self, user_id: str, email: str | None, current: str, new: str) -> None:
        if not email:
            raise AppError("UNAUTHENTICATED", "A local account is required.", 401)
        self._validate_password(current)
        self._validate_password(new)
        user, generation = self._load(self._email(email))
        if user.user_id != user_id:
            raise AppError("UNAUTHENTICATED", "Invalid local account.", 401)
        salt = bytes.fromhex(user.password_salt)
        if not hmac.compare_digest(
            self._hash_password(current, salt), bytes.fromhex(user.password_digest)
        ):
            raise AppError("UNAUTHENTICATED", "Current password is incorrect.", 401)
        new_salt = secrets.token_bytes(16)
        user.password_salt = new_salt.hex()
        user.password_digest = self._hash_password(new, new_salt).hex()
        user.must_change_password = False
        self._save(user, generation)
