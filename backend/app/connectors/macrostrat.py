"""
Macrostrat Geology Connector

Fetches bedrock geology unit data from the Macrostrat API.
Endpoint: https://macrostrat.org/api/v2

Macrostrat provides:
- Geologic map polygons with unit name, age, lithology
- Column data with stratigraphic context
- Lithology classifications

API docs: https://macrostrat.org/api/v2
"""
from typing import Any, Dict, List, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import shape

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature


class MacrostratConnector(BaseConnector):
    BASE_URL = "https://macrostrat.org/api/v2"

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch geologic map units intersecting bbox.

        Macrostrat geologic map endpoint:
        GET /geologic_units/map?lat=&lng= (point query)
        GET /geologic_units/map?bbox=minx,miny,maxx,maxy (bbox query)
        """
        params = {"format": "geojson"}
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            params["bbox"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"
        else:
            # Default to CONUS if no bbox provided
            params["bbox"] = "-125,24,-66,50"

        data = await self._get(f"{self.BASE_URL}/geologic_units/map", params=params)
        if isinstance(data, dict):
            return data.get("success", {}).get("data", {}).get("features", [])
        return []

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Map Macrostrat geologic unit features to Feature objects.

        Macrostrat GeoJSON fields:
        - map_id → source_record_id
        - unit_name, strat_name → name
        - lith → rock_type (primary lithology)
        - age → geologic_unit (age interval name)
        - b_age, t_age → age range in Ma
        """
        features = []
        for record in raw_records:
            props = record.get("properties", {})
            geom_data = record.get("geometry")
            if not geom_data:
                continue

            try:
                shapely_geom = shape(geom_data)
            except Exception:
                continue

            feature = Feature(
                source_channel=self.channel_config.name,
                source_record_id=str(props.get("map_id", "")),
                feature_type="geology_unit",
                name=props.get("unit_name") or props.get("strat_name"),
                geologic_unit=props.get("age"),
                rock_type=props.get("lith"),
                geometry=from_shape(shapely_geom, srid=4326),
                source_quality=0.9,
            )
            features.append(feature)

        return features
