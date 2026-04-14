import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.analysis_job import AnalysisJob

router = APIRouter(prefix="/analysis", tags=["analysis"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class AnalysisJobCreate(BaseModel):
    aoi_geojson: dict  # GeoJSON Feature or FeatureCollection
    target_mineral: str
    config: dict | None = None  # Optional: resolution_m, weights, enabled_agents


class AnalysisJobOut(BaseModel):
    id: UUID
    status: str
    target_mineral: str
    aoi_geojson: dict
    config: dict | None
    agent_results: dict | None
    final_scores: dict | None
    created_at: str
    completed_at: str | None
    error_message: str | None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs", response_model=AnalysisJobOut, status_code=201)
async def create_analysis_job(
    body: AnalysisJobCreate, db: AsyncSession = Depends(get_db)
):
    """
    Create a new analysis job and enqueue it for processing.
    The job immediately transitions to 'queued' status; the Celery worker
    will update it as agents run.
    """
    job = AnalysisJob(
        aoi_geojson=body.aoi_geojson,
        target_mineral=body.target_mineral,
        config=body.config or {},
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue the Celery analysis task
    from app.pipeline.ingest import run_analysis_job
    run_analysis_job.delay(str(job.id))

    return _job_to_dict(job)


@router.get("/jobs/{job_id}", response_model=AnalysisJobOut)
async def get_analysis_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Return current status and results for an analysis job."""
    job = await db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return _job_to_dict(job)


@router.get("/jobs/{job_id}/events")
async def analysis_job_events(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Server-Sent Events (SSE) endpoint for real-time agent progress updates.

    Clients subscribe with:
        const es = new EventSource('/api/v1/analysis/jobs/{id}/events')
        es.onmessage = (e) => console.log(JSON.parse(e.data))

    Events have the shape:
        { "event": "agent_complete", "agent_id": "lithology", "status": "completed" }
        { "event": "job_complete", "job_id": "...", "status": "completed" }
        { "event": "error", "message": "..." }
    """
    job = await db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    async def event_generator():
        """
        Poll the job status from Redis pub/sub (or directly from DB) and
        emit SSE messages.

        TODO: Replace DB polling with Redis pub/sub for lower latency.
        The OrchestratorAgent should publish progress events to a Redis
        channel named f"job:{job_id}:events".
        """
        import redis.asyncio as aioredis
        from app.config import settings

        red = aioredis.from_url(settings.redis_url)
        pubsub = red.pubsub()
        channel = f"job:{job_id}:events"
        await pubsub.subscribe(channel)

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    yield f"data: {data}\n\n"
                    parsed = json.loads(data)
                    # Close stream when job terminal event arrives
                    if parsed.get("event") in ("job_complete", "error"):
                        break
        finally:
            await pubsub.unsubscribe(channel)
            await red.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_to_dict(job: AnalysisJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "target_mineral": job.target_mineral,
        "aoi_geojson": job.aoi_geojson,
        "config": job.config,
        "agent_results": job.agent_results,
        "final_scores": job.final_scores,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
    }
