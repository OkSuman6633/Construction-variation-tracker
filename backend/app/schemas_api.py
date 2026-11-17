"""API request models for evidence and jobs."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from construction_variation_agent.shared_libraries.schemas import Identifier, ShortText, Strict


class UploadUrlRequest(Strict):
    filename: ShortText = Field(min_length=1)
    mime_type: ShortText
    size_bytes: int = Field(gt=0)


class RegisterEvidence(Strict):
    document_id: Identifier
    filename: ShortText = Field(min_length=1)
    mime_type: ShortText
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_event_date: date | None = None  # date ON the document, as supplied by the user
    event_date_unknown: bool = False
    origin: ShortText | None = None


class OrganisationCreate(Strict):
    name: ShortText = Field(min_length=2)


class LocalSignup(Strict):
    name: ShortText = Field(min_length=1, max_length=200)
    email: ShortText = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class LocalLogin(Strict):
    email: ShortText = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class PasswordChange(Strict):
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class MemberCreate(Strict):
    email: ShortText = Field(min_length=3, max_length=320)
    role: str = Field(pattern=r"^(admin|qs|external_reviewer|viewer)$")
