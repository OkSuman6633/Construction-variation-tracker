"""SQLite-backed local object store. Records and uploaded evidence share one database file."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .base import ObjectNotFound, PreconditionFailed, StoredObject


class SQLiteObjectStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS objects (
                    object_path TEXT PRIMARY KEY,
                    data BLOB,
                    generation INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    def get(self, path: str) -> StoredObject:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT data, generation, deleted FROM objects WHERE object_path = ?",
                (path,),
            ).fetchone()
        if row is None or row[2]:
            raise ObjectNotFound(path)
        return StoredObject(bytes(row[0]), int(row[1]))

    def put(self, path: str, data: bytes, *, if_generation_match: int | None = None) -> int:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT generation, deleted FROM objects WHERE object_path = ?",
                (path,),
            ).fetchone()
            current = int(row[0]) if row is not None and not row[1] else 0
            if if_generation_match is not None and if_generation_match != current:
                raise PreconditionFailed(path)
            generation = (int(row[0]) if row is not None else 0) + 1
            db.execute(
                """
                INSERT INTO objects (object_path, data, generation, deleted)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(object_path) DO UPDATE SET
                    data = excluded.data,
                    generation = excluded.generation,
                    deleted = 0
                """,
                (path, sqlite3.Binary(data), generation),
            )
            return generation

    def list(self, prefix: str) -> list[str]:
        stripped = prefix.rstrip("/")
        with closing(self._connect()) as db, db:
            if not stripped:
                rows = db.execute(
                    "SELECT object_path FROM objects WHERE deleted = 0 ORDER BY object_path"
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT object_path FROM objects
                    WHERE deleted = 0 AND (
                        object_path = ? OR
                        substr(object_path, 1, length(?) + 1) = ? || '/'
                    )
                    ORDER BY object_path
                    """,
                    (stripped, stripped, stripped),
                ).fetchall()
        return [str(row[0]) for row in rows]

    def delete(self, path: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT generation FROM objects WHERE object_path = ? AND deleted = 0",
                (path,),
            ).fetchone()
            if row is not None:
                db.execute(
                    "UPDATE objects SET data = NULL, deleted = 1 WHERE object_path = ?",
                    (path,),
                )
