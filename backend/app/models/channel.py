import uuid
from datetime import datetime

from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base import Base


class Channel(Base):
    """
    A data source channel — defines where and how to fetch geological data.
    Each channel maps to a specific connector class in app/connectors/.
    """
    __tablename__ = "channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Human-readable name, e.g. "USGS MRDS", "Macrostrat Lithology"
    name = Column(String(200), nullable=False, unique=True)

    # Connector type key used to instantiate the right connector class
    # e.g. "usgs_mrds", "macrostrat", "mindat"
    source_type = Column(String(100), nullable=False)

    # Base endpoint URL for the data source
    endpoint = Column(String(500), nullable=True)

    # Encrypted/obfuscated auth credentials stored as JSONB
    # e.g. {"api_key": "...", "token": "..."}
    auth_config = Column(JSONB, nullable=True)

    # Cron expression for scheduled refresh, e.g. "0 3 * * *"
    refresh_schedule = Column(String(100), nullable=True)

    # Geographic bounding box or coverage description as JSONB
    # e.g. {"bbox": [-180, -90, 180, 90], "regions": ["CONUS"]}
    spatial_coverage = Column(JSONB, nullable=True)

    # Category of data this channel provides
    # e.g. "mining_records", "geochemistry", "lithology", "remote_sensing"
    data_type = Column(String(100), nullable=True)

    # Name of the normalization profile / schema applied to raw records
    normalization_profile = Column(String(100), nullable=True)

    last_synced_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<Channel id={self.id} name={self.name!r} type={self.source_type}>"
