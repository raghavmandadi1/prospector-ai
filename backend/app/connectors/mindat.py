"""
Mindat.org Connector

Fetches mineral occurrence and locality data from the Mindat API.
Endpoint: https://api.mindat.org

Mindat is the world's largest mineral database with ~300,000 localities.
Requires an API key (free registration at mindat.org).

API docs: https://api.mindat.org/schema/redoc/
"""
from typing import Any, Dict, List, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature


class MindatConnector(BaseConnector):
    BASE_URL = "https://api.mindat.org"

    def _get_headers(self) -> Dict[str, str]:
        api_key = self.auth_config.get("api_key", "")
        return {"Authorization": f"Token {api_key}"}

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch mineral localities within bbox from Mindat API.

        Endpoint: GET /api/localities/?bbox=minlon,minlat,maxlon,maxlat
        Requires Authorization: Token <api_key> header.
        Returns paginated results — iterate through all pages.
        """
        params = {"format": "json", "page_size": 500}
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            params["bbox"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"

        headers = self._get_headers()
        all_records = []
        url = f"{self.BASE_URL}/api/localities/"

        # Paginate through results
        while url:
            data = await self._get(url, params=params, headers=headers)
            results = data.get("results", [])
            all_records.extend(results)
            url = data.get("next")  # None when no more pages
            params = {}  # next URL already has all params encoded

        return all_records

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Map Mindat locality records to Feature objects.

        Mindat fields:
        - id → source_record_id
        - name → name
        - latitude, longitude → geometry
        - mindat_formula / associated_minerals → commodity hints
        - description → raw notes
        """
        features = []
        for record in raw_records:
            lat = record.get("latitude")
            lon = record.get("longitude")
            if lat is None or lon is None:
                continue

            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                continue

            feature = Feature(
                source_channel=self.channel_config.name,
                source_record_id=str(record.get("id", "")),
                feature_type="mineral_locality",
                name=record.get("name"),
                geometry=from_shape(Point(lon, lat), srid=4326),
                source_quality=0.75,
            )
            features.append(feature)

        return features
