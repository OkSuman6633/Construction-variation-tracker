"""Object store abstraction with GCS-style generation preconditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PreconditionFailed(Exception):
    """Generation no longer matches (HTTP 409 at the API layer)."""


class ObjectNotFound(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    data: bytes
    generation: int


class ObjectStore(Protocol):
    def get(self, path: str) -> StoredObject: ...

    def put(self, path: str, data: bytes, *, if_generation_match: int | None = None) -> int:
        """Write and return the new generation. if_generation_match=0 = create only if absent."""
        ...

    def list(self, prefix: str) -> list[str]: ...

    def delete(self, path: str) -> None: ...
