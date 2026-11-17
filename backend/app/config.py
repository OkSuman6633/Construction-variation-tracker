"""Environment-driven settings. No secrets are defaulted; none are logged."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env", REPOSITORY_ROOT / ".env"),
        extra="ignore",
    )

    app_env: Literal["local", "test", "dev", "staging", "production"] = "local"

    # "local"/"jsonl" are dev/test adapters and are refused in staging/production.
    storage_backend: Literal["local", "sqlite", "gcs"] = "sqlite"
    local_data_dir: str = ".local-data"
    sqlite_database_path: str | None = None
    gcs_bucket: str | None = None
    audit_backend: Literal["jsonl", "sqlite", "bigquery"] = "sqlite"
    bigquery_dataset: str | None = None
    gcp_project: str | None = None

    # Provider-agnostic OIDC. The HS256 dev secret is honoured only in local/test.
    auth_issuer: str | None = "cvea-local"
    auth_audience: str | None = "cvea-local"
    auth_jwks_url: str | None = None
    auth_dev_hs256_secret: SecretStr | None = None

    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Gemini via ADK. Never exposed to the frontend or in API responses.
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    google_genai_use_vertexai: bool = False

    # Evidence ingestion / processing
    scanner_backend: Literal["none", "dev_noop"] = "dev_noop"  # local-only, no malware scan
    max_upload_bytes: int = 25 * 1024 * 1024  # default; Doc 01 lists max size as an assumption
    task_queue_backend: Literal["manual", "inline_thread", "cloud_tasks"] = "inline_thread"
    gcp_location: str = "europe-west2"
    tasks_queue: str | None = None
    worker_url: str | None = None
    task_service_account: str | None = None
    max_job_attempts: int = 3
    job_timeout_seconds: float = 900.0

    rate_limit_per_minute: int = Field(default=120, ge=1)
    max_json_body_bytes: int = 1_000_000

    def assert_safe_for_env(self) -> None:
        """Fail fast on configurations that are unsafe outside local development."""
        if self.app_env in {"staging", "production"}:
            problems = []
            if self.storage_backend != "gcs":
                problems.append("STORAGE_BACKEND must be gcs")
            if self.audit_backend != "bigquery":
                problems.append("AUDIT_BACKEND must be bigquery")
            if self.scanner_backend == "dev_noop":
                problems.append("SCANNER_BACKEND=dev_noop performs no scan and is dev-only")
            if self.task_queue_backend != "cloud_tasks":
                problems.append("TASK_QUEUE_BACKEND must be cloud_tasks")
            if self.auth_dev_hs256_secret is not None:
                problems.append("AUTH_DEV_HS256_SECRET must not be set")
            if not (self.auth_issuer and self.auth_audience and self.auth_jwks_url):
                problems.append("AUTH_ISSUER, AUTH_AUDIENCE and AUTH_JWKS_URL are required")
            if problems:
                raise RuntimeError("Unsafe configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
