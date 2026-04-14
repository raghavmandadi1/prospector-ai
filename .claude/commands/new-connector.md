# /new-connector — GeoProspector Data Connector Scaffolder

You are a code generation agent for GeoProspector. When invoked, you scaffold a complete
new data connector from the project's established pattern.

The user provides the connector name and source in `$ARGUMENTS`.
Example: `/new-connector earthdata — NASA Earthdata SRTM DEM and MODIS raster products`

If `$ARGUMENTS` is empty, ask the user for:
1. Connector name (snake_case, e.g. `earthdata`)
2. Data source name and URL
3. Access method: REST API | WFS | bulk file download | scrape
4. What `feature_type` will records map to: `MINE | PROSPECT | CLAIM | SAMPLE | FORMATION | SURVEY`
5. Is an API key required?

---

## What to Generate

### File to create: `backend/app/connectors/<name>.py`

```python
"""
[SOURCE_NAME] Connector for GeoProspector.

Source: [SOURCE_URL]
Access method: [REST_API | WFS | FILE_DOWNLOAD]
Feature type produced: [FEATURE_TYPE]
Auth required: [yes (CHANNEL_AUTH_KEY) | no]

Data notes:
  - [Coverage, limitations, known quirks]
  - [Pagination approach if any]
  - [Rate limits if known]

Reference: docs/01_system_design.md § 4.2
"""
import logging
from typing import Any, Dict, List, Optional

from app.connectors.base_connector import BaseConnector
from app.models.channel import Channel
from app.models.feature import Feature

logger = logging.getLogger(__name__)

# Canonical endpoint — override via Channel.endpoint if needed
DEFAULT_ENDPOINT = "[SOURCE_URL]"

# Map source-specific status values to canonical status vocab
STATUS_MAP = {
    "[source_active_value]": "active",
    "[source_historical_value]": "historical",
    # add more as discovered
}


class [NamePascalCase]Connector(BaseConnector):

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch raw records from [SOURCE_NAME].

        Args:
            bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84

        Returns:
            List of raw record dicts as returned by the source

        Pagination:
            [Describe pagination approach: offset/limit, page token, WFS paging, etc.]
        """
        params = {}

        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            # Adjust param names to match this API's bbox convention:
            params["bbox"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"

        # Add auth if required
        headers = {}
        if self.auth_config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.auth_config['api_key']}"

        # --- Simple single-page fetch ---
        try:
            data = await self._get(
                url=self.endpoint or DEFAULT_ENDPOINT,
                params=params,
                headers=headers,
            )
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Fetch failed: {e}")
            raise

        # Unwrap response — adjust key path to match source response shape:
        records = data.get("features", data) if isinstance(data, dict) else data
        logger.info(f"[{self.__class__.__name__}] Fetched {len(records)} raw records")
        return records

        # --- If pagination is needed, replace above with: ---
        # all_records = []
        # page = 1
        # while True:
        #     params["page"] = page
        #     data = await self._get(url=..., params=params, headers=headers)
        #     batch = data.get("features", [])
        #     if not batch:
        #         break
        #     all_records.extend(batch)
        #     page += 1
        # return all_records

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Transform raw [SOURCE_NAME] records into canonical Feature ORM instances.

        Required fields on every Feature:
          - source_channel: str
          - source_record_id: str  (must be stable/unique per source record)
          - geometry: WKT string (POINT(...) or POLYGON(...)) in SRID 4326

        All other fields are optional but populate them as completely as possible.
        """
        features = []
        channel_name = self.channel_config.name  # e.g. "earthdata"

        for record in raw_records:
            try:
                # --- Extract coordinates ---
                # Adjust key names to match source record structure:
                lat = float(record.get("latitude") or record.get("lat") or 0)
                lon = float(record.get("longitude") or record.get("lon") or record.get("lng") or 0)

                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    logger.warning(
                        f"[{self.__class__.__name__}] Invalid coordinates for "
                        f"record {record.get('id', '?')}: lat={lat}, lon={lon} — skipping"
                    )
                    continue

                geometry_wkt = f"POINT({lon} {lat})"

                # --- Map to canonical Feature schema ---
                feature = Feature(
                    source_channel=channel_name,
                    source_record_id=str(record.get("[SOURCE_ID_FIELD]", "")),
                    feature_type="[FEATURE_TYPE]",  # MINE | PROSPECT | CLAIM | SAMPLE | FORMATION | SURVEY
                    name=record.get("[NAME_FIELD]", ""),
                    geometry=geometry_wkt,
                    commodity_primary=record.get("[COMMODITY_FIELD]", "").lower() or None,
                    commodity_secondary=[],
                    deposit_type=record.get("[DEPOSIT_TYPE_FIELD]", None),
                    status=STATUS_MAP.get(record.get("[STATUS_FIELD]", ""), "unknown"),
                    geologic_unit=record.get("[GEOLOGIC_UNIT_FIELD]", None),
                    rock_type=record.get("[ROCK_TYPE_FIELD]", None),
                    structural_context=None,
                    geochemical_values={},  # populate if source has geochemical fields
                    source_quality=self._estimate_source_quality(record),
                    raw_record_ref=None,  # set to S3 path after storing raw
                )
                features.append(feature)

            except Exception as e:
                logger.warning(
                    f"[{self.__class__.__name__}] Failed to normalize record "
                    f"{record.get('[SOURCE_ID_FIELD]', '?')}: {e}"
                )
                continue

        logger.info(
            f"[{self.__class__.__name__}] Normalized {len(features)}/{len(raw_records)} records"
        )
        return features

    def _estimate_source_quality(self, record: Dict[str, Any]) -> float:
        """
        Assign a source_quality score (0.0–1.0) based on data characteristics.

        Scoring factors from system design:
          - Positional accuracy: GPS-precise=1.0, plotted on topo=0.7, estimated=0.3
          - Age of data: recent=1.0, pre-1980=0.6, pre-1920=0.4
          - Source authority: USGS/state survey=1.0, company report=0.7, crowd-sourced=0.4

        Adjust the logic below to match what fields this source provides.
        """
        # Default mid-quality; override with source-specific logic:
        return 0.7
```

---

### Registration steps

After generating the file, remind the user to:

1. Add the connector key to `CONNECTOR_REGISTRY` in `backend/app/pipeline/ingest.py`:
   ```python
   from app.connectors.[name] import [NamePascalCase]Connector

   CONNECTOR_REGISTRY = {
       ...
       "[name]": [NamePascalCase]Connector,
   }
   ```

2. Create a seed `Channel` record (via API or Alembic seed) with:
   ```json
   {
     "name": "[name]",
     "source_type": "REST_API",
     "endpoint": "[SOURCE_URL]",
     "data_type": "[LOCALITIES | CLAIMS | GEOCHEMISTRY | GEOLOGY | REMOTE_SENSING]",
     "refresh_schedule": "0 3 * * 0",
     "spatial_coverage": "global"
   }
   ```

3. If an API key is needed, add it to `.env` and `backend/app/config.py`.

---

## Checklist before finishing

- [ ] File created at correct path
- [ ] `fetch()` handles empty bbox gracefully (returns all records or raises clearly)
- [ ] `fetch()` has pagination if the source has result limits
- [ ] `normalize()` skips malformed records with a warning, does not raise
- [ ] `source_record_id` is truly unique and stable per upstream record
- [ ] Coordinates validated (lat in -90–90, lon in -180–180)
- [ ] `source_quality` is estimated with real logic, not hardcoded
- [ ] Registration steps shown to user
- [ ] Data notes section filled in with known limitations (see system design § 12)
