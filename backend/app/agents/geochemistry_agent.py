"""
Geochemistry Agent

Interprets stream sediment, soil, and rock geochemical sample data to identify
anomalous element concentrations indicative of nearby mineralization.

**There is no local geochemical sample source.** USGS NGDB is a live WFS and is
unreachable on the dev path, so `geochemical_samples` is empty on every run and
will stay empty until a sample extract exists on disk. Pretending otherwise would
be the worst failure mode available to this agent: an agent with no data for its
own domain that returns a confident score contributes 0.20 of the gold composite
out of nothing, and the engine weights by confidence, so a confident guess is
actively worse than an honest low-confidence one.

What it does now have is real mineralogy. Every WA DNR occurrence record carries
`ORE_MINERALS` and `GANGUE` — observed mineral assemblages, which are the physical
thing a pathfinder element is a proxy for. Arsenopyrite in the ore list is the
orogenic diagnostic; adularia and chalcedonic quartz in the gangue is a
low-sulfidation epithermal fingerprint. That is weaker than an assay and much
stronger than nothing.

Key signals:
- Pathfinder elements (Au, As, Sb, Hg for gold; Cu, Mo, Re for porphyry Cu)
- Observed ore and gangue mineralogy as a proxy for those elements
- Multi-element anomaly clustering
- Geochemical dispersion trains pointing up-gradient

Data sources used: WA DNR Mines and Minerals (mineralogy). USGS NGDB when a
sample extract is built.
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
    """Observed mineralogy near a cell, which is what stands in for assays."""
    occ = facts.get("occurrences") or {}
    records = occ.get("records") or []
    bits: List[str] = []

    seen_ore: List[str] = []
    seen_gangue: List[str] = []
    for r in records:
        ore, gangue = r.get("ore_minerals"), r.get("gangue")
        if ore and ore not in seen_ore:
            seen_ore.append(str(ore))
        if gangue and gangue not in seen_gangue:
            seen_gangue.append(str(gangue))

    if seen_ore:
        bits.append("ore minerals nearby: " + " / ".join(seen_ore[:3]))
    if seen_gangue:
        bits.append("gangue: " + " / ".join(seen_gangue[:2]))

    if occ.get("with_assays_5km"):
        # The flag says assays exist in the source literature; the values are not
        # in the GIS attributes. That distinction has to survive into the prompt,
        # or the model will quote numbers it never saw.
        bits.append(
            f'{occ["with_assays_5km"]} site(s) within 5 km have assays ON RECORD '
            f"(values are in the cited source documents, not in this dataset)"
        )

    units = facts.get("geology") or []
    if units:
        top = units[0]
        label = " ".join(
            str(v) for v in (top.get("unit"), top.get("lithology")) if v
        )
        bits.append(f"host: {label}")

    return " | ".join(bits) if bits else None


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
        cell_facts = spatial_context.get("cell_facts", {})
        # Halo cells of a sweep tile, if this is one. Empty for a
        # hand-drawn AOI, which keeps that prompt byte-identical.
        context_cells = spatial_context.get("context_cells") or []

        per_cell = cell_facts_block(
            grid_cells,
            cell_facts,
            _render,
            header="## Observed Mineralogy Near Each Cell (WA DNR Mines and Minerals)",
            empty_note=(
                "THIS IS NOT ASSAY DATA. It is the ore and gangue mineral "
                "assemblage recorded at nearby sites — a physical observation that "
                "a pathfinder element is a proxy for, and a legitimate but weaker "
                "line of evidence. Reason from the assemblage to the deposit model "
                "(arsenopyrite as the orogenic diagnostic; adularia plus "
                "chalcedonic quartz as low-sulfidation epithermal; galena and "
                "sphalerite as the Metaline base-metal style). Do NOT quote "
                "concentrations: none are present in this dataset, and inventing a "
                "ppm value would be fabrication."
            ),
            context_cells=context_cells,
        )

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
                "NO GEOCHEMICAL SAMPLES ARE AVAILABLE for this area, and none will "
                "be until a sample extract is built — USGS NGDB is a live service "
                "that this deployment cannot reach. Flag this as a data gap in your "
                "evidence for every cell. Score from the mineralogy below and from "
                "your knowledge of regional geochemical patterns in Washington "
                "State, and keep confidence LOW (0.1-0.3)."
            )

        sections = [samples_section]
        if per_cell:
            sections.append(per_cell)

        return f"""You are a geochemist identifying elemental anomalies indicative of {target_mineral} mineralization in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{chr(10).join(sections)}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 based on geochemical indicators for {target_mineral}:
1. If actual sample values are present, THEY ARE PRIMARY EVIDENCE — cells containing anomalous or ore-grade values anchor the top of your range, and you must quote the values in your evidence strings
2. Where only mineralogy is available, reason from the assemblage to the expected pathfinder suite and to the deposit model it implies. Name the minerals you used.
3. Consider dispersion: samples and named creeks sit DOWNSTREAM and DOWNSLOPE of their source, so a signal at a confluence points upstream, not at itself
4. Flag data gaps explicitly — a gap is a LOW CONFIDENCE score, never a low score stated confidently
5. DIFFERENTIATE between cells — scores should span a range, not cluster at one value

CALIBRATION FOR THIS AGENT'S NORMAL CASE:
- With no samples anywhere in the AOI, your confidence ceiling is 0.35. Say in every evidence string that no geochemical samples were available.
- Mineralogy from an adjacent recorded site supports 0.5-0.7 on score with confidence 0.3-0.45
- Do not let the absence of samples become a low score for the cell: an unsampled cell is unknown, not barren
- data_sources_used should include "WA_DNR_WGS_Mines_and_Minerals" when you used the mineralogy and "geochemical_knowledge" for regional inference

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
