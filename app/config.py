from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All credentials come from local .env or Google Secret Manager at deploy time."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    database_url: str
    google_cloud_project: str = "bond-agent-509210"
    google_cloud_region: str = "asia-east1"
    gcs_bucket: str = ""
    task_queue: str = "bond-agent-jobs"
    tasks_service_url: str = ""
    tasks_invoker_service_account: str = ""
    tasks_shared_secret: str = "change-me-before-local-use"

    local_storage_dir: Path = Path("/data/uploads")
    max_file_size_mb: int = Field(default=20, ge=1, le=100)
    max_pdf_pages: int = Field(default=80, ge=1, le=500)
    ocr_dpi: int = Field(default=200, ge=144, le=300)
    project_retention_days: int = Field(default=30, ge=1, le=365)

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    vertex_ai_location: str = "global"
    vertex_ai_embedding_model: str = "gemini-embedding-001"
    # Keep this aligned with pgvector's Vector(1024) column.  Gemini's native
    # vector is longer, but Vertex AI can produce a smaller retrieval vector.
    embedding_dimension: int = Field(default=1024, ge=128, le=3072)

    @property
    def is_cloud(self) -> bool:
        return self.app_env.lower() in {"production", "cloudrun", "gcp"}

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
