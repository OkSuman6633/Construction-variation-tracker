"""Task queue abstraction. Task name == job_id, so a repeated enqueue is de-duplicated."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any, Protocol


class TaskQueue(Protocol):
    def enqueue(self, tenant_id: str, job_id: str) -> None: ...


class ManualQueue:
    """TEST adapter: records tasks; the test (or a script) executes them explicitly."""

    def __init__(self) -> None:
        self.tasks: list[tuple[str, str]] = []

    def enqueue(self, tenant_id: str, job_id: str) -> None:
        if (tenant_id, job_id) not in self.tasks:  # dedupe by task name
            self.tasks.append((tenant_id, job_id))


class InlineThreadQueue:
    """LOCAL DEV adapter: runs the worker in a background thread of the API process."""

    def __init__(self, runner: Callable[[str, str], Any]) -> None:
        self._runner = runner
        self._seen: set[tuple[str, str]] = set()

    def enqueue(self, tenant_id: str, job_id: str) -> None:
        if (tenant_id, job_id) in self._seen:
            return
        self._seen.add((tenant_id, job_id))
        threading.Thread(target=self._runner, args=(tenant_id, job_id), daemon=True).start()


class CloudTasksQueue:
    """Production adapter. STATUS: implemented, NOT verified against live Cloud Tasks.

    Deterministic task name (the job id) gives at-most-once creation per job; the worker is
    additionally idempotent, so delivery retries are safe.
    """

    def __init__(
        self, project: str, location: str, queue: str, worker_url: str, service_account: str
    ) -> None:
        from google.cloud import tasks_v2

        self._tasks = tasks_v2
        self._client = tasks_v2.CloudTasksClient()
        self._parent = self._client.queue_path(project, location, queue)
        self._worker_url = worker_url.rstrip("/") + "/internal/tasks/analyse"
        self._sa = service_account

    def enqueue(self, tenant_id: str, job_id: str) -> None:
        from google.api_core import exceptions as gexc
        from google.protobuf import duration_pb2

        task = {
            "name": f"{self._parent}/tasks/{job_id}",
            "dispatch_deadline": duration_pb2.Duration(seconds=900),
            "http_request": {
                "http_method": self._tasks.HttpMethod.POST,
                "url": self._worker_url,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"tenant_id": tenant_id, "job_id": job_id}).encode(),
                "oidc_token": {"service_account_email": self._sa, "audience": self._worker_url},
            },
        }
        try:
            self._client.create_task(request={"parent": self._parent, "task": task})
        except gexc.AlreadyExists:
            return
