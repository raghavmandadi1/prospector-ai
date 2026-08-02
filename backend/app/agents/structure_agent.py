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
from typing import Any, Dict

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_summary,
)

logger = logging.getLogger(__name__)


class StructureAgent(BaseAgent):
    agent_id = "structure"
    agent_name = "Structure Agent"
    knowledge_domain = "structure"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        fault_traces = spatial_context.get("fault_traces", [])
        grid_cells = spatial_context.get("grid_cells", [])

        structures_section = (
            f"## Mapped Structural Features\n{json.dumps(fault_traces[:150], separators=(',', ':'))}"
            if fault_traces
            else "## Mapped Structural Features\nNo structural data available — infer from your knowledge of regional structure (e.g. Republic graben faults, Straight Creek fault, Entiat fault, Ross Lake fault zone)."
        )

        return f"""You are a structural geologist evaluating tectonic controls on {target_mineral} mineralization in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{structures_section}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 for structural favorability. Consider:
1. Proximity to fault traces and intersection zones
2. Fault type (extensional, compressional, strike-slip) and expected dilation
3. Fold hinge zones and associated fracture permeability
4. Regional structural trend alignment with mineralization style
5. DO NOT default to zero — use regional structural knowledge to differentiate cells; scores should span a range

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
