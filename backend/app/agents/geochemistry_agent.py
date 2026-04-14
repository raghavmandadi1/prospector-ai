"""
Geochemistry Agent

Interprets stream sediment, soil, and rock geochemical sample data to identify
anomalous element concentrations indicative of nearby mineralization.

Key signals:
- Pathfinder elements (Au, As, Sb, Hg for gold; Cu, Mo, Re for porphyry Cu)
- Multi-element anomaly clustering
- Background vs threshold vs anomaly classification
- Geochemical dispersion trains pointing up-gradient

Data sources used: USGS NGDB, state geochemical surveys
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import ScoredCell

logger = logging.getLogger(__name__)


class GeochemistryAgent(BaseAgent):
    agent_id = "geochemistry"
    agent_name = "Geochemistry Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        samples = spatial_context.get("geochemical_samples", [])
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a geochemist identifying elemental anomalies indicative of {target_mineral} mineralization.

## Area of Interest
{json.dumps(aoi_geojson, indent=2)}

## Geochemical Sample Data
{json.dumps(samples[:200], indent=2) if samples else "No geochemical data available — flag as data gap."}

## Grid Cells to Score
{json.dumps([{"cell_id": c.get("cell_id"), "geometry": c.get("geometry")} for c in grid_cells[:50]], indent=2)}

## Task
Score each cell 0.0–1.0 based on geochemical indicators for {target_mineral}:
1. Identify pathfinder elements and their threshold exceedances
2. Map multi-element halos and dispersion patterns
3. Flag data gaps where no samples exist
4. Assess sampling density adequacy

## Response Format
Return ONLY a JSON array:
```json
[
  {{
    "cell_id": "...",
    "score": 0.0,
    "confidence": 0.0,
    "evidence": ["Au anomaly: 3x background", "As pathfinder elevated"],
    "data_sources_used": ["usgs_ngdb"]
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
                    confidence=float(item.get("confidence", 0.5)),
                    evidence=item.get("evidence", []),
                    data_sources_used=item.get("data_sources_used", []),
                )
            )
        return scored
