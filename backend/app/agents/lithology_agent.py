"""
Lithology Agent

Analyzes the bedrock geology within the AOI to score grid cells based on
lithological favorability for the target mineral.
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


class LithologyAgent(BaseAgent):
    agent_id = "lithology"
    agent_name = "Lithology Agent"
    knowledge_domain = "lithology"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        geology_units = spatial_context.get("geology_units", [])
        grid_cells = spatial_context.get("grid_cells", [])

        if geology_units:
            geology_section = (
                "## Database Geology Units\n"
                f"{json.dumps(geology_units[:150], separators=(',', ':'))}"
            )
        else:
            geology_section = """## Database Geology Units
No pre-queried geology data available. USE YOUR GEOLOGICAL KNOWLEDGE of Washington State to identify:
- What geological province this area falls in (e.g., Okanogan Highlands, North Cascades, Columbia Basin, Republic Graben, etc.)
- Known formations and rock types at these coordinates
- Favorability of the bedrock for the target mineral based on known geology"""

        return f"""You are an expert economic geologist specializing in {target_mineral} deposit evaluation in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{geology_section}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above from 0.0 to 1.0 for {target_mineral} lithological favorability.

IMPORTANT INSTRUCTIONS:
- Use your knowledge of Washington State geology at these specific coordinates
- Cells in favorable geological provinces (e.g., Republic Graben for gold, Okanogan Highlands) should score HIGH (0.6-0.9)
- Cells in unfavorable geology (e.g., Columbia River Basalt flood basalts far from contacts) should score LOW (0.05-0.2)
- Cells in moderately favorable settings should score MEDIUM (0.3-0.6)
- DO NOT default to zero — use your geological knowledge to differentiate cells
- DIFFERENTIATE between cells: scores in this batch should span a range, not cluster at one value
- Confidence should reflect how certain you are about the geology at those coordinates (0.3-0.8 typical)
- Evidence MUST cite specific formation names, rock types, or geological provinces
- data_sources_used should be ["geological_knowledge"] when using your training data

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
