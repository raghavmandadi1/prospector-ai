"""
Lithology Agent

Analyzes the bedrock geology within the AOI to score grid cells based on
lithological favorability for the target mineral.
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


class LithologyAgent(BaseAgent):
    agent_id = "lithology"
    agent_name = "Lithology Agent"

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
            knowledge = self.load_knowledge("lithology", target_mineral)
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
        geology_units = spatial_context.get("geology_units", [])
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

        geology_section = ""
        if geology_units:
            geology_section = f"## Database Geology Units\n{json.dumps(geology_units, indent=2)}"
        else:
            geology_section = """## Database Geology Units
No pre-queried geology data available. USE YOUR GEOLOGICAL KNOWLEDGE of Washington State to identify:
- What geological province this area falls in (e.g., Okanogan Highlands, North Cascades, Columbia Basin, Republic Graben, etc.)
- Known formations and rock types at these coordinates
- Favorability of the bedrock for the target mineral based on known geology"""

        return f"""You are an expert economic geologist specializing in {target_mineral} deposit evaluation in Washington State.

## Area of Interest
{aoi_desc}
Number of cells: {len(grid_cells)}

{geology_section}

## Grid Cells (with center coordinates)
{_cell_summary(grid_cells)}

## Task
Score EVERY cell listed above from 0.0 to 1.0 for {target_mineral} lithological favorability.

IMPORTANT INSTRUCTIONS:
- Use your knowledge of Washington State geology at these specific coordinates
- Cells in favorable geological provinces (e.g., Republic Graben for gold, Okanogan Highlands) should score HIGH (0.6-0.9)
- Cells in unfavorable geology (e.g., Columbia River Basalt flood basalts far from contacts) should score LOW (0.05-0.2)
- Cells in moderately favorable settings should score MEDIUM (0.3-0.6)
- DO NOT default to zero — use your geological knowledge to differentiate cells
- Confidence should reflect how certain you are about the geology at those coordinates (0.3-0.8 typical)
- Evidence MUST cite specific formation names, rock types, or geological provinces
- data_sources_used should be ["geological_knowledge"] when using your training data

## Response Format
Return ONLY a JSON array with ALL cells:
```json
[
  {{
    "cell_id": "c0_r0",
    "score": 0.65,
    "confidence": 0.6,
    "evidence": ["Located in Republic Graben — Eocene volcanic rocks host epithermal gold deposits", "Sanpoil Volcanics are known hosts for Republic district-style mineralization"],
    "data_sources_used": ["geological_knowledge"]
  }}
]
```
"""

    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
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
