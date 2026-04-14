"""
USGS National Geochemical Database (NGDB) Connector

Fetches stream sediment, soil, and rock geochemical sample data.
Endpoint: https://mrdata.usgs.gov/geochem

Contains ~3 million samples across the US with multi-element analysis
(Au, Ag, Cu, Pb, Zn, As, Sb, Hg, Mo, etc.)

TODO:
- Implement WFS GetFeature or REST query
- Map element field names to normalized schema
- Handle unit conversions (ppb vs ppm vs %)
- Implement efficient spatial query with pagination
"""
from typing import Any, Dict, List, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature


class USGSNGDBConnector(BaseConnector):
    BASE_URL = "https://mrdata.usgs.gov/geochem"

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch geochemical sample points within bbox.

        The NGDB WFS service endpoint:
        GET https://mrdata.usgs.gov/geochem?service=WFS&request=GetFeature
            &typeName=ngdb&outputFormat=application/json&bbox=...
        """
        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": "geochem",
            "outputFormat": "application/json",
            "maxFeatures": 2000,
        }
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            params["bbox"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"

        data = await self._get(self.BASE_URL, params=params)
        return data.get("features", [])

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Map NGDB sample records to Feature objects.
        Store all element values in geochemical_values JSONB.

        Key NGDB fields:
        - samp_id → source_record_id
        - gold_ppm, silver_ppm, copper_ppm, etc. → geochemical_values
        - rock_type → rock_type
        """
        features = []
        for record in raw_records:
            props = record.get("properties", {})
            geom = record.get("geometry")

            if not geom or geom.get("type") != "Point":
                continue

            lon, lat = geom["coordinates"][:2]

            # Collect all numeric element values into JSONB
            elem_keys = [k for k in props if k.endswith(("_ppm", "_ppb", "_pct"))]
            geochem = {k: props[k] for k in elem_keys if props.get(k) is not None}

            feature = Feature(
                source_channel=self.channel_config.name,
                source_record_id=str(props.get("samp_id", "")),
                feature_type="geochemical_sample",
                rock_type=props.get("rock_type"),
                geometry=from_shape(Point(lon, lat), srid=4326),
                geochemical_values=geochem if geochem else None,
                source_quality=0.85,
            )
            features.append(feature)

        return features
