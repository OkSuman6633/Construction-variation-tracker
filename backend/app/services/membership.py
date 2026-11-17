"""Tenant membership stored in GCS (no SQL database)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from ..auth import PERMISSIONS, TenantContext
from ..errors import AppError, forbidden
from ..storage import paths
from ..storage.base import ObjectNotFound, ObjectStore, PreconditionFailed


class Member(BaseModel):
    user_id: str
    email: str | None = None
    name: str | None = None
    role: str


class MemberList(BaseModel):
    members: list[Member] = []


class UserMemberships(BaseModel):
    tenants: dict[str, str] = {}


class Organisation(BaseModel):
    tenant_id: str
    name: str
    created_by: str
    created_at: datetime


def key_hash(tenant_id: str, key: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{key}".encode()).hexdigest()


class MembershipService:
    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def _load(self, tenant: str) -> tuple[MemberList, int]:
        try:
            obj = self.store.get(paths.members(tenant))
        except ObjectNotFound:
            return MemberList(), 0
        return MemberList.model_validate_json(obj.data), obj.generation

    def resolve(self, tenant_id: str, user_id: str, email: str | None) -> TenantContext:
        """Uniform 403 for unknown tenants, malformed IDs and non-members (no enumeration)."""
        try:
            members, _ = self._load(tenant_id)
        except AppError:
            raise forbidden("Not a member of this organisation.") from None
        for m in members.members:
            if m.user_id == user_id:
                return TenantContext(tenant_id, user_id, email or m.email, m.role)
        raise forbidden("Not a member of this organisation.")

    def add_member(
        self, tenant_id: str, user_id: str, email: str | None, role: str, name: str | None = None
    ) -> None:
        if role not in PERMISSIONS:
            raise AppError("INVALID_ROLE", "Unknown role.", 400)
        for _ in range(5):
            members, gen = self._load(tenant_id)
            members.members = [m for m in members.members if m.user_id != user_id]
            members.members.append(Member(user_id=user_id, email=email, name=name, role=role))
            try:
                self.store.put(
                    paths.members(tenant_id),
                    members.model_dump_json().encode(),
                    if_generation_match=gen,
                )
                break
            except PreconditionFailed:
                continue
        else:
            raise AppError("CONFLICT", "Could not update membership; retry.", 409, True)
        self._index(user_id, tenant_id, role)

    def list_members(self, tenant_id: str) -> list[Member]:
        return self._load(tenant_id)[0].members

    def create_organisation(self, user_id: str, email: str | None, name: str) -> Organisation:
        tenant_id = f"org-{uuid.uuid4().hex[:12]}"
        organisation = Organisation(
            tenant_id=tenant_id,
            name=name,
            created_by=user_id,
            created_at=datetime.now(UTC),
        )
        self.store.put(
            paths.organisation(tenant_id),
            organisation.model_dump_json().encode(),
            if_generation_match=0,
        )
        try:
            self.add_member(tenant_id, user_id, email, "owner")
        except Exception:
            self.store.delete(paths.organisation(tenant_id))
            raise
        return organisation

    def organisations_for(self, user_id: str) -> list[dict[str, str]]:
        organisations = []
        for tenant_id, role in sorted(self.tenants_for(user_id).items()):
            try:
                obj = self.store.get(paths.organisation(tenant_id))
            except ObjectNotFound:
                name = tenant_id
            else:
                name = Organisation.model_validate_json(obj.data).name
            organisations.append({"tenant_id": tenant_id, "name": name, "role": role})
        return organisations

    def _index(self, user_id: str, tenant_id: str, role: str) -> None:
        path = paths.user_memberships(user_id)
        for _ in range(5):
            try:
                obj = self.store.get(path)
                idx, gen = UserMemberships.model_validate_json(obj.data), obj.generation
            except ObjectNotFound:
                idx, gen = UserMemberships(), 0
            idx.tenants[tenant_id] = role
            try:
                self.store.put(path, idx.model_dump_json().encode(), if_generation_match=gen)
                return
            except PreconditionFailed:
                continue

    def tenants_for(self, user_id: str) -> dict[str, str]:
        try:
            obj = self.store.get(paths.user_memberships(user_id))
        except ObjectNotFound:
            return {}
        return UserMemberships.model_validate_json(obj.data).tenants
