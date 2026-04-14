"""
BLM Mining Claims and Operations (MLRS) Connector

Fetches active and closed mining claim records from the BLM Master
Leasing and Realty System.
Endpoint: https://mlrs.blm.gov

Note: MLRS does not expose a public API — data is obtained via bulk
download files (CSV) updated monthly. This connector downloads and
parses the latest snapshot.

TODO:
- Locate current BLM MLRS bulk download URL
- Implement CSV parsing and geometry construction from township/range
  or lat/lon fields
- Handle coordinate conversion from PLSS to WGS84
"""
from typing import Any, Dict, List, Optional

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature


class BLMMLRSConnector(BaseConnector):
    BASE_URL = "https://mlrs.blm.gov"

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Download BLM MLRS bulk export and filter by bbox.

        TODO: Implement actual download from BLM MLRS portal.
        The endpoint typically requires navigating to:
        https://mlrs.blm.gov/statistics/report or downloading the
        LR2000 extract files.
        """
        # Stub: return empty list until actual download logic is implemented
        return []

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Map MLRS claim records to Feature ORM objects.

        Expected fields from MLRS CSV:
        - CASE_SEQ_NBR → source_record_id
        - MRRC_NAME → name
        - COMMODITY → commodity_primary
        - CASE_STATUS → status
        - LOC_LON, LOC_LAT → geometry
        """
        features = []
        for record in raw_records:
            # TODO: Implement field mapping once CSV format is confirmed
            pass
        return features
