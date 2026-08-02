from pathlib import Path

from pydantic_settings import BaseSettings
from typing import List

# Repository root — backend/app/config.py → backend/app → backend → repo root.
# Run records and the cell cache live under data/ relative to this, so they land
# in the same place whether the backend is started from the repo root (run-dev.sh
# cds into backend/) or from anywhere else.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
CACHE_DIR = DATA_DIR / "cache"


class Settings(BaseSettings):
    app_env: str = "development"
    dev_mode: bool = True  # When True, run analysis in-process (no Celery/Redis/PostGIS)
    secret_key: str = "change-this-secret-key"

    # Database
    database_url: str = "postgresql+asyncpg://geoprospector:changeme@localhost:5432/geoprospector"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Anthropic
    anthropic_api_key: str = ""

    # CORS
    cors_origins: List[str] = ["http://localhost:5173"]

    # MinIO / S3
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "geoprospector"

    # External API keys
    mindat_api_key: str = ""

    # --- Run records (data/runs/) -------------------------------------------
    # One immutable JSON file per analysis: provenance, inputs, outputs. Works
    # under DEV_MODE — files on disk, no Postgres.
    save_run_records: bool = True
    # Keep every raw LLM response in the run record. The only way to diagnose
    # "that score looks wrong" after the fact, and what lets historical runs be
    # re-parsed if parse_llm_response() is fixed. Costs disk, nothing else.
    save_raw_llm: bool = True

    # --- Cell cache (data/cache/cells.sqlite) --------------------------------
    # Per-cell, per-agent score cache keyed on everything that could change the
    # answer (model, prompt version, knowledge file hash, spatial context hash).
    cache_enabled: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
