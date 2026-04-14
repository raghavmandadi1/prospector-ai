"""
Celery application entry point.

This module is the import target for the Celery worker process:
    celery -A celery_worker worker --loglevel=info

All tasks are registered via imports in app/pipeline/ingest.py.
The worker must be started from the backend/ directory so that
app.* imports resolve correctly.
"""
from celery import Celery
from app.config import settings

celery_app = Celery(
    "geoprospector",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.pipeline.ingest"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # Prevent worker from pre-fetching long-running tasks
    task_acks_late=True,           # Acknowledge only after task completes (safer)
)
