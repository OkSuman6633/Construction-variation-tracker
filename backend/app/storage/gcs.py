"""Google Cloud Storage adapter (production). Requires ADC / workload identity.

STATUS: implemented but NOT verified against a live bucket from the build sandbox.
"""

from __future__ import annotations

from google.api_core import exceptions as gexc
from google.cloud import storage  # type: ignore[attr-defined]

from .base import ObjectNotFound, PreconditionFailed, StoredObject


class GcsObjectStore:
    def __init__(self, bucket: str, project: str | None = None) -> None:
        self._client = storage.Client(project=project)
        self._bucket = self._client.bucket(bucket)

    def get(self, path: str) -> StoredObject:
        blob = self._bucket.get_blob(path)
        if blob is None:
            raise ObjectNotFound(path)
        try:
            data = blob.download_as_bytes(if_generation_match=blob.generation)
        except gexc.NotFound as exc:
            raise ObjectNotFound(path) from exc
        except gexc.PreconditionFailed as exc:
            raise PreconditionFailed(path) from exc
        return StoredObject(data, int(blob.generation))

    def put(self, path: str, data: bytes, *, if_generation_match: int | None = None) -> int:
        blob = self._bucket.blob(path)
        try:
            blob.upload_from_string(
                data,
                content_type="application/octet-stream",
                if_generation_match=if_generation_match,
            )
        except gexc.PreconditionFailed as exc:
            raise PreconditionFailed(path) from exc
        return int(blob.generation)

    def list(self, prefix: str) -> list[str]:
        return [b.name for b in self._client.list_blobs(self._bucket, prefix=prefix)]

    def delete(self, path: str) -> None:
        try:
            self._bucket.blob(path).delete()
        except gexc.NotFound:
            return


def _signed_upload_url(self, path: str, mime: str, size: int, ttl):  # type: ignore[no-untyped-def]
    """V4 signed PUT URL: create-only (generation 0), size-capped, short-lived.

    STATUS: NOT verified against live GCS; requires credentials able to sign (service account /
    IAM signBlob). The headers returned MUST be sent by the client or the signature fails.
    """
    signed = {"x-goog-if-generation-match": "0", "x-goog-content-length-range": f"0,{size}"}
    url = self._bucket.blob(path).generate_signed_url(
        version="v4", expiration=ttl, method="PUT", content_type=mime, headers=signed
    )
    return url, {"Content-Type": mime, **signed}


GcsObjectStore.signed_upload_url = _signed_upload_url  # type: ignore[attr-defined]
