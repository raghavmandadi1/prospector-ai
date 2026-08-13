"""
Recorded mineral occurrences, mining districts, and abandoned workings.

Source is the WA DNR / Washington Geological Survey *Mines and Minerals*
geodatabase, extracted to static GeoJSON by
``scripts/build_reference_extracts.py``. It is a better source than MRDS for
this project for one specific reason: it carries `ASSAYS` and `PRODUCTION` as
explicit flags, and `LOCATION_ACCURACY` per record.

Those three fields change what the historical agent can be asked to do.
``knowledge/historical/gold.md`` states an assay-primacy rule — records with
real assay or production numbers dominate, district proximity without assay
backing caps around 0.6 — and until now the agent had to *guess* which category
a nearby occurrence fell into. It is now a lookup. `LOCATION_ACCURACY` does the
same job for the positional-accuracy caveat: instead of hedging every record
equally, we can say which ones are survey-grade.

The accuracy distinction is not cosmetic. Of 1467 gold/silver records, 917 carry
"coordinate accuracy highly variable" and 24 are literally *mining district
centroids* — a district centre wearing a site's clothes. Drawing those as crisp
dots, or letting them anchor a tight distance argument, invents precision that
was never in the data.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely.geometry import shape

from app.config import REFERENCE_DIR
from app.spatial.geometry import LocalMetric

logger = logging.getLogger(__name__)

OCCURRENCES_PATH = REFERENCE_DIR / "wa_occurrences.geojson"
DISTRICTS_PATH = REFERENCE_DIR / "wa_mining_districts.geojson"
IAML_PATH = REFERENCE_DIR / "wa_iaml.geojson"

#: Citation strings for `data_sources_used`. Kept here so every agent that
#: cites this data cites it identically and the benchmark can group by source.
DNR_CITATION = "WA_DNR_WGS_Mines_and_Minerals"

#: How much a record's position can be trusted, best first. Ordering is used to
#: pick the "best" nearby record and to decide what may serve as ground truth.
ACCURACY_RANK = {
    "survey": 0,
    "topo": 1,
    "derived": 2,
    "variable": 3,
    "district_centroid": 4,
    "unknown": 5,
}

#: Accuracy classes that must never anchor a distance argument or a benchmark
#: label. A district centroid is not a site; treating it as one is the exact
#: failure mode ``benchmarks/labels.yaml`` warns about in its header.
UNTRUSTWORTHY_POSITION = {"district_centroid"}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass
class _CachedFile:
    mtime: float
    payload: Dict[str, Any]


_CACHE: Dict[Path, _CachedFile] = {}


def _load_feature_collection(path: Path) -> Optional[Dict[str, Any]]:
    """Read and cache a GeoJSON file, reloading when it changes on disk.

    Keyed on mtime rather than loaded once per process: these files are rebuilt
    by a script during development, and a stale in-process copy would silently
    make a rebuild look like it had no effect.
    """
    try:
        stat = path.stat()
    except OSError:
        return None

    cached = _CACHE.get(path)
    if cached is not None and cached.mtime == stat.st_mtime:
        return cached.payload

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read %s: %s", path.name, exc)
        return None

    n = len(payload.get("features") or [])
    logger.info("Loaded %s (%d features)", path.name, n)
    _CACHE[path] = _CachedFile(mtime=stat.st_mtime, payload=payload)
    return payload


def occurrences_available() -> bool:
    return OCCURRENCES_PATH.exists()


def districts_available() -> bool:
    return DISTRICTS_PATH.exists()


def iaml_available() -> bool:
    return IAML_PATH.exists()


def load_occurrences_geojson() -> Optional[Dict[str, Any]]:
    return _load_feature_collection(OCCURRENCES_PATH)


def load_districts_geojson() -> Optional[Dict[str, Any]]:
    return _load_feature_collection(DISTRICTS_PATH)


def load_iaml_geojson() -> Optional[Dict[str, Any]]:
    return _load_feature_collection(IAML_PATH)


def occurrence_points(
    commodity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Flat list of occurrences as ``{lon, lat, name, ...props}`` dicts.

    This is the shape ``toponyms.matcher._corroborate`` expects, which is the
    whole reason toponym corroboration has been inert: with an empty occurrence
    list every place-name hit came back ``corroboration: "unknown"`` and
    ``score_cap_for`` applied the uncorroborated cap to all of them.

    ``commodity`` filters on a case-insensitive substring of
    ``commodity_primary`` — "gold" keeps 'Gold (Au)' and drops 'Silver (Ag)'.
    """
    fc = load_occurrences_geojson()
    if not fc:
        return []
    want = commodity.lower() if commodity else None
    out: List[Dict[str, Any]] = []
    for f in fc.get("features") or []:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        props = dict(f.get("properties") or {})
        if want and want not in str(props.get("commodity_primary", "")).lower():
            continue
        props["lon"] = coords[0]
        props["lat"] = coords[1]
        out.append(props)
    return out


# ---------------------------------------------------------------------------
# Per-cell aggregation
# ---------------------------------------------------------------------------


#: Fields carried from an occurrence record into an agent prompt. Deliberately
#: not the whole record: `comments` and `location_description` are free prose
#: that can run to several hundred characters each, and 150 cells' worth of
#: them would dominate the prompt without changing a score.
PROMPT_FIELDS = (
    "name",
    "commodity_primary",
    "commodities",
    "ore_minerals",
    "gangue",
    "district",
    "assays",
    "production",
    "accuracy_class",
    "doc_count",
)


def _slim(props: Dict[str, Any], distance_km: float) -> Dict[str, Any]:
    out = {k: props.get(k) for k in PROMPT_FIELDS if props.get(k) not in (None, "")}
    out["km"] = round(distance_km, 2)
    return out


def _evidence_rank(props: Dict[str, Any], distance_km: float) -> Tuple:
    """Sort key picking the most *informative* nearby record, not the closest.

    A production-backed mine 3 km away tells you more about a cell's prospects
    than an unassayed occurrence 400 m away whose coordinates came out of a
    legal description. Ordering is: documented production, then assays, then
    positional trustworthiness, then distance.
    """
    return (
        0 if props.get("production") else 1,
        0 if props.get("assays") else 1,
        ACCURACY_RANK.get(str(props.get("accuracy_class", "unknown")), 5),
        distance_km,
    )


@dataclass
class PointLayer:
    """A set of point records projected into one AOI's metre frame."""

    metric: LocalMetric
    props: List[Dict[str, Any]] = field(default_factory=list)
    #: Shapely Point array, parallel to ``props``, in metres.
    points: Any = None

    @classmethod
    def build(
        cls,
        records: Sequence[Dict[str, Any]],
        metric: LocalMetric,
    ) -> "PointLayer":
        props: List[Dict[str, Any]] = []
        xs: List[float] = []
        ys: List[float] = []
        for r in records:
            lon, lat = r.get("lon"), r.get("lat")
            if lon is None or lat is None:
                continue
            x, y = metric.xy(float(lon), float(lat))
            xs.append(x)
            ys.append(y)
            props.append(r)
        pts = (
            shapely.points(np.asarray(xs), np.asarray(ys))
            if props
            else np.empty(0, dtype=object)
        )
        return cls(metric=metric, props=props, points=pts)

    def __len__(self) -> int:
        return len(self.props)

    def distances_m(self, cell_geom_projected) -> np.ndarray:
        """Distance from a projected cell polygon to every point, metres.

        Zero for a point inside the cell. Measured from the polygon rather than
        its centre so a working just inside a corner is not reported as being
        half a cell away.
        """
        if not self.props:
            return np.empty(0)
        return shapely.distance(cell_geom_projected, self.points)


def occurrences_for_cell(
    layer: PointLayer,
    cell_geom_projected,
    radius_km: float,
    max_records: int,
) -> Dict[str, Any]:
    """Occurrence facts for one cell: counts by radius, nearest, and the best.

    Returns ``{}`` when nothing is within ``radius_km`` — an empty dict is a
    meaningful answer here (*nothing is recorded near this cell*) and the caller
    renders it as such rather than omitting the cell.
    """
    if not len(layer):
        return {}

    d_m = layer.distances_m(cell_geom_projected)
    d_km = d_m / 1000.0
    within = np.nonzero(d_km <= radius_km)[0]
    if within.size == 0:
        return {"n_1km": 0, "n_2km": 0, f"n_{int(radius_km)}km": 0, "nearest_km": None}

    def count(limit: float) -> int:
        return int(np.count_nonzero(d_km <= limit))

    ranked = sorted(
        ((layer.props[i], float(d_km[i])) for i in within),
        key=lambda t: _evidence_rank(t[0], t[1]),
    )
    nearest_i = int(within[np.argmin(d_km[within])])
    nearest_props = layer.props[nearest_i]
    nearest_km = float(d_km[nearest_i])

    assays = sum(1 for i in within if layer.props[i].get("assays"))
    production = sum(1 for i in within if layer.props[i].get("production"))

    out: Dict[str, Any] = {
        "n_in_cell": count(0.0),
        "n_1km": count(1.0),
        "n_2km": count(2.0),
        f"n_{int(radius_km)}km": int(within.size),
        "nearest_km": round(nearest_km, 2),
        "nearest": _slim(nearest_props, nearest_km),
        f"with_assays_{int(radius_km)}km": assays,
        f"with_production_{int(radius_km)}km": production,
        "records": [_slim(p, d) for p, d in ranked[:max_records]],
    }
    # `best` is only worth carrying when it differs from `nearest` — otherwise
    # it is the same JSON twice in every prompt.
    best_props, best_km = ranked[0]
    if best_props is not nearest_props:
        out["best"] = _slim(best_props, best_km)
    return out


@dataclass
class DistrictLayer:
    """Mining district polygons, projected, for cell membership tests."""

    props: List[Dict[str, Any]] = field(default_factory=list)
    polys: Any = None

    @classmethod
    def build(cls, fc: Optional[Dict[str, Any]], metric: LocalMetric) -> "DistrictLayer":
        props: List[Dict[str, Any]] = []
        geoms: List[Any] = []
        for f in (fc or {}).get("features") or []:
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                g = metric.project(shape(geom))
            except Exception:
                continue
            if g is None or g.is_empty:
                continue
            props.append(dict(f.get("properties") or {}))
            geoms.append(g)
        return cls(props=props, polys=np.asarray(geoms, dtype=object) if geoms else None)

    def __len__(self) -> int:
        return len(self.props)

    def for_cell(self, cell_geom_projected) -> Optional[Dict[str, Any]]:
        """The district a cell falls in, or the nearest one within ~2 km.

        District boundaries are generalised, so a cell just outside one is
        practically inside it. Membership is reported with the distance so the
        model can tell "in the Republic district" from "1.4 km outside it".
        """
        if not len(self):
            return None
        d = shapely.distance(cell_geom_projected, self.polys)
        i = int(np.argmin(d))
        km = float(d[i]) / 1000.0
        if km > 2.0:
            return None
        p = self.props[i]
        out = {
            k: p.get(k)
            for k in (
                "name",
                "county",
                "commodity_primary",
                "deposit_type",
                "discovery",
                "production_years",
                "production_amount",
                "production_unit",
            )
            if p.get(k) not in (None, "")
        }
        out["km"] = round(km, 2)
        out["inside"] = km == 0.0
        return out or None


def iaml_records() -> List[Dict[str, Any]]:
    """Inactive-and-abandoned-mine-lands records as flat lon/lat dicts.

    This is where adits, shafts, dumps and portals live — the physical evidence
    of workings, as opposed to the occurrence record that says a deposit exists.
    """
    fc = load_iaml_geojson()
    if not fc:
        return []
    out: List[Dict[str, Any]] = []
    for f in fc.get("features") or []:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        props = dict(f.get("properties") or {})
        props["lon"], props["lat"] = coords[0], coords[1]
        out.append(props)
    return out


IAML_PROMPT_FIELDS = (
    "name",
    "kind",
    "feature_description",
    "hazard",
    "mining_district",
    "production",
    "years_of_operation",
)


def iaml_for_cell(
    layer: PointLayer, cell_geom_projected, radius_km: float, max_records: int
) -> List[Dict[str, Any]]:
    if not len(layer):
        return []
    d_km = layer.distances_m(cell_geom_projected) / 1000.0
    idx = np.nonzero(d_km <= radius_km)[0]
    if idx.size == 0:
        return []
    order = idx[np.argsort(d_km[idx])][:max_records]
    out = []
    for i in order:
        p = layer.props[int(i)]
        rec = {
            k: p.get(k) for k in IAML_PROMPT_FIELDS if p.get(k) not in (None, "")
        }
        rec["km"] = round(float(d_km[int(i)]), 2)
        out.append(rec)
    return out
