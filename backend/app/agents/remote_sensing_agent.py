"""
Remote Sensing Agent

Analyzes satellite and airborne imagery for spectral indicators of
alteration zones, iron oxides, clay minerals, and lineaments.

Key signals:
- Hydrothermal alteration zones (SWIR clay minerals, alunite, kaolinite)
- Iron oxide / gossan spectral signatures
- Structural lineaments from image enhancement
- Vegetation stress patterns (phytogeochemical anomalies)

Data sources used: Landsat 8/9, Sentinel-2, ASTER (via usgs_eros or earthengine)
TODO: Integrate Earth Engine API or Planet API for live imagery fetch
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import ScoredCell

logger = logging.getLogger(__name__)


class RemoteSensingAgent(BaseAgent):
    agent_id = "remote_sensing"
    agent_name = "Remote Sensing Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a remote sensing specialist detecting alteration signatures for {target_mineral} exploration.

## Area of Interest
{json.dumps(aoi_geojson, indent=2)}

## Available Imagery Metadata
No live imagery ingested yet — provide qualitative assessment based on terrain and known regional patterns.

## Grid Cells to Score
{json.dumps([{"cell_id": c.get("cell_id"), "geometry": c.get("geometry")} for c in grid_cells[:50]], indent=2)}

## Task
Score each cell 0.0–1.0 for remote sensing indicators:
1. Predicted hydrothermal alteration probability based on geology
2. Lineament density from regional DEM (if available)
3. NDVI anomalies suggesting geochemical stress
4. Iron oxide spectral index predictions

Note: When live imagery is integrated, this agent will use ASTER SWIR band ratios
and Landsat OLI to map actual alteration zones.

## Response Format
Return ONLY a JSON array:
```json
[
  {{
    "cell_id": "...",
    "score": 0.0,
    "confidence": 0.2,
    "evidence": ["Predicted alteration zone based on lithology", "No imagery available"],
    "data_sources_used": []
  }}
]
```
"""

    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
        parsed = self._safe_parse_json(response)
        if not parsed or not isinstance(parsed, list):
            return []

        cell_map = {c.get("cell_id"): c for c in grid_cells}
        scored = []
        for item in parsed:
            cell_id = item.get("cell_id")
            cell = cell_map.get(cell_id)
            if not cell:
                continue
            scored.append(
                ScoredCell(
                    cell_id=cell_id,
                    geometry=cell.get("geometry", {}),
                    score=float(item.get("score", 0.0)),
                    confidence=float(item.get("confidence", 0.2)),
                    evidence=item.get("evidence", []),
                    data_sources_used=item.get("data_sources_used", []),
                )
            )
        return scored
