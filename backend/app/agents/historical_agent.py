"""
Historical Agent

Mines historical mining records, GLO survey notes, and early geological
reports to surface information not captured in modern databases.

Assay primacy: records carrying actual assay/grade/production values are the
strongest historical evidence and dominate this agent's scoring.
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_summary,
)

logger = logging.getLogger(__name__)


class HistoricalAgent(BaseAgent):
    agent_id = "historical"
    agent_name = "Historical Agent"
    knowledge_domain = "historical"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        historic_mines = spatial_context.get("historic_mines", [])
        grid_cells = spatial_context.get("grid_cells", [])

        if historic_mines:
            records_section = (
                "## Database Historical Records\n"
                "Records include location, status, commodities, and — where "
                "available — assay/grade values (e.g. Au_ppb, Ag_ppm) under "
                "'geochemical_values'.\n"
                f"{json.dumps(historic_mines[:150], separators=(',', ':'))}"
            )
        else:
            records_section = """## Database Historical Records
No pre-queried historical records available. USE YOUR KNOWLEDGE of Washington State mining history to assess:
- Known mining districts at or near these coordinates (e.g., Republic, Blewett, Monte Cristo, Holden, Sultan Basin, etc.)
- Historical producers, their commodities, and production levels
- GLO survey records and early USGS reports for this area
- Exploration maturity — was this area thoroughly prospected historically?"""

        return f"""You are a mining historian and exploration geologist specializing in Washington State's {target_mineral} mining history.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{records_section}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above from 0.0 to 1.0 based on historical {target_mineral} mining evidence.

ASSAY PRIMACY — the most important rule for this agent:
- Records with reported ASSAY VALUES, GRADES, or PRODUCTION FIGURES are the strongest historical evidence and must dominate your scoring
- A cell containing (or immediately adjacent to) a record with significant {target_mineral} assays should anchor the TOP of your score range (0.8-0.95)
- Scale assay influence by magnitude: ore-grade values >> anomalous values >> trace values
- District proximity WITHOUT any assay/production backing caps at ~0.6
- Quote the actual assay values in your evidence strings when present (e.g. "MRDS record: 12.3 g/t Au from Knob Hill vein")

OTHER INSTRUCTIONS:
- Use your knowledge of Washington State mining history at these specific coordinates
- Cells within or adjacent to known historic mining districts should score HIGH (0.6-0.95)
- Cells in areas with minor prospects or occurrences should score MEDIUM (0.3-0.6)
- Cells in areas with no known mining history but that were never explored should score LOW with LOW confidence (0.1-0.3)
- Cells in areas thoroughly explored with no finds should score VERY LOW with HIGHER confidence (0.02-0.1)
- DO NOT default to zero — differentiate based on proximity to known districts
- DIFFERENTIATE between cells: scores in this batch should span a range, not cluster at one value
- Confidence should reflect data quality and certainty about mining history (0.3-0.8 typical; higher when assays back the score)
- Evidence MUST name specific mines, districts, or historical records
- data_sources_used should include ["historical_knowledge", "usgs_mrds"] when using your training data

## Key Washington Gold Districts for Reference
- Republic district (Ferry County) — major epithermal gold
- Blewett/Peshastin (Chelan County) — orogenic gold in serpentinite
- Monte Cristo (Snohomish County) — Au-Ag-Cu veins
- Holden Mine (Chelan County) — Cu-Zn-Au-Ag massive sulfide
- Sultan Basin (Snohomish County) — placer and lode gold
- Liberty/Swauk (Kittitas County) — placer and lode gold
- Oroville area (Okanogan County) — various gold prospects

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
