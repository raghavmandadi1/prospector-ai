"""
Proximity Agent

Scores cells based on spatial proximity to known mineral occurrences,
past-producing mines, and permitted claims.

Key signals:
- Distance to nearest producing mine of target commodity
- Number of occurrences within search radius
- Density of historic workings
- Presence of active mining claims (BLM MLRS)

Data sources used: USGS MRDS, BLM MLRS, state mine databases
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import ScoredCell

logger = logging.getLogger(__name__)


class ProximityAgent(BaseAgent):
    agent_id = "proximity"
    agent_name = "Proximity Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        known_deposits = spatial_context.get("known_deposits", [])
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a mineral exploration analyst evaluating proximity factors for {target_mineral}.

## Area of Interest
{json.dumps(aoi_geojson, indent=2)}

## Known Deposits and Occurrences
{json.dumps(known_deposits[:100], indent=2) if known_deposits else "No deposit data available."}

## Grid Cells to Score
{json.dumps([{"cell_id": c.get("cell_id"), "geometry": c.get("geometry")} for c in grid_cells[:50]], indent=2)}

## Task
Score each cell 0.0–1.0 based on proximity indicators:
1. Distance and density of known {target_mineral} deposits/mines
2. Clustering patterns suggesting district-scale mineralization
3. Presence of analogous deposit types
4. Historic production records

## Response Format
Return ONLY a JSON array:
```json
[
  {{
    "cell_id": "...",
    "score": 0.0,
    "confidence": 0.0,
    "evidence": ["..."],
    "data_sources_used": ["usgs_mrds", "blm_mlrs"]
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
