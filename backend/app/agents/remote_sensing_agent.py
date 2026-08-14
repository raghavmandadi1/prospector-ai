"""
Remote Sensing Agent

Analyzes satellite and airborne imagery for spectral indicators of
alteration zones, iron oxides, clay minerals, and lineaments.

**No imagery is ingested, and none is reachable from this deployment.** So this
agent does not analyse imagery: it produces a *predicted* alteration favourability
from mapped host lithology and deposit model, at low confidence, and it must never
claim to have observed a spectral signature. Its default gold weight is 0.07, the
second smallest, which is the right size for an inference of that kind.

What it does now receive is the mapped bedrock under each cell. Predicted
alteration is a function of host rock and deposit style — argillic and
advanced-argillic alteration over Eocene volcanics in the Republic grabens,
adularia-sericite in the Sanpoil, narrow sericite-carbonate halos around orogenic
veins in the North Cascades schists — so knowing the host is most of what this
prediction can be built from.

Key signals (what WOULD be diagnostic, given imagery):
- Hydrothermal alteration zones (SWIR clay minerals, alunite, kaolinite)
- Iron oxide / gossan spectral signatures
- Structural lineaments from image enhancement
- Vegetation stress patterns (phytogeochemical anomalies)

TODO: Integrate Earth Engine or Planet imagery. Until then, read the confidence
ceiling in knowledge/remote_sensing/gold.md as binding.
"""
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
    """Host rock and any alteration proxy available for a cell."""
    bits: List[str] = []

    units = facts.get("geology") or []
    if units:
        bits.append(
            "host: "
            + "; ".join(
                f'{int(round(float(u.get("frac", 0)) * 100))}% '
                + " ".join(str(v) for v in (u.get("unit"), u.get("lithology")) if v)
                for u in units[:2]
            )
        )

    wofe = facts.get("wofe") or {}
    if wofe.get("favourable_unit"):
        bits.append(
            f'Eocene volcanic host ({wofe["favourable_unit"]}) — argillic and '
            f"adularia-sericite alteration expected if mineralised"
        )

    # Gangue mineralogy at a nearby site is the closest thing to ground truth on
    # alteration style that exists without imagery.
    occ = facts.get("occurrences") or {}
    gangues = [r.get("gangue") for r in (occ.get("records") or []) if r.get("gangue")]
    if gangues:
        bits.append("recorded gangue nearby: " + " / ".join(str(g) for g in gangues[:2]))

    return " | ".join(bits) if bits else None


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
        cell_facts = spatial_context.get("cell_facts", {})
        # Halo cells of a sweep tile, if this is one. Empty for a
        # hand-drawn AOI, which keeps that prompt byte-identical.
        context_cells = spatial_context.get("context_cells") or []

        per_cell = cell_facts_block(
            grid_cells,
            cell_facts,
            _render,
            header="## Mapped Host Rock Per Cell (basis for PREDICTED alteration)",
            empty_note=(
                "This is mapped geology, not imagery. Use it to predict what "
                "alteration WOULD be expected if the cell were mineralised. You "
                "have not seen a spectral signature and must not describe one as "
                "observed."
            ),
            context_cells=context_cells,
        )

        sections = [
            "## Available Imagery\n"
            "NONE. No ASTER, Landsat or Sentinel scene has been ingested for this "
            "AOI, and this deployment cannot fetch one. Your output is a PREDICTED "
            "alteration favourability from host lithology and deposit model. Do not "
            "describe band ratios, spectral indices or NDVI values as if you had "
            "computed them."
        ]
        if per_cell:
            sections.append(per_cell)

        return f"""You are a remote sensing specialist assessing alteration favorability for {target_mineral} exploration in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{chr(10).join(sections)}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 for PREDICTED alteration favorability:
1. Alteration style expected from the mapped host rock and the deposit model it implies — argillic and adularia-sericite over Eocene volcanics; narrow sericite-carbonate-pyrite halos around orogenic veins in metamorphic hosts; garnet-pyroxene skarn at carbonate-intrusive contacts
2. Whether that alteration would be DETECTABLE if imagery were available. In Washington it frequently would not be: dense conifer canopy west of the Cascade crest and across much of the Okanogan Highlands, Quaternary glacial cover masking bedrock, and seasonal snow all suppress the signal. A cell where alteration is likely but undetectable is a poor remote sensing target and a fine exploration target — say which you mean.
3. Terrain and exposure: alpine and burned ground exposes rock; forest does not

CALIBRATION — READ THIS BEFORE SCORING:
- Your confidence ceiling for every cell in this batch is 0.3, because you have no imagery. Do not exceed it.
- Never state or imply that you observed an anomaly, index value, or band ratio
- Differentiate cells on host rock and exposure rather than assigning a uniform score
- Every evidence string must make clear that this is a prediction from mapped geology, not an image observation
- data_sources_used should include "WA_DNR_WGS_Surface_Geology_24k" when you used the mapped host and "remote_sensing_knowledge" for the alteration model. Do NOT cite Landsat, ASTER or Sentinel — you did not use them.

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
