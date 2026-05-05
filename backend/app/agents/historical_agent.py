"""
Historical Agent

Mines historical mining records, GLO survey notes, and early geological
reports to surface information not captured in modern databases.
"""
import json
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import AgentResult, ScoredCell

logger = logging.getLogger(__name__)


def _cell_summary(cells: List[Dict]) -> str:
    """Build a compact, human-readable cell list with coordinates."""
    lines = []
    for c in cells[:60]:
        bbox = c.get("bbox", [0, 0, 0, 0])
        center_lon = (bbox[0] + bbox[2]) / 2
        center_lat = (bbox[1] + bbox[3]) / 2
        lines.append(f'  - {c["cell_id"]}: center ({center_lat:.4f}°N, {center_lon:.4f}°W)')
    if len(cells) > 60:
        lines.append(f"  ... and {len(cells) - 60} more cells")
    return "\n".join(lines)


class HistoricalAgent(BaseAgent):
    agent_id = "historical"
    agent_name = "Historical Agent"

    async def run(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
        config: Dict[str, Any],
    ) -> AgentResult:
        grid_cells = spatial_context.get("grid_cells", [])
        try:
            prompt = self.build_prompt(aoi_geojson, target_mineral, spatial_context)
            knowledge = self.load_knowledge("historical", target_mineral)
            logger.info(f"[{self.agent_id}] Sending prompt ({len(prompt)} chars) to LLM")
            llm_response = await self.call_llm(prompt, system_prompt=knowledge)
            logger.info(f"[{self.agent_id}] LLM response ({len(llm_response)} chars)")
            scored_cells = self.parse_llm_response(llm_response, grid_cells)
            return AgentResult(
                agent_id=self.agent_id,
                status="completed",
                scored_cells=scored_cells,
                agent_notes=llm_response[:1000] if llm_response else None,
            )
        except Exception as exc:
            logger.exception(f"Agent {self.agent_id} failed: {exc}")
            return AgentResult(
                agent_id=self.agent_id,
                status="failed",
                warnings=[str(exc)],
            )

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        historic_mines = spatial_context.get("historic_mines", [])
        grid_cells = spatial_context.get("grid_cells", [])

        # Compute AOI bounding box for geographic context
        all_bboxes = [c.get("bbox", [0, 0, 0, 0]) for c in grid_cells]
        if all_bboxes:
            min_lon = min(b[0] for b in all_bboxes)
            min_lat = min(b[1] for b in all_bboxes)
            max_lon = max(b[2] for b in all_bboxes)
            max_lat = max(b[3] for b in all_bboxes)
            aoi_desc = f"Bounding box: {min_lat:.4f}°N to {max_lat:.4f}°N, {abs(max_lon):.4f}°W to {abs(min_lon):.4f}°W"
        else:
            aoi_desc = "Unknown location"

        records_section = ""
        if historic_mines:
            records_section = f"## Database Historical Records\n{json.dumps(historic_mines[:100], indent=2)}"
        else:
            records_section = """## Database Historical Records
No pre-queried historical records available. USE YOUR KNOWLEDGE of Washington State mining history to assess:
- Known mining districts at or near these coordinates (e.g., Republic, Blewett, Monte Cristo, Holden, Sultan Basin, etc.)
- Historical producers, their commodities, and production levels
- GLO survey records and early USGS reports for this area
- Exploration maturity — was this area thoroughly prospected historically?"""

        return f"""You are a mining historian and exploration geologist specializing in Washington State's {target_mineral} mining history.

## Area of Interest
{aoi_desc}
Number of cells: {len(grid_cells)}

{records_section}

## Grid Cells (with center coordinates)
{_cell_summary(grid_cells)}

## Task
Score EVERY cell listed above from 0.0 to 1.0 based on historical {target_mineral} mining evidence.

IMPORTANT INSTRUCTIONS:
- Use your knowledge of Washington State mining history at these specific coordinates
- Cells within or adjacent to known historic mining districts should score HIGH (0.6-0.95)
- Cells in areas with minor prospects or occurrences should score MEDIUM (0.3-0.6)
- Cells in areas with no known mining history but that were never explored should score LOW with LOW confidence (0.1-0.3)
- Cells in areas thoroughly explored with no finds should score VERY LOW with HIGHER confidence (0.02-0.1)
- DO NOT default to zero — differentiate based on proximity to known districts
- Confidence should reflect data quality and certainty about mining history (0.3-0.8 typical)
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

## Response Format
Return ONLY a JSON array with ALL cells:
```json
[
  {{
    "cell_id": "c0_r0",
    "score": 0.75,
    "confidence": 0.7,
    "evidence": ["Within Republic mining district — Knob Hill mine produced 2M oz Au", "Multiple patented lode claims in immediate area per GLO records"],
    "data_sources_used": ["historical_knowledge", "usgs_mrds"]
  }}
]
```
"""

    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
        parsed = self._safe_parse_json(response)
        if not parsed or not isinstance(parsed, list):
            logger.warning(f"[{self.agent_id}] Could not parse response")
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

        # Fill in any cells the LLM missed
        scored_ids = {s.cell_id for s in scored}
        for c in grid_cells:
            cid = c.get("cell_id", "")
            if cid not in scored_ids:
                scored.append(
                    ScoredCell(
                        cell_id=cid,
                        geometry=c.get("geometry", {}),
                        score=0.0,
                        confidence=0.0,
                        evidence=["Cell not scored by LLM"],
                        data_sources_used=[],
                    )
                )
        return scored
