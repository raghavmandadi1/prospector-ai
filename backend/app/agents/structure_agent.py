"""
Structure Agent

Evaluates structural geology controls on mineralization — faults, shear zones,
fold axes, and other conduits that focused hydrothermal fluids.

This agent carries the highest gold weight of any of the six (0.30), and until
the local geology store existed it had neither a knowledge file nor a single
mapped fault: it inferred structure from bare coordinates. It now receives, per
cell, the faults, folds and dikes mapped at 1:24,000 within the 1,700 m buffer
that the USGS OF01-501 weights-of-evidence study measured as optimal, with their
principal azimuths and whether any falls in the favourable NW-to-NNE band.

Azimuths are folded into [0, 180) because a fault trace has no direction. The
published favourable band of 345°-030° therefore appears as two intervals,
az <= 30 or az >= 165 — see `spatial/geology.in_favourable_trend`.

Key signals:
- Fault density and orientation relative to paleo-stress
- Intersection of fault sets (dilatational jogs)
- Distance to mapped fault traces
- Fold-related permeability enhancement

Data sources used: WA DNR / WGS Surface Geology 1:24k (fault, fold, dike)
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


def _wofe_structures(facts: Dict[str, Any]) -> Optional[str]:
    """Structure signal from the OF-00-495 grids, for where 24k has no coverage.

    This is the fallback that matters. The WA DNR 1:24,000 geology is a mosaic of
    published quadrangles and it does not cover NE Washington gold country — that
    ground was mapped at 1:100,000, which is exactly why OF-00-495 was compiled.
    Its fault and fold rasters are sparse presence layers over the six Colville /
    Chewelah / Republic / Nespelem / Omak / Oroville quadrangles, so inside that
    footprint we can still say whether a fault crosses a cell even with no 24k
    trace to measure.

    Fault and fold types are named, not reported as bare codes. The `.e00`
    value-attribute tables carry only VALUE and COUNT with empty labels, so the
    codes look opaque if you only read the raster — but Appendices B-1 and B-2 of
    the report define every one of them, and the distinction decides the score.
    The OF01-501 predictor is specifically a **normal** fault: a thrust is
    Mesozoic contraction pre-dating the Eocene ore event, and a low-angle normal
    fault is a core-complex detachment rather than a steep vein conduit.

    What is *not* available here is orientation. These are presence rasters, so a
    cell can be told a normal fault crosses it but not which way the fault trends,
    and the 345°-030° half of the OF01-501 rule cannot be applied. Saying so is
    the point — the alternative is a model quietly assuming the favourable case.
    """
    wofe = facts.get("wofe") or {}
    fault_types = wofe.get("fault_types") or []
    fold_types = wofe.get("fold_types") or []
    dikes = wofe.get("dike_units") or []
    if not (fault_types or fold_types or dikes):
        return None

    bits = []
    if fault_types:
        line = "OF-00-495 faults in this cell: " + "; ".join(fault_types)
        if wofe.get("has_predictor_fault"):
            line += " — includes the OF01-501 NORMAL-fault predictor class"
        bits.append(line)
        bits.append("no azimuth available from this raster (presence only)")
    if fold_types:
        bits.append("folds: " + "; ".join(fold_types))
    if dikes:
        bits.append("dikes: " + ", ".join(dikes))
    bits.append("no 1:24,000 structural mapping covers this cell")
    return " | ".join(bits)


def _render(facts: Dict[str, Any]) -> Optional[str]:
    """One line describing the structures in and around a cell."""
    s = facts.get("structures") or {}
    if not s:
        # No 24k coverage here. Fall back to the OF-00-495 grids, which is the
        # normal case in NE Washington.
        return _wofe_structures(facts)

    count = s.get("count", 0)
    buffer_km = s.get("buffer_km")

    if not count:
        nearest = s.get("nearest_km")
        fallback = _wofe_structures(facts)
        if nearest is None:
            return fallback
        line = (
            f"nothing mapped within {buffer_km} km; nearest "
            f"{s.get('nearest_kind') or 'structure'} {nearest} km"
        )
        return f"{line} | {fallback}" if fallback else line

    bits = []
    kinds = s.get("kinds") or {}
    kind_str = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
    bits.append(f"{count} within {buffer_km} km ({kind_str})")

    nearest = s.get("nearest_km") or {}
    if nearest:
        bits.append(
            "nearest " + ", ".join(f"{k} {v} km" for k, v in nearest.items())
        )

    az = s.get("azimuths") or []
    if az:
        trend = (
            f"IN favourable NW-NNE band ({', '.join(str(a) for a in s.get('favourable_azimuths') or [])})"
            if s.get("favourable_trend")
            else "none in favourable NW-NNE band"
        )
        bits.append(f"azimuths {', '.join(str(a) for a in az)} — {trend}")

    crossings = s.get("fault_intersections_in_cell")
    if crossings:
        bits.append(f"{crossings} fault intersection(s) inside the cell")

    named = s.get("named") or []
    if named:
        bits.append("named: " + "; ".join(named))

    # A dike swarm is a magmatic-plumbing signal in its own right, so surface it
    # even when it came through the WofE grid rather than the 24k geology.
    wofe = facts.get("wofe") or {}
    if wofe.get("dike_units"):
        bits.append("OF-00-495 dikes: " + ", ".join(wofe["dike_units"]))

    return " | ".join(bits)


class StructureAgent(BaseAgent):
    agent_id = "structure"
    agent_name = "Structure Agent"
    knowledge_domain = "structure"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        fault_traces = spatial_context.get("fault_traces", [])
        grid_cells = spatial_context.get("grid_cells", [])
        cell_facts = spatial_context.get("cell_facts", {})

        per_cell = cell_facts_block(
            grid_cells,
            cell_facts,
            _render,
            header="## Mapped Structures Per Cell (WA DNR 1:24,000)",
            empty_note=(
                "Distances are from the cell polygon, so 0 km means the structure "
                "crosses the cell. The 1.7 km buffer is the OF01-501 measured "
                "optimum for normal faults against epithermal gold. Azimuths are "
                "folded into 0-180 degrees because a fault trace has no direction: "
                "the published favourable band of 345-030 degrees is therefore "
                "az <= 30 or az >= 165, and both halves count. IMPORTANT: fault "
                "density at 1:24,000 partly measures how thoroughly a quadrangle "
                "was mapped, not how deformed it is — do not reward a cell for "
                "sitting in a well-mapped quad."
            ),
        )

        if fault_traces:
            structures_section = (
                "## Mapped Structural Features Across This AOI\n"
                "Longest first. Use the per-cell block above for what each cell "
                "actually sits on.\n"
                f"{json.dumps(fault_traces[:40], separators=(',', ':'))}"
            )
        else:
            structures_section = (
                "## Mapped Structural Features\n"
                "No structural data available for this AOI — infer from your "
                "knowledge of regional structure (e.g. Republic graben faults, "
                "Straight Creek fault, Entiat fault, Ross Lake fault zone) and keep "
                "confidence LOW (0.2-0.4): you are reasoning from coordinates, not "
                "from a map."
            )

        sections = [structures_section]
        if per_cell:
            sections.append(per_cell)

        return f"""You are a structural geologist evaluating tectonic controls on {target_mineral} mineralization in Washington State.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{chr(10).join(sections)}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above 0.0–1.0 for structural favorability. Consider, in order of weight:
1. Proximity to mapped fault traces, using the 1,700 m buffer as your reference distance
2. Orientation — structures in the favourable NW-to-NNE band (az <= 30 or az >= 165 as reported) are the graben-bounding and intra-graben faults that controlled hydrothermal flow in the Republic system
3. Fault intersections and dilatational jogs inside a cell: two faults crossing is a materially better target than one trace passing through
4. Fold hinge zones and associated fracture permeability
5. Regional structural trend alignment with the deposit style expected at these coordinates

CALIBRATION:
- A cell cut by a favourably-oriented fault, or within a few hundred metres of one, is a strong structural target (0.7-0.9)
- A cell with structures nearby but none favourably oriented is moderate (0.4-0.6)
- A cell with nothing mapped within 1.7 km scores low (0.1-0.3), but note that "nothing mapped" is not "nothing there" — unmapped ground in poorly-surveyed quadrangles deserves LOW CONFIDENCE rather than a low score stated confidently
- Several sub-parallel traces are frequently one polyline split at quadrangle boundaries. Do not read that as structural complexity.
- DO NOT default to zero — differentiate cells; scores should span a range
- Confidence: mapped structures present 0.6-0.85; nothing mapped but well-surveyed ground 0.4-0.6; reasoning from coordinates alone 0.2-0.4
- Evidence MUST quote the distances, azimuths and structure names you actually used
- data_sources_used should include "WA_DNR_WGS_Surface_Geology_24k" when you used mapped structures, "USGS_OF01_501" when you applied the buffer or trend rule, and "structural_knowledge" when reasoning from your own training data

{RESPONSE_FORMAT_INSTRUCTIONS}
"""
