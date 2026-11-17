"""Idempotently copy the local filesystem store and JSONL audit log into SQLite."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.config import get_settings
from app.main import resolve_local_path
from app.storage.base import ObjectNotFound, ObjectStore, PreconditionFailed
from app.storage.local import LocalObjectStore
from app.storage.sqlite import SQLiteObjectStore


def migrate_objects(source: ObjectStore, target: ObjectStore) -> tuple[int, int]:
    migrated = 0
    identical = 0
    for object_path in source.list(""):
        data = source.get(object_path).data
        try:
            target.put(object_path, data, if_generation_match=0)
        except PreconditionFailed:
            try:
                existing = target.get(object_path).data
            except ObjectNotFound:
                raise RuntimeError(
                    f"Object changed while migrating: {object_path}"
                ) from None
            if existing != data:
                raise RuntimeError(f"Conflicting SQLite object: {object_path}") from None
            identical += 1
        else:
            migrated += 1
    return migrated, identical


def migrate_audit_log(source_path: Path, database_path: Path) -> tuple[int, int]:
    if not source_path.exists():
        return 0, 0

    migrated = 0
    existing = 0
    with closing(sqlite3.connect(database_path, timeout=30)) as db, db:
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            )
            """
        )
        for payload in source_path.read_text(encoding="utf-8").splitlines():
            if not payload:
                continue
            json.loads(payload)
            found = db.execute(
                "SELECT 1 FROM audit_events WHERE payload = ? LIMIT 1", (payload,)
            ).fetchone()
            if found:
                existing += 1
                continue
            db.execute("INSERT INTO audit_events (payload) VALUES (?)", (payload,))
            migrated += 1
    return migrated, existing


def main() -> None:
    settings = get_settings()
    if settings.app_env != "local":
        raise SystemExit("Migration is allowed only with APP_ENV=local.")
    if settings.storage_backend != "sqlite" or settings.audit_backend != "sqlite":
        raise SystemExit("Set STORAGE_BACKEND=sqlite and AUDIT_BACKEND=sqlite before migrating.")

    source_path = resolve_local_path(Path(settings.local_data_dir) / "objects")
    database_setting = settings.sqlite_database_path or str(
        Path(settings.local_data_dir) / "cvea.db"
    )
    database_path = resolve_local_path(database_setting)
    audit_path = resolve_local_path(
        Path(settings.local_data_dir) / "audit" / "audit_events.jsonl"
    )

    source = LocalObjectStore(source_path)
    target = SQLiteObjectStore(database_path)
    migrated_objects, identical_objects = migrate_objects(source, target)
    migrated_events, existing_events = migrate_audit_log(audit_path, database_path)
    print(
        f"Migrated {migrated_objects} object(s); skipped {identical_objects} identical "
        f"object(s); migrated {migrated_events} audit event(s); skipped {existing_events} "
        "existing event(s). Local source files were retained."
    )


if __name__ == "__main__":
    main()
