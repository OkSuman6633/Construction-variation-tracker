"""LOCAL FILESYSTEM adapter - development and test only (refused in staging/production).

Models GCS generation preconditions so concurrency logic can be tested without
cloud access. It is NOT evidence that the GCS adapter works.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .base import ObjectNotFound, PreconditionFailed, StoredObject


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _p(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if not str(target).startswith(str(self.root) + os.sep):
            raise ValueError("path escapes store root")
        return target

    @staticmethod
    def _side(target: Path, ext: str) -> Path:
        return target.with_name(target.name + ext)

    def _current(self, target: Path) -> int:
        gp = self._side(target, ".gen")
        if not target.exists() or not gp.exists():
            return 0
        return int(json.loads(gp.read_text())["generation"])

    def get(self, path: str) -> StoredObject:
        with self._lock:
            target = self._p(path)
            if not target.is_file():
                raise ObjectNotFound(path)
            return StoredObject(target.read_bytes(), self._current(target))

    def put(self, path: str, data: bytes, *, if_generation_match: int | None = None) -> int:
        with self._lock:
            target = self._p(path)
            current = self._current(target)
            if if_generation_match is not None and if_generation_match != current:
                raise PreconditionFailed(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            tomb = self._side(target, ".tomb")
            floor = int(tomb.read_text()) if tomb.exists() else 0
            new_gen = max(current, floor) + 1  # monotonic across deletes
            target.write_bytes(data)
            self._side(target, ".gen").write_text(json.dumps({"generation": new_gen}))
            return new_gen

    def list(self, prefix: str) -> list[str]:
        with self._lock:
            stripped = prefix.rstrip("/")
            base = self._p(stripped) if stripped else self.root
            if not base.exists():
                return []
            return sorted(
                p.relative_to(self.root).as_posix()
                for p in base.rglob("*")
                if p.is_file() and p.suffix not in {".gen", ".tomb"}
            )

    def delete(self, path: str) -> None:
        with self._lock:
            target = self._p(path)
            if target.exists():
                gen = self._current(target)
                target.unlink()
                self._side(target, ".gen").unlink(missing_ok=True)
                self._side(target, ".tomb").write_text(str(gen))
