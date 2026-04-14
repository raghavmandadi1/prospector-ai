import uuid
from datetime import datetime

from sqlalchemy import Column, String, Float, DateTime, Text, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geometry

from app.models.base import Base


class Feature(Base):
    """
    A geospatial feature ingested from a data source (mine, deposit, outcrop,
    geochemical sample, etc.). Geometry can be either a Point or Polygon.
    """
    __tablename__ = "features"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Source tracking
    source_channel = Column(String(100), nullable=False, index=True)
    source_record_id = Column(String(255), nullable=True)  # ID in the upstream system
    raw_record_ref = Column(String(500), nullable=True)    # S3 key or URL to raw record

    # Classification
    feature_type = Column(String(100), nullable=True, index=True)  # mine, prospect, outcrop, sample, etc.
    name = Column(String(500), nullable=True)
    deposit_type = Column(String(200), nullable=True)
    status = Column(String(100), nullable=True)  # active, historic, prospect, occurrence

    # Geology
    geologic_unit = Column(String(200), nullable=True)
    rock_type = Column(String(200), nullable=True)

    # Commodities
    commodity_primary = Column(String(100), nullable=True, index=True)
    commodity_secondary = Column(ARRAY(String), nullable=True)

    # Geometry — stored as EWKB; use WGS84 (SRID 4326)
    geometry = Column(Geometry(geometry_type="GEOMETRY", srid=4326), nullable=True)

    # Geochemical data as flexible JSONB: {"Au_ppb": 1200, "Ag_ppm": 5.3, ...}
    geochemical_values = Column(JSONB, nullable=True)

    # Quality score [0.0–1.0] based on source reliability and completeness
    source_quality = Column(Float, nullable=True)

    ingested_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Feature id={self.id} name={self.name!r} type={self.feature_type}>"
