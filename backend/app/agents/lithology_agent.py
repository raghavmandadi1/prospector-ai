"""
Lithology Agent

Analyzes the bedrock geology within the AOI to score grid cells based on
lithological favorability for the target mineral.

Per-cell evidence comes from two sources with different jobs. The WA DNR 1:24k
surface geology gives statewide coverage of what rock is actually mapped under
each cell, with unit descriptions. USGS OF-00-495 covers only the six NE
Washington quadrangles, but its unit labels are the standardised ones the
published OF01-501 weights-of-evidence contrasts are keyed to — so inside that
footprint a cell can carry a *measured* predictive weight instead of an opinion
about one. The 24k labels are quad-local and cannot be matched against the WofE
table; see `app/spatial/wofe_grid.py` for why that matters.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.agents.base_agent import (
    BaseAgent,
    RESPONSE_FORMAT_INSTRUCTIONS,
    aoi_description,
    cell_facts_block,
    cell_summary,
)

logger = logging.getLogger(__name__)


def _render(facts: Dict[str, Any]) -> Optional[str]:
    """One line describing the rock under a cell, and its measured weight."""
    bits = []

    units = facts.get("geology") or []
    if units:
        described = []
        for u in units:
            label = u.get("unit") or "?"
            detail = " ".join(
                str(v) for v in (u.get("name"), u.get("lithology")) if v
            ).strip()
            pct = int(round(float(u.get("frac", 0)) * 100))
            described.append(f"{pct}% {label}" + (f" ({detail})" if detail else ""))
        bits.append("; ".join(described))

    wofe = facts.get("wofe") or {}
    fav_unit = wofe.get("favourable_unit")
    if fav_unit:
        frac = wofe.get("favourable_frac")
        share = f", {int(round(float(frac) * 100))}% of cell" if frac else ""
        bits.append(
            f"OF01-501 favourable unit {fav_unit} "
            f"contrast {wofe.get('favourable_contrast')} "
            f"[{wofe.get('formation')}{share}]"
        )
    elif wofe.get("unit"):
        # Inside the study area on a unit the WofE analysis found no correlation
        # with. That is a finding, not a gap: 92% of NE Washington is
        # non-permissive, and saying so beats leaving the model to infer it.
        bits.append(
            f"OF-00-495 unit {wofe['unit']} — zero training sites in OF01-501 "
            f"(non-predictive)"
        )

    return " | ".join(bits) if bits else None


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
        cell_facts = spatial_context.get("cell_facts", {})

        per_cell = cell_facts_block(
            grid_cells,
            cell_facts,
            _render,
            header=(
                "## Mapped Bedrock Under Each Cell (WA DNR 1:24,000 surface geology"
                "; OF-00-495 where it applies)"
            ),
            empty_note=(
                "Percentages are the share of the cell's area held by that unit — a "
                "cell straddling a contact is reported as the mixture it is, and "
                "those are often the interesting cells. Unit codes from the 24k "
                "geology are quad-local labels; OF01-501 contrast values are "
                "measured predictors from 50 epithermal training sites and should "
                "anchor your score where present. \"no data\" means no mapped "
                "polygon was found for that cell, not barren ground."
            ),
        )

        if geology_units:
            geology_section = (
                "## Geologic Units Present Across This AOI\n"
                "Deduplicated unit list for regional context; the per-cell block "
                "above is what each cell actually contains.\n"
                f"{json.dumps(geology_units[:40], separators=(',', ':'))}"
            )
        else:
            geology_section = """## Database Geology Units
No mapped geology available for this AOI. USE YOUR GEOLOGICAL KNOWLEDGE of Washington State to identify:
- What geological province this area falls in (e.g., Okanogan Highlands, North Cascades, Columbia Basin, Republic Graben, etc.)
- Known formations and rock types at these coordinates
- Favorability of the bedrock for the target mineral based on known geology
Keep confidence moderate at best — you are reasoning from coordinates, not from a map."""

        sections = [geology_section]
        if per_cell:
            sections.append(per_cell)

        return f"""You are an expert economic geologist specializing in {target_mineral} deposit evaluation in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{chr(10).join(sections)}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above from 0.0 to 1.0 for {target_mineral} lithological favorability.

IMPORTANT INSTRUCTIONS:
- The mapped units for each cell are the primary evidence. Where a cell carries an OF01-501 contrast value, that is a measured statistical predictor — let it anchor the score rather than overriding it with regional intuition.
- Where mapped geology is absent for a cell, fall back to your knowledge of Washington State geology at those coordinates and LOWER the confidence accordingly.
- Cells in favorable geological provinces (e.g., Republic Graben for gold, Okanogan Highlands) should score HIGH (0.6-0.9)
- Cells in unfavorable geology (e.g., Columbia River Basalt flood basalts far from contacts) should score LOW (0.05-0.2)
- Cells in moderately favorable settings should score MEDIUM (0.3-0.6)
- A cell straddling a contact between a favorable host and an unfavorable unit is often MORE prospective than either alone — competency and permeability contrasts localize ore.
- DO NOT default to zero — use the mapped units and your geological knowledge to differentiate cells
- DIFFERENTIATE between cells: scores in this batch should span a range, not cluster at one value
- Confidence should reflect the evidence you actually had for that cell: mapped units plus a contrast value warrants 0.7-0.9; mapped units alone 0.5-0.75; coordinates only 0.2-0.4
- Evidence MUST cite the specific unit codes, formation names, or rock types you used, quoting the contrast value when one was given
- data_sources_used should include "WA_DNR_WGS_Surface_Geology_24k" when you used the mapped units, "USGS_OF01_501" when you used a contrast value, and "geological_knowledge" when you fell back on your own training data

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
