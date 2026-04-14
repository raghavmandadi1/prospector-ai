"""
BLM General Land Office (GLO) Records Connector

Fetches historical GLO survey field notes and plat maps that may contain
references to mineral occurrences, vegetation, and terrain features.
Endpoint: https://glorecords.blm.gov

TODO:
- GLO Records is a document archive, not a structured API
- Implement full-text search via BLM's search endpoint
- Parse survey notes for mineral keywords
- Geocode PLSS descriptions to coordinates
"""
from typing import Any, Dict, List, Optional

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature


class GLORecordsConnector(BaseConnector):
    BASE_URL = "https://glorecords.blm.gov"
    SEARCH_URL = "https://glorecords.blm.gov/search/default.aspx"

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Search GLO Records for survey documents intersecting the bbox area.

        TODO: The GLO Records site uses ASP.NET forms. Options:
        1. Use the BLM ESRI REST service at gis.blm.gov/arcgis/rest/services/lands/
        2. Scrape the search results page with httpx + HTML parsing
        3. Use bulk dataset download from data.doi.gov

        For now returns empty list pending endpoint investigation.
        """
        return []

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Convert GLO document references to Feature objects.

        Features created from GLO records will have feature_type="historic_survey"
        and store the document reference URL in raw_record_ref.
        """
        features = []
        for record in raw_records:
            # TODO: Implement once fetch returns structured data
            pass
        return features
