from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2.functions import ST_AsGeoJSON, ST_MakeEnvelope, ST_Within
import json

from app.db.session import get_db
from app.models.feature import Feature

router = APIRouter(prefix="/features", tags=["features"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_features(
    bbox: Optional[str] = Query(
        None,
        description="Bounding box as 'minLon,minLat,maxLon,maxLat' (WGS84)",
        example="-120,35,-115,40",
    ),
    commodity: Optional[str] = Query(None, description="Filter by primary commodity"),
    feature_type: Optional[str] = Query(None, description="Filter by feature type"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Query features and return a GeoJSON FeatureCollection.

    Supports filtering by:
    - bbox: spatial bounding box
    - commodity: primary mineral commodity (e.g. "gold")
    - feature_type: record type (e.g. "mine", "prospect", "sample")
    """
    query = select(Feature, ST_AsGeoJSON(Feature.geometry).label("geom_json"))

    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
            envelope = ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
            query = query.where(ST_Within(Feature.geometry, envelope))
        except ValueError:
            pass  # Ignore malformed bbox

    if commodity:
        query = query.where(Feature.commodity_primary.ilike(f"%{commodity}%"))

    if feature_type:
        query = query.where(Feature.feature_type == feature_type)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    rows = result.all()

    features = []
    for feat, geom_json in rows:
        geometry = json.loads(geom_json) if geom_json else None
        features.append(
            {
                "type": "Feature",
                "id": str(feat.id),
                "geometry": geometry,
                "properties": {
                    "source_channel": feat.source_channel,
                    "feature_type": feat.feature_type,
                    "name": feat.name,
                    "commodity_primary": feat.commodity_primary,
                    "commodity_secondary": feat.commodity_secondary,
                    "deposit_type": feat.deposit_type,
                    "status": feat.status,
                    "geologic_unit": feat.geologic_unit,
                    "rock_type": feat.rock_type,
                    "source_quality": feat.source_quality,
                    "ingested_at": feat.ingested_at.isoformat() if feat.ingested_at else None,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
        "offset": offset,
    }


@router.get("/{feature_id}")
async def get_feature(feature_id: UUID, db: AsyncSession = Depends(get_db)):
    """Return a single feature with full detail including geochemical values."""
    result = await db.execute(
        select(Feature, ST_AsGeoJSON(Feature.geometry).label("geom_json")).where(
            Feature.id == feature_id
        )
    )
    row = result.first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Feature not found")

    feat, geom_json = row
    geometry = json.loads(geom_json) if geom_json else None

    return {
        "type": "Feature",
        "id": str(feat.id),
        "geometry": geometry,
        "properties": {
            "source_channel": feat.source_channel,
            "source_record_id": feat.source_record_id,
            "feature_type": feat.feature_type,
            "name": feat.name,
            "commodity_primary": feat.commodity_primary,
            "commodity_secondary": feat.commodity_secondary,
            "deposit_type": feat.deposit_type,
            "status": feat.status,
            "geologic_unit": feat.geologic_unit,
            "rock_type": feat.rock_type,
            "geochemical_values": feat.geochemical_values,
            "source_quality": feat.source_quality,
            "ingested_at": feat.ingested_at.isoformat() if feat.ingested_at else None,
            "raw_record_ref": feat.raw_record_ref,
        },
    }
