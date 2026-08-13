"""
Assemble one AOI's spatial context from local files.

This is the answer to the question the project has been unable to answer: *what
is actually recorded about this patch of ground?* Until now every agent received
an empty context dict and scored from model prior and a markdown briefing.
`orchestrator._build_spatial_context()` tried to fill it from PostGIS, and on the
dev path — the default, and the one `run-dev.sh` forces — that query dies on the
`asyncpg` import before it reaches a query. So the honest description of a run
was "Claude scoring grid cells from a 50 KB prose briefing".

What changes here is not the plumbing, it is what the model is looking at. Every
agent now receives, **per cell**:

* which rock units underlie it and in what proportion, from the WA DNR 1:24k
  surface geology;
* the published OF01-501 weights-of-evidence contrast for the cell's unit, where
  the cell falls inside the OF-00-495 study area — a measured predictive weight,
  not an opinion;
* mapped faults, folds and dikes within the 1,700 m WofE buffer, with their
  principal azimuths and whether any sits in the favourable NW-to-NNE band;
* recorded occurrences by distance band, with WA DNR's own `ASSAYS` and
  `PRODUCTION` flags and the positional accuracy of each record;
* mining-district membership with production figures, abandoned workings from the
  IAML inventory, and corroborated mining toponyms.

Two design decisions are load-bearing.

**Per-cell, not AOI-wide.** The old context keys were flat lists for the whole
AOI, which asked the model to do the spatial join itself from a JSON blob and a
list of cell centres. It cannot, reliably, and when it fails it fails silently by
smearing one district's evidence across every cell. The join happens here, in
Python, deterministically. The AOI-wide lists are still populated so the existing
prompt branches keep working, but they are now the summary and the per-cell block
is the evidence.

**Files, not a database.** See `app.spatial.__init__` and "steps for raghav 2.0"
§31. Every artifact is optional; a missing one costs a line in the prompt and a
greyed-out map toggle. `context_sources` reports exactly what was loaded, so a
run that saw nothing says so in its own record instead of looking normal.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import shapely
from shapely.geometry import shape

from app.config import settings
from app.spatial import geology as geology_mod
from app.spatial import occurrences as occ_mod
from app.spatial import wofe_grid as wofe_mod
from app.spatial.geometry import LocalMetric, pad_bbox

logger = logging.getLogger(__name__)

#: Max units named per cell. Beyond three the tail is slivers at contacts.
MAX_UNITS_PER_CELL = 3
#: Max named structures listed per cell.
MAX_NAMED_STRUCTURES = 4

#: Novelty thresholds, kilometres. A recorded working inside the cell or within
#: `CONFIRMS_KM` of it means a high score is the model agreeing with the record;
#: within `EXTENDS_KM` it is stepping out from known ground; beyond that it is a
#: genuine lead. These three cases look identical on a plain choropleth and mean
#: opposite things — see "steps for raghav 2.0" §27.
NOVELTY_CONFIRMS_KM = 0.5
NOVELTY_EXTENDS_KM = 2.0


def local_context_available() -> bool:
    """Is there any local evidence at all on this install?"""
    return bool(_available_sources())


def _available_sources() -> List[str]:
    sources: List[str] = []
    if occ_mod.occurrences_available():
        sources.append("wa_occurrences.geojson")
    if occ_mod.districts_available():
        sources.append("wa_mining_districts.geojson")
    if occ_mod.iaml_available():
        sources.append("wa_iaml.geojson")
    if geology_mod.get_store().available:
        sources.append("wa_geology.sqlite")
    if wofe_mod.get_store().available:
        sources.append("of00495.sqlite")
    return sources


def _aoi_bbox(grid_cells: Sequence[Any]) -> Optional[Tuple[float, float, float, float]]:
    boxes = []
    for c in grid_cells:
        bbox = c.get("bbox") if isinstance(c, dict) else getattr(c, "bbox", None)
        if bbox:
            boxes.append(bbox)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _cell_dicts(grid_cells: Sequence[Any]) -> List[Dict[str, Any]]:
    out = []
    for c in grid_cells:
        if isinstance(c, dict):
            out.append(c)
        elif hasattr(c, "model_dump"):
            out.append(c.model_dump())
        else:
            out.append(dict(getattr(c, "__dict__", {})))
    return out


def novelty_for(nearest_km: Optional[float]) -> Optional[str]:
    """Classify how novel a cell is relative to the recorded record.

    ``None`` means unknown — the occurrence extract is not built — which the UI
    must render as nothing rather than as a lead. Claiming "nothing recorded
    nearby" when we simply have no records would be the worst possible failure
    of this feature: it would turn missing data into a prospecting signal.
    """
    if nearest_km is None:
        return None
    if nearest_km <= NOVELTY_CONFIRMS_KM:
        return "confirms"
    if nearest_km <= NOVELTY_EXTENDS_KM:
        return "extends"
    return "lead"


def build_local_context(
    aoi_geojson: Dict[str, Any],
    grid_cells: Sequence[Any],
    target_mineral: str = "gold",
) -> Dict[str, Any]:
    """Everything local files know about this AOI, per cell and in aggregate.

    Returns the historical context keys (`geology_units`, `fault_traces`,
    `known_deposits`, `historic_mines`, `geochemical_samples`) plus `cell_facts`,
    `cell_novelty`, `context_sources` and `roles_active`. Never raises: a failure
    anywhere here degrades the prompt, and a run that scores from prior alone is
    a worse run, not a crashed one.
    """
    started = time.monotonic()
    context: Dict[str, Any] = {
        "geology_units": [],
        "fault_traces": [],
        "known_deposits": [],
        # No local geochemical source exists. USGS NGDB is a live service and is
        # unreachable on the dev path, so this stays empty and the geochemistry
        # agent's knowledge file makes the no-samples branch its primary path.
        "geochemical_samples": [],
        "historic_mines": [],
        "cell_facts": {},
        "cell_novelty": {},
        "context_sources": [],
        "coverage": {},
        "roles_active": {},
    }

    if not settings.local_context_enabled:
        logger.info("local_context_enabled is false — agents run on model prior only")
        return context

    sources = _available_sources()
    context["context_sources"] = sources
    if not sources:
        logger.warning(
            "No local spatial artifacts found under data/reference or "
            "data/derived — agents will run on LLM regional knowledge only. "
            "Build them with scripts/build_reference_extracts.py, "
            "scripts/build_geology_store.py and scripts/build_of00495.py."
        )
        return context

    bbox = _aoi_bbox(grid_cells)
    if bbox is None:
        return context

    cells = _cell_dicts(grid_cells)
    radius_km = float(settings.occurrence_search_radius_km)
    max_records = int(settings.max_records_per_cell)

    metric = LocalMetric.for_bbox(bbox)
    # Pad the window so a working just outside the polygon still informs the cell
    # next to it. An AOI edge is where the user stopped drawing, not a geological
    # boundary.
    window_bbox = pad_bbox(bbox, radius_km + 1.0)

    # --- projected cell geometries ---------------------------------------
    projected: Dict[str, Any] = {}
    for c in cells:
        cid = c.get("cell_id")
        geom = c.get("geometry")
        if not cid or not geom:
            continue
        try:
            projected[cid] = metric.project(shape(geom))
        except Exception:
            continue

    # --- point and polygon layers ----------------------------------------
    occurrence_records = _in_bbox(occ_mod.occurrence_points(), window_bbox)
    occ_layer = occ_mod.PointLayer.build(occurrence_records, metric)
    iaml_layer = occ_mod.PointLayer.build(
        _in_bbox(occ_mod.iaml_records(), window_bbox), metric
    )
    district_layer = occ_mod.DistrictLayer.build(
        occ_mod.load_districts_geojson(), metric
    )

    # --- geology window ---------------------------------------------------
    geo_window = geology_mod.get_store().window(window_bbox, metric)

    # --- OF-00-495 -------------------------------------------------------
    wofe_facts = wofe_mod.get_store().facts_for_cells(
        [c.get("cell_id") for c in cells if c.get("cell_id")]
    )

    # --- toponyms --------------------------------------------------------
    # Passing the occurrence list is what finally makes corroboration work: with
    # an empty list every hit came back "unknown" and score_cap_for applied the
    # uncorroborated cap to all of them (CLAUDE.md Known Gap #5).
    toponyms = _toponyms(cells, occurrence_records)

    # --- user field pins --------------------------------------------------
    evidence_pins, role_counts = _user_pins(window_bbox)
    context["roles_active"] = role_counts
    pin_layer = occ_mod.PointLayer.build(evidence_pins, metric)
    if evidence_pins:
        context["context_sources"] = sources + ["user_sites"]

    # --- per-cell assembly -----------------------------------------------
    facts: Dict[str, Dict[str, Any]] = {}
    novelty: Dict[str, Dict[str, Any]] = {}

    for c in cells:
        cid = c.get("cell_id")
        cell_geom = projected.get(cid)
        if not cid or cell_geom is None:
            continue

        entry: Dict[str, Any] = {}

        units = geology_mod.geology_for_cell(
            geo_window, cell_geom, MAX_UNITS_PER_CELL
        )
        if units:
            entry["geology"] = units

        structures = geology_mod.structures_for_cell(
            geo_window, cell_geom, MAX_NAMED_STRUCTURES
        )
        if structures:
            entry["structures"] = structures

        wf = wofe_facts.get(cid)
        if wf:
            entry["wofe"] = wf

        occ = occ_mod.occurrences_for_cell(
            occ_layer, cell_geom, radius_km, max_records
        )
        if occ:
            entry["occurrences"] = occ

        district = district_layer.for_cell(cell_geom)
        if district:
            entry["district"] = district

        workings = occ_mod.iaml_for_cell(iaml_layer, cell_geom, radius_km, 3)
        if workings:
            entry["workings"] = workings

        top = toponyms.get(cid)
        if top:
            entry["toponyms"] = top

        pins = occ_mod.iaml_for_cell(pin_layer, cell_geom, radius_km, 3)
        if pins:
            # Only role == "evidence" pins ever reach here; see _user_pins.
            entry["field_observations"] = pins

        facts[cid] = entry

        nearest_km = (occ or {}).get("nearest_km")
        novelty[cid] = {
            "nearest_occurrence_km": nearest_km,
            "nearest_occurrence_name": ((occ or {}).get("nearest") or {}).get("name"),
            "novelty": novelty_for(nearest_km) if occ_layer.props else None,
        }

    context["cell_facts"] = facts
    context["cell_novelty"] = novelty

    # --- coverage, reported rather than assumed -------------------------------
    # The WA DNR 1:24,000 compilation is a mosaic of 342 published quadrangles,
    # not a statewide layer, and it has real holes. Measured 2026-08-12 against
    # the benchmark AOIs: it covers exactly one of eleven (`puget_lowland_glacial`)
    # and has nothing within 16 km of Monte Cristo, Sultan Basin, Lennox Creek or
    # the North Fork Snoqualmie corridor. NE Washington is thin at 24k for the
    # same reason OF-00-495 exists — that ground was mapped at 1:100,000.
    #
    # So "the geology store is installed" and "this AOI has mapped geology" are
    # different claims, and conflating them would let a run look grounded while
    # the lithology and structure agents fell back to model prior. The counts go
    # into the SSE stream and the run record so the difference is never silent.
    context["coverage"] = {
        "geology_polygons": len(geo_window.unit_props),
        "geology_structures": len(geo_window.lin_props),
        "wofe_cells": len(wofe_facts),
        "occurrences": len(occ_layer),
        "iaml": len(iaml_layer),
        "toponym_cells": len(toponyms),
        "evidence_pins": len(evidence_pins),
        "cells_with_geology": sum(1 for f in facts.values() if f.get("geology")),
        "cells_with_structures": sum(1 for f in facts.values() if f.get("structures")),
        "cells_with_wofe": sum(1 for f in facts.values() if f.get("wofe")),
        "cells_with_occurrences": sum(
            1 for f in facts.values() if (f.get("occurrences") or {}).get("nearest")
        ),
        "cells_total": len(facts),
    }
    if facts and not geo_window.unit_props:
        logger.warning(
            "No WA DNR 1:24k geology covers this AOI — the 24k compilation is a "
            "342-quadrangle mosaic with gaps, and this AOI is in one. Lithology "
            "and structure will fall back to model prior%s.",
            " (OF-00-495 does cover it)" if wofe_facts else "",
        )

    # --- AOI-wide summaries, for the pre-existing prompt branches ---------
    context["geology_units"] = geology_mod.summarize_units(geo_window)
    context["fault_traces"] = geology_mod.summarize_structures(geo_window)
    deposits = _summarize_occurrences(occurrence_records)
    context["known_deposits"] = deposits
    context["historic_mines"] = [
        d for d in deposits if d.get("production") or d.get("assays")
    ]

    logger.info(
        "Local context for %d cells in %.2fs — sources: %s "
        "(%d occurrences, %d geology polygons, %d structures, %d WofE cells, "
        "%d toponym cells, %d evidence pins)",
        len(facts),
        time.monotonic() - started,
        ", ".join(context["context_sources"]) or "none",
        len(occ_layer),
        len(geo_window.unit_props),
        len(geo_window.lin_props),
        len(wofe_facts),
        len(toponyms),
        len(evidence_pins),
    )
    return context


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _in_bbox(
    records: Sequence[Dict[str, Any]], bbox: Sequence[float]
) -> List[Dict[str, Any]]:
    """Cheap lon/lat prefilter before anything gets projected."""
    min_lon, min_lat, max_lon, max_lat = bbox[0], bbox[1], bbox[2], bbox[3]
    out = []
    for r in records:
        lon, lat = r.get("lon"), r.get("lat")
        if lon is None or lat is None:
            continue
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            out.append(r)
    return out


def _summarize_occurrences(
    records: Sequence[Dict[str, Any]], limit: int = 40
) -> List[Dict[str, Any]]:
    """AOI-wide occurrence list: regional orientation, not per-cell evidence.

    Deliberately lean. Before per-cell facts existed this list *was* the evidence,
    so it carried every field including the `ore_minerals` and `gangue` strings,
    which run to 100+ characters each. Multiplied by 60 records and repeated in
    every batch of every agent, that dominated the prompt — a Monte Cristo
    historical prompt measured 24.6 KB, most of it this dump — while asking the
    model to do the spatial join itself.

    The per-cell block now carries the mineralogy for the records that are
    actually near each cell, so this one keeps only what a regional overview
    needs. Ordering still matters because the agents slice it: sorting by evidence
    weight means production- and assay-backed records are the ones that survive
    truncation.
    """
    ranked = sorted(
        records,
        key=lambda r: (
            0 if r.get("production") else 1,
            0 if r.get("assays") else 1,
            occ_mod.ACCURACY_RANK.get(str(r.get("accuracy_class", "unknown")), 5),
            str(r.get("name") or ""),
        ),
    )
    out = []
    for r in ranked[:limit]:
        rec = {
            "name": r.get("name"),
            "commodity": r.get("commodity_primary"),
            "district": r.get("district"),
            "assays": bool(r.get("assays")),
            "production": bool(r.get("production")),
            "accuracy_class": r.get("accuracy_class"),
            "lon": round(float(r["lon"]), 5),
            "lat": round(float(r["lat"]), 5),
        }
        out.append({k: v for k, v in rec.items() if v not in (None, "")})
    return out


def _toponyms(
    cells: Sequence[Dict[str, Any]], occurrence_records: Sequence[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Per-cell toponym hits, compacted, or {} when the lexicon is absent.

    Compacted rather than passed through as ``ToponymHit.evidence_string()``:
    those strings are deliberately verbose so a human can act on them, which is
    right for an evidence string in the UI and wrong for a prompt repeated across
    fifty cells. The fields a model needs to weigh a toponym are the name, its
    tier, and whether a recorded occurrence corroborates it.
    """
    try:
        from app.toponyms.matcher import load_gnis, load_lexicon, toponyms_for_cells
    except Exception as exc:  # pragma: no cover — import-time only
        logger.warning("Toponym matcher unavailable: %s", exc)
        return {}

    lexicon = load_lexicon()
    names = load_gnis()
    if lexicon is None or not names:
        return {}

    try:
        raw = toponyms_for_cells(
            cells, names, lexicon, occurrences=list(occurrence_records) or None
        )
    except Exception as exc:
        logger.warning("Toponym matching failed: %s", exc)
        return {}

    out: Dict[str, List[Dict[str, Any]]] = {}
    for cid, payload in raw.items():
        hits = payload.get("hits") or []
        if not hits:
            continue
        # Cap at three: toponyms are corroborative evidence, never primary, and
        # a cell with eleven mining place names does not need all eleven quoted
        # to make the point.
        out[cid] = [
            {
                "name": h.name,
                "tier": h.tier,
                "tier_name": h.tier_name,
                "km": h.distance_km,
                "corroboration": h.corroboration,
                "occurrence_km": h.nearest_occurrence_km,
            }
            for h in hits[:3]
        ]
    return out


def _user_pins(
    bbox: Sequence[float],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Field pins that may be shown to a model, plus a census of all roles.

    THE INVARIANT: only `role == "evidence"` pins are returned for prompting. A
    `role == "truth"` pin is benchmark ground truth, and a model told "someone
    marked this spot" will rank that spot highly by construction, which makes the
    benchmark measure nothing ("steps for raghav 2.0" §30). The role census is
    returned separately so the run record can log which roles were active without
    the pins themselves reaching a prompt.
    """
    try:
        from app.spatial.user_sites import load_user_sites, role_counts
    except Exception:
        # The importer script and this loader ship together; absent is normal.
        return [], {}

    try:
        counts = role_counts()
    except Exception as exc:
        logger.warning("Could not census user site roles: %s", exc)
        counts = {}

    try:
        pins = load_user_sites(roles=("evidence",))
    except Exception as exc:
        logger.warning("Could not load user sites: %s", exc)
        return [], counts

    out = []
    for p in pins:
        props = dict(p.get("properties") or p)
        lon, lat = props.get("lon"), props.get("lat")
        if lon is None or lat is None:
            geom = p.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            lon, lat = coords[0], coords[1]
        if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        if str(props.get("role")) != "evidence":
            # Defensive: the loader already filters, but this invariant is worth
            # enforcing twice. A truth pin leaking into a prompt is silent and
            # invalidates every benchmark number taken afterwards.
            continue
        out.append(
            {
                "lon": lon,
                "lat": lat,
                "name": props.get("name"),
                "kind": "field_observation",
                "feature_description": props.get("observed"),
                "hazard": None,
                "mining_district": None,
                "production": None,
                "years_of_operation": props.get("date"),
            }
        )
    return out, counts
