"""
Historical Agent

Mines historical mining records, GLO survey notes, and early geological
reports to surface information not captured in modern databases.

Key signals:
- Historic mine production records (commodity, tonnage, grade)
- GLO field notes mentioning mineralization
- Early USGS and state survey reports
- Abandoned mine land (AML) data

Data sources used: GLO Records, BLM AML, USGS historical publications
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import ScoredCell

logger = logging.getLogger(__name__)


class HistoricalAgent(BaseAgent):
    agent_id = "historical"
    agent_name = "Historical Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        historic_mines = spatial_context.get("historic_mines", [])
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a mining historian and exploration geologist analyzing historical records for {target_mineral}.

## Area of Interest
{json.dumps(aoi_geojson, indent=2)}

## Historical Mining Records
{json.dumps(historic_mines[:100], indent=2) if historic_mines else "No historical records available."}

## Grid Cells to Score
{json.dumps([{"cell_id": c.get("cell_id"), "geometry": c.get("geometry")} for c in grid_cells[:50]], indent=2)}

## Task
Score each cell 0.0–1.0 based on historical evidence for {target_mineral}:
1. Past production of target and associated minerals
2. Density and quality of historic workings
3. References in survey reports to favorable geology
4. Patterns suggesting incompletely explored areas

## Response Format
Return ONLY a JSON array:
```json
[
  {{
    "cell_id": "...",
    "score": 0.0,
    "confidence": 0.0,
    "evidence": ["3 historic gold mines within 500m", "GLO notes mention 'rich quartz veins'"],
    "data_sources_used": ["usgs_mrds", "glo_records"]
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
