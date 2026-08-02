"""
Remote Sensing Agent

Analyzes satellite and airborne imagery for spectral indicators of
alteration zones, iron oxides, clay minerals, and lineaments.

Key signals:
- Hydrothermal alteration zones (SWIR clay minerals, alunite, kaolinite)
- Iron oxide / gossan spectral signatures
- Structural lineaments from image enhancement
- Vegetation stress patterns (phytogeochemical anomalies)

Data sources used: Landsat 8/9, Sentinel-2, ASTER (via usgs_eros or earthengine)
TODO: Integrate Earth Engine API or Planet API for live imagery fetch
"""
import logging
from typing import Any, Dict

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_summary,
)

logger = logging.getLogger(__name__)


class RemoteSensingAgent(BaseAgent):
    agent_id = "remote_sensing"
    agent_name = "Remote Sensing Agent"
    knowledge_domain = "remote_sensing"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        grid_cells = spatial_context.get("grid_cells", [])

        return f"""You are a remote sensing specialist detecting alteration signatures for {target_mineral} exploration in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

## Available Imagery Metadata
No live imagery ingested yet — provide qualitative assessment based on terrain and known regional patterns, and keep confidence LOW (0.1-0.3).

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 for remote sensing indicators:
1. Predicted hydrothermal alteration probability based on geology
2. Lineament density from regional DEM (if available)
3. NDVI anomalies suggesting geochemical stress
4. Iron oxide spectral index predictions
5. Differentiate cells rather than assigning a uniform score

Note: When live imagery is integrated, this agent will use ASTER SWIR band ratios
and Landsat OLI to map actual alteration zones.

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
