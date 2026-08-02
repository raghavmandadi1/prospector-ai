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
from typing import Any, Dict

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_summary,
)

logger = logging.getLogger(__name__)


class ProximityAgent(BaseAgent):
    agent_id = "proximity"
    agent_name = "Proximity Agent"
    knowledge_domain = "proximity"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        known_deposits = spatial_context.get("known_deposits", [])
        grid_cells = spatial_context.get("grid_cells", [])

        deposits_section = (
            f"## Known Deposits and Occurrences\n{json.dumps(known_deposits[:150], separators=(',', ':'))}"
            if known_deposits
            else "## Known Deposits and Occurrences\nNo pre-queried deposit data — use your knowledge of documented Washington State mines and prospects near these coordinates."
        )

        return f"""You are a mineral exploration analyst evaluating proximity factors for {target_mineral} in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{deposits_section}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 based on proximity indicators:
1. Distance and density of known {target_mineral} deposits/mines
2. Clustering patterns suggesting district-scale mineralization
3. Presence of analogous deposit types
4. Historic production records
5. DO NOT default to zero — differentiate cells by distance to known occurrences; scores should span a range

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
