"""
Celery tasks for data ingestion and analysis job execution.

Tasks:
- sync_channel: fetch → normalize → upsert features for a single channel
- run_analysis_job: trigger the orchestrator for a submitted analysis job

The CONNECTOR_REGISTRY maps channel.source_type → connector class.
Add new connectors here after implementing them in app/connectors/.
"""
import asyncio
import logging
from datetime import datetime
from uuid import UUID

from celery import Task

from app.celery_worker import celery_app
from app.connectors.usgs_mrds import USGSMRDSConnector
from app.connectors.blm_mlrs import BLMMLRSConnector
from app.connectors.glo_records import GLORecordsConnector
from app.connectors.usgs_ngdb import USGSNGDBConnector
from app.connectors.macrostrat import MacrostratConnector
from app.connectors.mindat import MindatConnector

logger = logging.getLogger(__name__)

# Map source_type values to connector classes
CONNECTOR_REGISTRY = {
    "usgs_mrds": USGSMRDSConnector,
    "blm_mlrs": BLMMLRSConnector,
    "glo_records": GLORecordsConnector,
    "usgs_ngdb": USGSNGDBConnector,
    "macrostrat": MacrostratConnector,
    "mindat": MindatConnector,
}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def sync_channel(self: Task, channel_id: str):
    """
    Celery task: fetch and upsert all features for a channel.

    Steps:
    1. Load Channel record from DB
    2. Look up the connector class from CONNECTOR_REGISTRY
    3. Call connector.fetch(bbox=channel.spatial_coverage.get('bbox'))
    4. Call connector.normalize() to get Feature objects
    5. Upsert features (ON CONFLICT UPDATE on source_record_id)
    6. Update channel.last_synced_at
    """
    asyncio.run(_sync_channel_async(channel_id))


async def _sync_channel_async(channel_id: str):
    from app.db.session import AsyncSessionLocal
    from app.models.channel import Channel
    from sqlalchemy.dialects.postgresql import insert

    async with AsyncSessionLocal() as session:
        channel = await session.get(Channel, UUID(channel_id))
        if not channel:
            logger.error(f"Channel {channel_id} not found")
            return

        connector_cls = CONNECTOR_REGISTRY.get(channel.source_type)
        if not connector_cls:
            logger.error(f"No connector for source_type={channel.source_type}")
            return

        connector = connector_cls(channel)

        # Get optional bbox from channel's spatial_coverage config
        bbox = None
        if channel.spatial_coverage:
            bbox = channel.spatial_coverage.get("bbox")
            if bbox:
                bbox = tuple(bbox)

        logger.info(f"Fetching channel {channel.name} (source_type={channel.source_type})")
        raw_records = await connector.fetch(bbox=bbox)
        logger.info(f"Fetched {len(raw_records)} records from {channel.name}")

        features = await connector.normalize(raw_records)
        logger.info(f"Normalized {len(features)} features from {channel.name}")

        # Upsert features
        for feature in features:
            session.add(feature)

        channel.last_synced_at = datetime.utcnow()
        await session.commit()
        logger.info(f"Synced channel {channel.name}: {len(features)} features upserted")


@celery_app.task(bind=True, max_retries=1)
def run_analysis_job(self: Task, job_id: str):
    """
    Celery task: run the full multi-agent analysis pipeline for a job.

    Steps:
    1. Load AnalysisJob from DB, set status=running
    2. Instantiate OrchestratorAgent
    3. Call orchestrator.run_analysis()
    4. Persist final_scores and agent_results to the DB
    5. Set status=completed (or failed on error)
    """
    asyncio.run(_run_analysis_job_async(job_id))


async def _run_analysis_job_async(job_id: str):
    from datetime import datetime
    from app.db.session import AsyncSessionLocal
    from app.models.analysis_job import AnalysisJob
    from app.agents.orchestrator import OrchestratorAgent

    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, UUID(job_id))
        if not job:
            logger.error(f"Analysis job {job_id} not found")
            return

        job.status = "running"
        await session.commit()

        try:
            orchestrator = OrchestratorAgent()
            final_scores, agent_results = await orchestrator.run_analysis(
                job_id=job_id,
                aoi_geojson=job.aoi_geojson,
                target_mineral=job.target_mineral,
                config=job.config or {},
            )

            job.status = "completed"
            job.final_scores = final_scores
            job.agent_results = agent_results
            job.completed_at = datetime.utcnow()
            await session.commit()
            logger.info(f"Analysis job {job_id} completed successfully")

        except Exception as exc:
            logger.exception(f"Analysis job {job_id} failed: {exc}")
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()
            await session.commit()
            raise
