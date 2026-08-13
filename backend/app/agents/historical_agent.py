"""
Historical Agent

Mines historical mining records, GLO survey notes, and early geological
reports to surface information not captured in modern databases.

Assay primacy: records carrying actual assay/grade/production values are the
strongest historical evidence and dominate this agent's scoring.

That rule used to be a request for inference. `knowledge/historical/gold.md` told
the agent to privilege assay-backed occurrences, and the agent had to guess which
nearby records those were. The WA DNR *Mines and Minerals* dataset carries
`ASSAYS` and `PRODUCTION` as explicit per-site flags and `LOCATION_ACCURACY` per
record, so the guess is now a lookup — and the positional caveat can be applied
to the records that deserve it instead of to all of them equally.

The accuracy field is not decoration. Of 1,467 WA DNR gold/silver sites, 917 are
recorded as "coordinate accuracy highly variable" and 24 are mining *district
centroids* — a district centre in a site's clothing. A tight distance argument
built on one of those is fiction, and the prompt says so.
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

#: Compact position-trust labels. The full WA DNR strings run to 45 characters
#: and appear on every record in every cell; the class is what changes a decision.
_POSITION_LABEL = {
    "survey": "GPS/orthophoto",
    "topo": "7.5' topo",
    "derived": "from description",
    "variable": "ACCURACY VARIABLE",
    "district_centroid": "DISTRICT CENTROID — not a site position",
    "unknown": "accuracy unknown",
}


def _record_str(rec: Dict[str, Any]) -> str:
    """A nearby occurrence rendered with its evidence flags in front."""
    flags = []
    flags.append("production Y" if rec.get("production") else "production n/r")
    flags.append("assays Y" if rec.get("assays") else "assays n/r")
    flags.append(
        _POSITION_LABEL.get(str(rec.get("accuracy_class", "unknown")), "accuracy unknown")
    )
    docs = rec.get("doc_count")
    if docs:
        flags.append(f"{docs} scanned doc{'s' if docs != 1 else ''}")
    name = rec.get("name") or "unnamed"
    # Mineralogy is deliberately not repeated here. `ORE_MINERALS` and `GANGUE`
    # run to 100+ characters per record and this agent does not reason from them
    # — the geochemistry agent does, and it gets them. Repeating them across
    # fifty cells doubled this prompt for no change in score.
    return f'"{name}" {rec.get("km")} km [{", ".join(flags)}]'


def _render(facts: Dict[str, Any]) -> Optional[str]:
    """One line of recorded mining history for a cell."""
    bits: List[str] = []

    occ = facts.get("occurrences") or {}
    if occ.get("nearest"):
        bits.append("nearest " + _record_str(occ["nearest"]))
        total = occ.get("n_5km", 0)
        if total:
            bits.append(
                f"{total} recorded site(s) within 5 km "
                f"({occ.get('with_assays_5km', 0)} assay-backed, "
                f"{occ.get('with_production_5km', 0)} with production, "
                f"{occ.get('n_in_cell', 0)} inside the cell)"
            )
        best = occ.get("best")
        if best:
            bits.append("best-documented nearby: " + _record_str(best))
    elif occ:
        bits.append("no recorded site within 5 km")

    district = facts.get("district") or {}
    if district.get("name"):
        where = "inside" if district.get("inside") else f'{district.get("km")} km outside'
        detail = []
        if district.get("production_amount"):
            detail.append(
                f'{district["production_amount"]} {district.get("production_unit") or ""}'.strip()
            )
        if district.get("production_years"):
            detail.append(str(district["production_years"]))
        if district.get("deposit_type"):
            detail.append(str(district["deposit_type"]))
        suffix = f' — {"; ".join(detail)}' if detail else ""
        bits.append(f'district {district["name"]} ({where}){suffix}')

    workings = facts.get("workings") or []
    if workings:
        bits.append(
            "IAML workings: "
            + "; ".join(
                f'{w.get("feature_description") or w.get("name")} {w.get("km")} km'
                for w in workings[:2]
            )
        )

    obs = facts.get("field_observations") or []
    if obs:
        bits.append(
            "FIELD OBSERVATION: "
            + "; ".join(
                f'{o.get("name")} {o.get("km")} km — {o.get("feature_description") or "no note"}'
                for o in obs[:2]
            )
        )

    tops = facts.get("toponyms") or []
    if tops:
        bits.append(
            "toponyms: "
            + "; ".join(
                f'"{t["name"]}" (T{t["tier"]} {t.get("tier_name","")}, {t["corroboration"]})'
                for t in tops
            )
        )

    return " | ".join(bits) if bits else None


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
        cell_facts = spatial_context.get("cell_facts", {})

        per_cell = cell_facts_block(
            grid_cells,
            cell_facts,
            _render,
            header="## Recorded Mining History Per Cell (WA DNR Mines and Minerals)",
            empty_note=(
                "`production Y` and `assays Y` are AUTHORITATIVE FLAGS from the WA "
                "DNR record, not inferences — where they appear, the assay-primacy "
                "rule applies as fact. `n/r` means not recorded, which is weaker "
                "evidence than a 'no'. The bracketed position label is that "
                "record's own accuracy: a DISTRICT CENTROID is a district centre "
                "with no site position at all and must never anchor a distance "
                "argument, and ACCURACY VARIABLE means the coordinate may be off by "
                "more than a cell width. Distances are from the cell polygon, so "
                "0 km means inside the cell."
            ),
        )

        if historic_mines:
            records_section = (
                "## Recorded Sites Across This AOI\n"
                "Ordered most-documented first (production, then assays, then "
                "positional accuracy). The per-cell block above is what each cell "
                "actually sits near.\n"
                f"{json.dumps(historic_mines[:40], separators=(',', ':'))}"
            )
        else:
            records_section = """## Database Historical Records
No pre-queried historical records available. USE YOUR KNOWLEDGE of Washington State mining history to assess:
- Known mining districts at or near these coordinates (e.g., Republic, Blewett, Monte Cristo, Holden, Sultan Basin, etc.)
- Historical producers, their commodities, and production levels
- GLO survey records and early USGS reports for this area
- Exploration maturity — was this area thoroughly prospected historically?
Keep confidence low-to-moderate: you are recalling a district, not reading a record."""

        sections = [records_section]
        if per_cell:
            sections.append(per_cell)

        return f"""You are a mining historian and exploration geologist specializing in Washington State's {target_mineral} mining history.

## Area of Interest
{aoi_description(grid_cells)}
Number of cells in this batch: {len(grid_cells)}

{chr(10).join(sections)}

## Grid Cells (with center coordinates as lat, lon)
{cell_summary(grid_cells)}

## Task
Score EVERY cell listed above from 0.0 to 1.0 based on historical {target_mineral} mining evidence.

ASSAY PRIMACY — the most important rule for this agent, now keyed to real flags:
- A cell containing or immediately adjacent to a record flagged `production Y` anchors the TOP of your range (0.85-0.95)
- `assays Y` without recorded production: 0.70-0.85
- A recorded occurrence with neither flag: 0.40-0.60 — it is a documented showing, not a documented deposit
- District membership with no assay- or production-backed record nearby caps at ~0.6, as the knowledge base specifies
- Scale by magnitude where the record gives you one, and quote the actual figures in your evidence strings
- POSITION DISCIPLINE: reduce the weight you give a distance argument when the record's position label says ACCURACY VARIABLE, and give a DISTRICT CENTROID record no distance weight at all — treat it as district-level evidence only. Say so in the evidence string.
- A FIELD OBSERVATION line is a first-hand record of something someone stood next to. It is not in any database and is not circular; weigh it as strong local evidence and name it as a field observation in your evidence string.

OTHER INSTRUCTIONS:
- Use your knowledge of Washington State mining history at these specific coordinates to supplement, never to override, the recorded flags
- Cells in areas with no known mining history but that were never explored should score LOW with LOW confidence (0.1-0.3)
- Cells in areas thoroughly explored with no finds should score VERY LOW with HIGHER confidence (0.02-0.1)
- DO NOT default to zero — differentiate based on proximity to known districts
- DIFFERENTIATE between cells: scores in this batch should span a range, not cluster at one value
- Confidence: a production- or assay-flagged record in or beside the cell warrants 0.75-0.9; an unflagged record 0.5-0.7; district knowledge alone 0.3-0.5
- Evidence MUST name specific mines, districts, or records, and state which flags you relied on
- data_sources_used should include "WA_DNR_WGS_Mines_and_Minerals" when you used the recorded flags, "USGS_GNIS_DomesticNames_WA" when you used a toponym, and "historical_knowledge" when using your training data

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
