from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.channel import Channel

router = APIRouter(prefix="/channels", tags=["channels"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ChannelCreate(BaseModel):
    name: str
    source_type: str
    endpoint: str | None = None
    auth_config: dict | None = None
    refresh_schedule: str | None = None
    spatial_coverage: dict | None = None
    data_type: str | None = None
    normalization_profile: str | None = None


class ChannelOut(BaseModel):
    id: UUID
    name: str
    source_type: str
    endpoint: str | None
    data_type: str | None
    is_active: bool
    last_synced_at: str | None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, db: AsyncSession = Depends(get_db)):
    """
    Register a new data source channel.
    The channel is not synced immediately — call /channels/{id}/sync to trigger a fetch.
    """
    channel = Channel(**body.model_dump())
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel


@router.get("", response_model=List[ChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)):
    """Return all registered channels ordered by name."""
    result = await db.execute(select(Channel).order_by(Channel.name))
    return result.scalars().all()


@router.post("/{channel_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_channel_sync(channel_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Enqueue a Celery task to sync (fetch + normalize + upsert) the channel.
    Returns the Celery task ID for polling.
    """
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Import here to avoid circular imports at module level
    from app.pipeline.ingest import sync_channel
    task = sync_channel.delay(str(channel_id))

    return {"task_id": task.id, "channel_id": str(channel_id), "status": "queued"}
