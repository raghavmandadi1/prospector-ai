"""
Base class for all GeoProspector data source connectors.

Each connector handles one external data source:
1. fetch() — retrieve raw records from the source (paginated, bbox-filtered)
2. normalize() — transform raw records into Feature ORM objects

To add a new connector:
    1. Create a new file in app/connectors/
    2. Subclass BaseConnector
    3. Implement fetch() and normalize()
    4. Register a new Channel record with source_type = your connector key
    5. Map the key in the CONNECTOR_REGISTRY in pipeline/ingest.py
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from app.models.channel import Channel
from app.models.feature import Feature


class BaseConnector(ABC):
    def __init__(self, channel_config: Channel):
        self.channel_config = channel_config
        self.endpoint = channel_config.endpoint
        self.auth_config = channel_config.auth_config or {}

    @abstractmethod
    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch raw records from the data source.

        Args:
            bbox: Optional (min_lon, min_lat, max_lon, max_lat) spatial filter

        Returns:
            List of raw record dicts as returned by the source API/file
        """
        raise NotImplementedError

    @abstractmethod
    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Transform raw records into Feature ORM instances ready for upsert.

        Each Feature must have at minimum:
        - source_channel: the channel name
        - source_record_id: the upstream record identifier
        - geometry: a WKT or GeoAlchemy2-compatible geometry value

        Returns:
            List of Feature ORM instances (not yet committed to DB)
        """
        raise NotImplementedError

    async def _get(self, url: str, params: Dict = None, headers: Dict = None) -> Any:
        """Shared HTTP GET helper with timeout and error handling."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=headers or {})
            response.raise_for_status()
            return response.json()
