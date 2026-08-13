"""
Proximity Agent

Scores cells based on spatial proximity to known mineral occurrences,
past-producing mines, and permitted claims.

This agent is the most circular of the six, and that is worth stating in the
module rather than only in its knowledge file. It rewards ground that is already
known to be mineralised, so a high proximity score is the model agreeing with the
record — confirmation, never discovery. Its default gold weight is 0.03 for
exactly that reason. The counts it now receives are real, which makes it more
useful and no less circular.

Key signals:
- Distance to nearest producing mine of target commodity
- Number of occurrences within search radius
- Density of historic workings
- Presence of active mining claims (BLM MLRS)

Data sources used: WA DNR Mines and Minerals extract, IAML inventory. USGS MRDS
and BLM MLRS remain live-only services and are unreachable on the dev path;
`blm_mlrs.py` and `glo_records.py` are still registered stubs returning [].
"""
import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_facts_block,
    cell_summary,
)

logger = logging.getLogger(__name__)


def _render(facts: Dict[str, Any]) -> Optional[str]:
    """One line of distance-and-density facts for a cell."""
    occ = facts.get("occurrences") or {}
    if not occ:
        return None

    nearest_km = occ.get("nearest_km")
    if nearest_km is None:
        return "no recorded occurrence within 5 km"

    bits: List[str] = []
    nearest = occ.get("nearest") or {}
    accuracy = str(nearest.get("accuracy_class", "unknown"))
    caveat = (
        " [position unreliable — do not use for a tight distance argument]"
        if accuracy in ("variable", "district_centroid", "unknown")
        else ""
    )
    bits.append(
        f'nearest "{nearest.get("name") or "unnamed"}" {nearest_km} km{caveat}'
    )
    bits.append(
        f'counts: {occ.get("n_in_cell", 0)} in cell, {occ.get("n_1km", 0)} <=1 km, '
        f'{occ.get("n_2km", 0)} <=2 km, {occ.get("n_5km", 0)} <=5 km'
    )
    bits.append(
        f'of those within 5 km: {occ.get("with_production_5km", 0)} with production, '
        f'{occ.get("with_assays_5km", 0)} assay-backed'
    )

    district = facts.get("district") or {}
    if district.get("name"):
        where = "inside" if district.get("inside") else f'{district.get("km")} km out'
        bits.append(f'district {district["name"]} ({where})')

    workings = facts.get("workings") or []
    if workings:
        bits.append(f"{len(workings)} IAML working(s) within 5 km")

    return " | ".join(bits)


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
        cell_facts = spatial_context.get("cell_facts", {})

        per_cell = cell_facts_block(
            grid_cells,
            cell_facts,
            _render,
            header="## Occurrence Distances and Densities Per Cell",
            empty_note=(
                "Distances are measured from the cell polygon in metres and "
                "converted, so 0 km means the record falls inside the cell — you do "
                "not need to estimate any distance yourself. Where a position is "
                "flagged unreliable, the count still means something but the "
                "distance does not: 917 of 1,467 WA DNR gold/silver records carry "
                "'coordinate accuracy highly variable' and 24 are district "
                "centroids. A tight cluster of imprecise coordinates is an artifact, "
                "not a district."
            ),
        )

        deposits_section = (
            "## Known Deposits and Occurrences Across This AOI\n"
            "Ordered most-documented first. The per-cell block above already has "
            "the distances.\n"
            f"{json.dumps(known_deposits[:40], separators=(',', ':'))}"
            if known_deposits
            else (
                "## Known Deposits and Occurrences\n"
                "No pre-queried deposit data — use your knowledge of documented "
                "Washington State mines and prospects near these coordinates, and "
                "keep confidence LOW (0.2-0.4)."
            )
        )

        sections = [deposits_section]
        if per_cell:
            sections.append(per_cell)

        return f"""You are a mineral exploration analyst evaluating proximity factors for {target_mineral} in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{chr(10).join(sections)}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 based on proximity indicators:
1. Distance to the nearest recorded {target_mineral} occurrence, weighted by whether that record has production or assays behind it — a producing mine 2 km away outranks an unassayed showing 500 m away
2. Density: occurrence counts at 1, 2 and 5 km, which distinguish a district from an isolated prospect
3. Clustering that suggests district-scale mineralization, discounted where the positions are flagged unreliable
4. Presence of physical workings (IAML), which is evidence somebody dug rather than evidence somebody recorded
5. The 4,000 m placer-association distance where placer occurrences are present

CALIBRATION:
- In or beside a production-backed occurrence: 0.8-0.95
- Several assay-backed occurrences within 2 km: 0.6-0.8
- One unflagged occurrence within 2 km: 0.35-0.55
- Nothing recorded within 5 km: 0.1-0.25 — and say plainly in the evidence that this is an absence of records, which in poorly-prospected ground is not an absence of mineralisation
- DO NOT default to zero — differentiate cells by distance and density; scores should span a range
- BE HONEST ABOUT CIRCULARITY: a high score here means this ground is already known. State that in your evidence string rather than implying a discovery.
- Confidence: real counts and a trustworthy nearest position 0.7-0.85; counts with unreliable positions 0.45-0.6; no data 0.2-0.35
- data_sources_used should include "WA_DNR_WGS_Mines_and_Minerals" when you used the recorded counts and distances

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
