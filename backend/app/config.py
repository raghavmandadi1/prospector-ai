from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    app_env: str = "development"
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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
