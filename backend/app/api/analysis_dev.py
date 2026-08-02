"""
Dev-mode analysis endpoint.

Runs the full multi-agent analysis pipeline in-process (no Celery, no Redis,
no PostGIS). Streams SSE events directly back to the client.

Only mounted when DEV_MODE=true in settings.
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.orchestrator import OrchestratorAgent
from app.scoring.grid import generate_grid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis-dev"])

# How long the SSE generator waits on the event queue before checking whether
# the client is still there. Agents can go minutes between events, so without
# this poll a disconnect is not noticed until the *next* event arrives — which
# is why aborting the fetch used to leave the run burning tokens.
DISCONNECT_POLL_SECONDS = 1.0

# Comment frames sent during quiet periods. Two jobs: they keep proxies from
# idling the connection out, and the failed write is how uvicorn learns the
# socket is gone on platforms where is_disconnected() alone is not enough.
HEARTBEAT_SECONDS = 15.0


class DevAnalysisRequest(BaseModel):
    aoi_geojson: dict
    target_mineral: str
    config: Optional[dict] = None
    anthropic_api_key: str  # Required in dev mode — comes from the UI


@router.post("/jobs", status_code=200)
async def run_analysis_dev(request: Request, body: DevAnalysisRequest):
    """
    Dev-mode: run analysis in-process and stream SSE events.

    Unlike the production endpoint, this:
    - Does NOT persist to a database
    - Does NOT use Celery or Redis
    - Streams results directly via SSE
    - Requires the API key in the request body

    Cancellation: the client aborts the fetch, the generator notices the
    disconnect on its next poll, and the `finally` cancels the pipeline task.
    That cancellation propagates through the orchestrator's asyncio.gather
    into each agent, killing in-flight Anthropic requests.
    """
    job_id = str(uuid4())
    config = body.config or {}

    # Event queue for SSE streaming
    event_queue: asyncio.Queue = asyncio.Queue()

    async def emit_fn(payload: Dict):
        """Push events to the SSE queue."""
        await event_queue.put(payload)

    async def run_pipeline():
        """Background task that runs the orchestrator."""
        try:
            orchestrator = OrchestratorAgent(api_key=body.anthropic_api_key)
            final_scores, agent_results = await orchestrator.run_analysis(
                job_id=job_id,
                aoi_geojson=body.aoi_geojson,
                target_mineral=body.target_mineral,
                config=config,
                emit_fn=emit_fn,
            )
            # Send final results as a special event
            await event_queue.put({
                "event": "results",
                "final_scores": final_scores,
                "agent_results": agent_results,
            })
        except asyncio.CancelledError:
            # Client stopped the run. Nothing to report — the socket is gone.
            logger.info(f"[{job_id}] Dev analysis cancelled by client")
            raise
        except Exception as exc:
            logger.exception(f"Dev analysis failed: {exc}")
            await event_queue.put({"event": "error", "message": str(exc)})
        finally:
            # Signal end of stream
            await event_queue.put(None)

    async def event_generator():
        """Yield SSE events from the queue, polling for client disconnect.

        The queue wait is bounded rather than open-ended: an agent can run for
        minutes without emitting, and a generator parked on an unbounded
        `queue.get()` never returns control, so a client that has already gone
        away keeps a full analysis running.
        """
        task = asyncio.create_task(run_pipeline())
        last_heartbeat = time.monotonic()

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        event_queue.get(), timeout=DISCONNECT_POLL_SECONDS
                    )
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        logger.info(f"[{job_id}] Client disconnected — stopping run")
                        break
                    if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                        last_heartbeat = time.monotonic()
                        yield ": keepalive\n\n"
                    continue

                if event is None:
                    break
                last_heartbeat = time.monotonic()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if not task.done():
                task.cancel()
                # Give the cancellation a moment to unwind so in-flight
                # Anthropic requests are torn down here rather than by the
                # garbage collector. Bounded and shielded: this runs inside a
                # possibly-already-cancelled scope during generator close, and
                # blocking here would stall the response teardown.
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/jobs/{job_id}")
async def get_job_dev(job_id: str):
    """
    Dev-mode stub: job status is embedded in the SSE stream,
    so this endpoint returns a minimal placeholder.
    """
    return {
        "id": job_id,
        "status": "dev_mode",
        "message": "In dev mode, results are streamed directly via SSE on the POST response.",
    }


@router.get("/jobs/{job_id}/events")
async def job_events_dev(job_id: str):
    """
    Dev-mode stub: events are streamed from the POST endpoint directly.
    This is here so the frontend SSE subscription doesn't 404.
    """
    async def empty_stream():
        yield f"data: {json.dumps({'event': 'error', 'message': 'In dev mode, subscribe to the POST response stream instead.'})}\n\n"

    return StreamingResponse(
        empty_stream(),
        media_type="text/event-stream",
    )
