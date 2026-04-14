import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base import Base


class AnalysisJob(Base):
    """
    An analysis run over a user-defined AOI (area of interest).
    Tracks job lifecycle from queued → running → completed / failed.
    Stores intermediate agent results and final scored grid as JSONB.
    """
    __tablename__ = "analysis_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Lifecycle: queued | running | completed | failed
    status = Column(String(50), nullable=False, default="queued", index=True)

    # User-provided AOI as GeoJSON FeatureCollection / Polygon
    aoi_geojson = Column(JSONB, nullable=False)

    # Target mineral (e.g. "gold", "copper", "silver")
    target_mineral = Column(String(100), nullable=False, index=True)

    # Analysis config: resolution, agent weights, enabled agents, etc.
    config = Column(JSONB, nullable=True)

    # Per-agent results: keyed by agent_id, value is AgentResult JSON
    agent_results = Column(JSONB, nullable=True)

    # Final scored grid cells after synthesis: list of ScoredCell JSON
    final_scores = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    def __repr__(self):
        return (
            f"<AnalysisJob id={self.id} mineral={self.target_mineral!r} "
            f"status={self.status!r}>"
        )
