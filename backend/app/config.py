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
#: Small, human-servable reference layers. Tracked in git.
REFERENCE_DIR = DATA_DIR / "reference"
#: Machine-built artifacts derived from data/raw/ by scripts/build_*.py.
#: Gitignored and absent on a fresh clone — every consumer must degrade.
DERIVED_DIR = DATA_DIR / "derived"
#: Imported field pins (scripts/import_field_pins.py). Gitignored.
USER_SITES_DIR = DATA_DIR / "user_sites"


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

    # --- Local spatial context (data/reference/ + data/derived/) -------------
    # The agents' evidence base. Read straight off disk rather than through
    # PostGIS, because the PostGIS path is dead under DEV_MODE (no asyncpg) and
    # DEV_MODE is the path everyone actually runs — see CLAUDE.md Known Gap #2.
    # Turning this off reproduces the old "LLM regional knowledge only" runs,
    # which is occasionally what you want when measuring what the data adds.
    local_context_enabled: bool = True
    # How far around a cell to look for recorded occurrences, kilometres. Also
    # the radius beyond which a cell is called a lead rather than a re-find.
    occurrence_search_radius_km: float = 5.0
    # Per-cell record caps. These bound prompt size, which bounds cost: a cell
    # in the middle of the Republic district can have dozens of occurrences and
    # the marginal ones do not change the score.
    max_records_per_cell: int = 6

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
