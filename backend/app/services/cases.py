"""Project and case persistence with optimistic concurrency (GCS generation match)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from construction_variation_agent.shared_libraries.schemas import (
    CaseCreate,
    CaseState,
    Project,
    ProjectCreate,
)
from construction_variation_agent.shared_libraries.types import CaseStage

from ..errors import not_found, revision_conflict
from ..storage import paths
from ..storage.base import ObjectNotFound, ObjectStore, PreconditionFailed


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ProjectService:
    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def create(self, tenant_id: str, user_id: str, data: ProjectCreate) -> Project:
        project = Project(
            **data.model_dump(), project_id=new_id("proj"), tenant_id=tenant_id, created_by=user_id
        )
        self.store.put(
            paths.project(tenant_id, project.project_id),
            project.model_dump_json().encode(),
            if_generation_match=0,
        )
        return project

    def get(self, tenant_id: str, project_id: str) -> Project:
        try:
            obj = self.store.get(paths.project(tenant_id, project_id))
        except ObjectNotFound:
            raise not_found("Project") from None
        return Project.model_validate_json(obj.data)

    def list(self, tenant_id: str) -> list[Project]:
        items = [
            Project.model_validate_json(self.store.get(n).data)
            for n in self.store.list(paths.project_prefix(tenant_id))
        ]
        return sorted(items, key=lambda p: p.created_at, reverse=True)


class CaseService:
    def __init__(self, store: ObjectStore, projects: ProjectService) -> None:
        self.store = store
        self.projects = projects

    def create(self, tenant_id: str, user_id: str, data: CaseCreate) -> CaseState:
        self.projects.get(tenant_id, data.project_id)  # must exist inside THIS tenant
        state = CaseState(
            case_id=new_id("case"),
            tenant_id=tenant_id,
            created_by=user_id,
            stage=CaseStage.EVIDENCE_COLLECTION,
            **data.model_dump(),
        )
        self.store.put(
            paths.case_state(tenant_id, state.case_id),
            state.model_dump_json().encode(),
            if_generation_match=0,
        )
        return state

    def get(self, tenant_id: str, case_id: str) -> CaseState:
        return self._load(tenant_id, case_id)[0]

    def _load(self, tenant_id: str, case_id: str) -> tuple[CaseState, int]:
        try:
            obj = self.store.get(paths.case_state(tenant_id, case_id))
        except ObjectNotFound:
            raise not_found("Case") from None
        return CaseState.model_validate_json(obj.data), obj.generation

    def list(self, tenant_id: str, project_id: str | None = None) -> list[CaseState]:
        out = []
        for name in self.store.list(paths.case_prefix(tenant_id)):
            if name.endswith("/state.json"):
                s = CaseState.model_validate_json(self.store.get(name).data)
                if project_id is None or s.project_id == project_id:
                    out.append(s)
        return sorted(out, key=lambda c: c.created_at, reverse=True)

    def update(
        self,
        tenant_id: str,
        case_id: str,
        mutate: Callable[[CaseState], CaseState],
        expected_revision: int | None = None,
    ) -> CaseState:
        """Load -> mutate -> write with ifGenerationMatch. Stale input returns HTTP 409."""
        state, generation = self._load(tenant_id, case_id)
        if expected_revision is not None and state.revision != expected_revision:
            raise revision_conflict()
        new = mutate(state.model_copy(deep=True))
        new.revision = state.revision + 1
        new.updated_at = datetime.now(UTC)
        try:
            self.store.put(
                paths.case_state(tenant_id, case_id),
                new.model_dump_json().encode(),
                if_generation_match=generation,
            )
        except PreconditionFailed:
            raise revision_conflict() from None
        return new
