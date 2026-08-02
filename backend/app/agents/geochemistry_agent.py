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
from typing import Any, Dict

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_summary,
)

logger = logging.getLogger(__name__)


class GeochemistryAgent(BaseAgent):
    agent_id = "geochemistry"
    agent_name = "Geochemistry Agent"
    knowledge_domain = "geochemistry"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        samples = spatial_context.get("geochemical_samples", [])
        grid_cells = spatial_context.get("grid_cells", [])

        if samples:
            samples_section = (
                "## Geochemical Sample Data\n"
                "Each sample includes location and assay values under "
                "'geochemical_values' (e.g. Au_ppb, As_ppm).\n"
                f"{json.dumps(samples[:200], separators=(',', ':'))}"
            )
        else:
            samples_section = (
                "## Geochemical Sample Data\n"
                "No geochemical samples available for this area — flag as a data "
                "gap. Use your knowledge of regional geochemical patterns in "
                "Washington State, and keep confidence LOW (0.1-0.3)."
            )

        return f"""You are a geochemist identifying elemental anomalies indicative of {target_mineral} mineralization in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{samples_section}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 based on geochemical indicators for {target_mineral}:
1. ASSAY VALUES ARE PRIMARY EVIDENCE — cells containing samples with anomalous or ore-grade values must anchor the top of your score range; quote the values in evidence strings
2. Identify pathfinder elements and their threshold exceedances (Au, As, Sb, Hg for gold)
3. Map multi-element halos and dispersion patterns; samples are often downstream/downslope of source
4. Flag data gaps where no samples exist (low confidence, not zero score)
5. DIFFERENTIATE between cells — scores should span a range, not cluster at one value

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
