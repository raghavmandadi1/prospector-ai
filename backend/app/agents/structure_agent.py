"""
Structure Agent

Evaluates structural geology controls on mineralization — faults, shear zones,
fold axes, and other conduits that focused hydrothermal fluids.

Key signals:
- Fault density and orientation relative to paleo-stress
- Intersection of fault sets (dilatational jogs)
- Distance to mapped fault traces
- Fold-related permeability enhancement

Data sources used: State geological surveys, USGS fault database
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import ScoredCell

logger = logging.getLogger(__name__)


class StructureAgent(BaseAgent):
    agent_id = "structure"
    agent_name = "Structure Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        fault_traces = spatial_context.get("fault_traces", [])
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a structural geologist evaluating tectonic controls on {target_mineral} mineralization.

## Area of Interest
{json.dumps(aoi_geojson, indent=2)}

## Mapped Structural Features
{json.dumps(fault_traces, indent=2) if fault_traces else "No structural data available — infer from regional context."}

## Grid Cells to Score
{json.dumps([{"cell_id": c.get("cell_id"), "geometry": c.get("geometry")} for c in grid_cells[:50]], indent=2)}

## Task
Score each cell 0.0–1.0 for structural favorability. Consider:
1. Proximity to fault traces and intersection zones
2. Fault type (extensional, compressional, strike-slip) and expected dilation
3. Fold hinge zones and associated fracture permeability
4. Regional structural trend alignment with mineralization style

## Response Format
Return ONLY a JSON array:
```json
[
  {{
    "cell_id": "...",
    "score": 0.0,
    "confidence": 0.0,
    "evidence": ["..."],
    "data_sources_used": ["usgs_faults"]
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
