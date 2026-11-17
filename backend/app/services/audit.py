"""Append-only audit sink. Never receives raw document content or prompts."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from ..errors import correlation_id_var

log = logging.getLogger("cvea.audit")


class AuditEvent(BaseModel):
    correlation_id: str = Field(default_factory=lambda: correlation_id_var.get())
    action: str
    actor_id: str
    tenant_id: str
    object_type: str
    object_id: str
    object_version: int | None = None
    outcome: str = "success"
    model_version: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class JsonlAuditSink:
    """DEV/TEST ONLY: append-only JSON lines on local disk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: AuditEvent) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x]


class SQLiteAuditSink:
    """Local append-only audit events stored alongside SQLite-backed application data."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    def append(self, event: AuditEvent) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO audit_events (payload) VALUES (?)", (event.model_dump_json(),))

    def read_all(self) -> list[dict]:
        with closing(self._connect()) as db, db:
            rows = db.execute("SELECT payload FROM audit_events ORDER BY event_id").fetchall()
        return [json.loads(row[0]) for row in rows]


class BigQueryAuditSink:
    """Production sink: streaming insert into the partitioned `audit_events` table.

    STATUS: not verified against live BigQuery from the build sandbox.
    KNOWN LIMITATION until the Milestone 6 outbox: a failed insert is logged with
    audit_lost=true (alerted via log-based metric) and the business action is not rolled back.
    """

    def __init__(self, project: str, dataset: str) -> None:
        from google.cloud import bigquery

        self._client = bigquery.Client(project=project)
        self._table = f"{project}.{dataset}.audit_events"

    def append(self, event: AuditEvent) -> None:
        row = json.loads(event.model_dump_json())
        row["detail"] = json.dumps(row["detail"])
        try:
            errors = self._client.insert_rows_json(self._table, [row])
        except Exception:
            log.error("audit write failed", extra={"audit_lost": True}, exc_info=True)
            return
        if errors:
            log.error("audit write rejected", extra={"audit_lost": True, "n_errors": len(errors)})
