"""Idempotency for every write: claim-then-complete using GCS create-if-absent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from ..errors import AppError
from ..storage import paths
from ..storage.base import ObjectStore, PreconditionFailed
from .membership import key_hash

Handler = Callable[[], tuple[int, dict[str, Any]]]


def fingerprint(method: str, path: str, body: Any) -> str:
    canon = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{method}|{path}|{canon}".encode()).hexdigest()


def run_idempotent(
    store: ObjectStore, tenant_id: str, key: str | None, fp: str, handler: Handler
) -> tuple[int, dict[str, Any], bool]:
    """Return (status, body, replayed)."""
    if not key or not (8 <= len(key) <= 128):
        raise AppError(
            "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header (8-128 chars) is required.", 400
        )
    path = paths.idempotency(tenant_id, key_hash(tenant_id, key))
    claim = json.dumps({"state": "in_progress", "fingerprint": fp}).encode()
    try:
        gen = store.put(path, claim, if_generation_match=0)
    except PreconditionFailed:
        existing = json.loads(store.get(path).data)
        if existing["fingerprint"] != fp:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "This Idempotency-Key was already used with a different request.",
                422,
            ) from None
        if existing["state"] == "done":
            return existing["status"], existing["body"], True
        raise AppError(
            "IDEMPOTENCY_IN_PROGRESS", "The original request is still being processed.", 409, True
        ) from None
    try:
        status, body = handler()
    except Exception:
        store.delete(path)  # failed attempts may be retried with the same key
        raise
    body = json.loads(json.dumps(body, default=str))
    done = json.dumps({"state": "done", "fingerprint": fp, "status": status, "body": body}).encode()
    store.put(path, done, if_generation_match=gen)
    return status, body, False
