"""
USGS Mineral Resources Data System (MRDS) Connector

Fetches mineral deposit records via the USGS MRDS WFS service.
Endpoint: https://mrdata.usgs.gov/services/mrds

WFS GetFeature request returns GML/JSON with deposit locations,
commodity info, deposit type, and development status.

TODO:
- Implement pagination via WFS startIndex
- Parse all commodity fields (primary + secondary)
- Handle coordinate reference system transformations
- Map MRDS deposit_type codes to normalized vocabulary
"""
from typing import Any, Dict, List, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature


class USGSMRDSConnector(BaseConnector):
    BASE_URL = "https://mrdata.usgs.gov/services/mrds"

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch MRDS records via WFS GetFeature.
        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84
        """
        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": "mrds",
            "outputFormat": "application/json",
            "maxFeatures": 1000,
        }
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            params["bbox"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"

        data = await self._get(self.BASE_URL, params=params)
        return data.get("features", [])

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Map MRDS GeoJSON features to Feature ORM objects.

        MRDS field mapping:
        - dep_id → source_record_id
        - site_name → name
        - commod1 → commodity_primary
        - commod2, commod3 → commodity_secondary
        - dev_stat → status
        - dep_type → deposit_type
        - geometry.coordinates → geometry (Point)
        """
        features = []
        for record in raw_records:
            props = record.get("properties", {})
            geom = record.get("geometry")

            if not geom or geom.get("type") != "Point":
                continue

            lon, lat = geom["coordinates"][:2]
            shapely_point = Point(lon, lat)

            secondary = [
                c for c in [props.get("commod2"), props.get("commod3")] if c
            ]

            feature = Feature(
                source_channel=self.channel_config.name,
                source_record_id=str(props.get("dep_id", "")),
                feature_type="deposit",
                name=props.get("site_name"),
                commodity_primary=props.get("commod1"),
                commodity_secondary=secondary or None,
                deposit_type=props.get("dep_type"),
                status=props.get("dev_stat"),
                geometry=from_shape(shapely_point, srid=4326),
                source_quality=0.8,  # USGS MRDS is a high-quality authoritative source
            )
            features.append(feature)

        return features
