"""
Lithology Agent

Analyzes the bedrock geology within the AOI to score grid cells based on
lithological favorability for the target mineral.

Key signals:
- Host rock type (intrusive vs extrusive, sedimentary vs metamorphic)
- Geologic age / stratigraphic position
- Proximity to intrusive contacts
- Known mineral-hosting formations (e.g., Carlin-trend carbonates for gold)

Data sources used: Macrostrat, state geological surveys
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import AgentResult, ScoredCell

logger = logging.getLogger(__name__)


class LithologyAgent(BaseAgent):
    agent_id = "lithology"
    agent_name = "Lithology Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        geology_units = spatial_context.get("geology_units", [])
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a specialist geologist evaluating lithological favorability for {target_mineral} mineralization.

## Area of Interest
{json.dumps(aoi_geojson, indent=2)}

## Geology Units Present
{json.dumps(geology_units, indent=2) if geology_units else "No geology data available — use regional context."}

## Grid Cells to Score
{json.dumps([{"cell_id": c.get("cell_id"), "geometry": c.get("geometry")} for c in grid_cells[:50]], indent=2)}

## Task
Score each grid cell from 0.0 (unfavorable) to 1.0 (highly favorable) for {target_mineral} mineralization
based on lithological indicators. Consider:
1. Host rock suitability (composition, permeability, reactivity)
2. Intrusive/extrusive contacts
3. Structural setting of the rocks
4. Regional metallogenic context

## Response Format
Return ONLY a JSON array:
```json
[
  {{
    "cell_id": "...",
    "score": 0.0,
    "confidence": 0.0,
    "evidence": ["reason 1", "reason 2"],
    "data_sources_used": ["macrostrat"]
  }}
]
```
"""

    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
        """Parse LLM JSON response into ScoredCell objects."""
        parsed = self._safe_parse_json(response)
        if not parsed or not isinstance(parsed, list):
            logger.warning(f"[{self.agent_id}] Could not parse response, returning zero scores")
            return self._zero_scores(grid_cells)

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

    def _zero_scores(self, grid_cells: List[Dict]) -> List[ScoredCell]:
        return [
            ScoredCell(
                cell_id=c.get("cell_id", ""),
                geometry=c.get("geometry", {}),
                score=0.0,
                confidence=0.0,
                evidence=["No lithology data available"],
                data_sources_used=[],
            )
            for c in grid_cells
        ]
