#!/usr/bin/env python3
"""
Build the tracked WA DNR reference extracts from the Mines & Minerals geodatabase.

This is the offline half of closing Known Gap #5: `data/raw/` holds 77 MB of
WA-authored, statewide mineral occurrence data that no code has ever read, while
`toponyms.matcher._corroborate()` runs against an empty occurrence list and caps
every toponym hit as uncorroborated. The runtime has no business reading an ESRI
geodatabase, so the conversion happens here, once, and the results are small
tracked GeoJSON that both the map and `app.spatial.local_store` can open with
nothing but `json`.

    .venv/bin/python scripts/build_reference_extracts.py            # all three
    .venv/bin/python scripts/build_reference_extracts.py occurrences
    .venv/bin/python scripts/build_reference_extracts.py districts --out-dir /tmp

Writes to data/reference/:
    wa_occurrences.geojson       Gold_Silver_Locations + Metallic_Mineral_Locations,
                                 enriched with the scanned-document index
    wa_mining_districts.geojson  Mining_Distircts_WA (the typo is in the source)
    wa_iaml.geojson              IAML_Sites + IAML_Features

Why pyogrio.raw and not geopandas/ogr2ogr: neither is installed, and adding
either to run a once-a-year conversion is not worth the dependency. `pyogrio.raw.read`
returns numpy arrays plus WKB, which is all this needs.

Three things worth knowing before you trust the output:

* **Positions come from the geometry column, not LATITUDE/LONGITUDE.** The two
  disagree — usually by ~1 m (attribute rounding), but by 130 m at Buckhorn
  Mountain. The geometry is what WGS digitised; the attributes are a convenience
  copy. Rows with no geometry fall back to the attributes and the count is reported.
* **`accuracy_class == "district_centroid"` is a district centre wearing a site's
  clothes.** 24 gold/silver rows and 19 metallic rows are placed at the centroid of
  their mining district. They are legitimate evidence that a district exists and
  useless as a position. Never promote one to benchmark ground truth.
* **The district polygons carry almost no attributes.** In this release of the
  geodatabase Primary_Comm, Other_Comm, Discovery, Prod_Years, Prod_Amnt,
  Prod_Unit, Dep_Type, Prod_Link, Prod_Cite and Notes are NULL for all 68 rows.
  The schema keeps the keys (they are in the source and may be populated in a
  later release) but they come out as empty strings today.
"""
import argparse
import collections
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pyogrio
import shapely
from pyproj import Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

logger = logging.getLogger("build_reference_extracts")

DEFAULT_GDB = REPO_ROOT / "data" / "raw" / "ger_portal_mines_minerals" / "WGS_Mines_Minerals.gdb"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "reference"

# Citation string used in AgentResult.data_sources_used — keep it identical to the
# one in the contract and in data/README.md.
SOURCE_NAME = "WA_DNR_WGS_Mines_and_Minerals"

# Both geodatabases declare NAD83(HARN) / Washington South (US survey feet) and
# their coordinates really are in it — the layer bounds round-trip to the WA extent.
SOURCE_CRS = "EPSG:2927"
TARGET_CRS = "EPSG:4326"

LAYER_GOLD_SILVER = "Gold_Silver_Locations"
LAYER_METALLIC = "Metallic_Mineral_Locations"
LAYER_DOCUMENTS = "Metallic_Minerals_Scanned_Documents"
LAYER_DISTRICTS = "Mining_Distircts_WA"  # sic — the typo is in the source geodatabase
LAYER_IAML_SITES = "IAML_Sites"
LAYER_IAML_FEATURES = "IAML_Features"

# 6 dp is ~11 cm at this latitude — finer than the best-located site in the file
# (43 rows are GPS; the rest are topo-map or worse) and it halves the file size.
COORD_DECIMALS = 6
# ~55 m. District boundaries are 1980 hand-drawn plate boundaries; carrying 40421
# vertices for 68 polygons buys nothing but bytes over the wire.
DISTRICT_SIMPLIFY_DEG = 0.0005
# A site can have up to 293 scanned documents. The popup wants a hint, not a
# bibliography; doc_count keeps the full number honest.
MAX_DOCS_PER_SITE = 3

# LOCATION_ACCURACY is free text in the source. This is the full observed domain
# (eight strings across both layers); anything new maps to "unknown" and warns.
ACCURACY_CLASS: Dict[str, str] = {
    "GPS coordinates": "survey",
    "located from orthophoto": "survey",
    "USGS 7.5-minute topographic map": "topo",
    "generally from USGS 7.5-minute topographic map": "topo",
    "coordinates estimated from location description": "derived",
    "coordinates estimated from legal description": "derived",
    "coordinate accuracy highly variable": "variable",
    "mining district centroid": "district_centroid",
}
ACCURACY_UNKNOWN = "unknown"

_WA_BOUNDS_FALLBACK = (-125.5, 45.0, -116.0, 49.5)


def wa_bounds() -> Tuple[float, float, float, float]:
    """WA_BOUNDS from the grid module, with a literal fallback.

    Imported late and defensively on purpose: this is an offline build script and
    it should not stop working because someone is mid-edit in `backend/app`. The
    fallback is the same tuple `app.scoring.grid` defines; if they ever diverge
    the grid module wins and this warns.
    """
    try:
        from app.scoring.grid import WA_BOUNDS  # noqa: PLC0415 - see docstring

        if tuple(WA_BOUNDS) != _WA_BOUNDS_FALLBACK:
            logger.warning(
                "WA_BOUNDS in app.scoring.grid is %s but this script's fallback says %s "
                "— update the fallback",
                tuple(WA_BOUNDS),
                _WA_BOUNDS_FALLBACK,
            )
        return tuple(WA_BOUNDS)  # type: ignore[return-value]
    except Exception as exc:  # pragma: no cover - only when the backend is broken
        logger.warning("Could not import app.scoring.grid (%s); using literal WA_BOUNDS", exc)
        return _WA_BOUNDS_FALLBACK


# --- reading ---------------------------------------------------------------


def read_layer(
    gdb: Path, layer: str, columns: Optional[Sequence[str]] = None
) -> Tuple[Optional[np.ndarray], Dict[str, np.ndarray], int]:
    """Read a whole layer as (wkb array or None, {column: array}, n_rows).

    Whole-layer reads only. `bbox=`/`mask=` push-down returns 0 features on this
    geodatabase (stale spatial index), so never filter here — filter downstream.

    Note the returned column order is the geodatabase's, not the order requested,
    which is why everything downstream indexes the dict by name.
    """
    meta, _, geom, fields = pyogrio.raw.read(
        str(gdb), layer=layer, columns=list(columns) if columns else None
    )
    names = list(meta["fields"])
    cols = {n: fields[i] for i, n in enumerate(names)}
    n = len(geom) if geom is not None else (len(fields[0]) if fields else 0)
    logger.info("read %s: %d rows, %d columns", layer, n, len(names))
    return geom, cols, n


def _s(v: Any) -> str:
    """Normalize any source value to a stripped string.

    NULL becomes `""` rather than None deliberately: the map popup renderer drops
    both, and a uniform string type means the frontend never has to type-check a
    property. IAML county values carry trailing padding ('Chelan         '), hence
    the strip.
    """
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).strip()


def _i(v: Any) -> Optional[int]:
    """Normalize a numeric source value to int, or None when absent."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _yes(v: Any, field: str, seen_odd: collections.Counter) -> bool:
    """ASSAYS / PRODUCTION are the string 'yes' or empty. Emit a real bool.

    Anything else is recorded so schema drift in a future release surfaces as a
    warning instead of being silently read as False.
    """
    s = _s(v).lower()
    if s not in ("", "yes"):
        seen_odd[f"{field}={s!r}"] += 1
    return s == "yes"


def _round(v: float) -> float:
    return round(float(v), COORD_DECIMALS)


class PointResolver:
    """Turns a layer's geometry column into WGS84 lon/lat, with an attribute fallback.

    Geometry wins: it is what WGS digitised, and the LATITUDE/LONGITUDE columns are
    a rounded copy that disagrees by up to 130 m on at least one site. But a row
    with no geometry and a usable lat/lon is still a real occurrence, so the
    attributes are tried second, and the count of rows that needed them is reported.

    Every candidate is gated on WA_BOUNDS, because the source contains at least one
    record where *both* positions are corrupt: IAML feature 5501-1 (Northport
    Smelter) has northing 20288290 ft, which reprojects to latitude 89.99999986, and
    its attribute latitude is 117.77299 — the longitude with the sign flipped. A
    point like that would sail through the build and then raise AOIOutOfRangeError,
    or worse anchor a cell id somewhere near the pole. Rejected rows are dropped and
    named, never repaired: the parent site's coordinate is available but writing it
    here would invent a position the source does not record.
    """

    def __init__(self) -> None:
        self.transformer = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)
        self.bounds = wa_bounds()
        self.from_geometry = 0
        self.from_attributes = 0
        self.unpositioned = 0
        self.rejected: List[Dict[str, Any]] = []

    def _in_bounds(self, lon: float, lat: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bounds
        return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat

    def _from_geometry(
        self, geom: Optional[np.ndarray], n: int
    ) -> List[Optional[Tuple[float, float]]]:
        """One vectorised reprojection for every row that has a geometry."""
        out: List[Optional[Tuple[float, float]]] = [None] * n
        if geom is None:
            return out
        idx: List[int] = []
        xs: List[float] = []
        ys: List[float] = []
        for i in range(n):
            wkb = geom[i]
            if wkb is None:
                continue
            pt = shapely.from_wkb(wkb)
            if pt is None or pt.is_empty:
                continue
            # force_2d because IAML and district geometries are Z-flavoured (Z is
            # always 0 in this release, but do not rely on that).
            pt = shapely.force_2d(pt)
            idx.append(i)
            xs.append(pt.x)
            ys.append(pt.y)
        if idx:
            lons, lats = self.transformer.transform(np.array(xs), np.array(ys))
            for j, i in enumerate(idx):
                out[i] = (_round(lons[j]), _round(lats[j]))
        return out

    def resolve(
        self,
        geom: Optional[np.ndarray],
        lon_attr: Optional[np.ndarray],
        lat_attr: Optional[np.ndarray],
        n: int,
        labels: Optional[Sequence[str]] = None,
    ) -> List[Optional[Tuple[float, float]]]:
        geom_pts = self._from_geometry(geom, n)
        out: List[Optional[Tuple[float, float]]] = [None] * n

        for i in range(n):
            label = labels[i] if labels is not None else f"row {i}"
            candidates: List[Tuple[str, Tuple[float, float]]] = []
            if geom_pts[i] is not None:
                candidates.append(("geometry", geom_pts[i]))
            lon = _f(lon_attr[i]) if lon_attr is not None else None
            lat = _f(lat_attr[i]) if lat_attr is not None else None
            # 0/0 is the geodatabase's "unknown", not a position in the Gulf of Guinea.
            if lon is not None and lat is not None and not (lon == 0.0 and lat == 0.0):
                candidates.append(("attributes", (_round(lon), _round(lat))))

            for source, (lo, la) in candidates:
                if self._in_bounds(lo, la):
                    out[i] = (lo, la)
                    if source == "geometry":
                        self.from_geometry += 1
                    else:
                        self.from_attributes += 1
                    break
                self.rejected.append(
                    {"label": label, "source": source, "lon": lo, "lat": la}
                )
            else:
                self.unpositioned += 1
                detail = (
                    ", ".join(f"{s}=({lo:.6f},{la:.6f})" for s, (lo, la) in candidates)
                    or "no geometry and no lat/lon"
                )
                logger.warning(
                    "%s has no position inside WA_BOUNDS (%s); dropped", label, detail
                )
        return out


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# --- scanned-document index -------------------------------------------------


def build_document_index(gdb: Path) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, int]]:
    """Index 107739 document rows by SITE_ID in one pass.

    Built once and shared by both occurrence layers — 3314 sites * a scan of 107739
    rows would be 357 million comparisons for no reason.

    The join key is bare SITE_ID with no layer discriminator, which is only safe
    because the two layers' id spaces are disjoint (verified: 1467 gold/silver ids
    and 1847 metallic ids, intersection empty). ~746 document site ids belong to
    neither layer — they key the coal and nonmetallic inventories in the same
    geodatabase — and are ignored.
    """
    _, cols, n = read_layer(
        gdb,
        LAYER_DOCUMENTS,
        columns=[
            "SITE_ID",
            "TITLE",
            "AUTHOR",
            "DOCUMENT_DATE",
            "DOCUMENT_TYPE",
            "HYPERLINK",
            "UNIQUE_ID",
        ],
    )
    site_id = cols["SITE_ID"]
    title = cols["TITLE"]
    author = cols["AUTHOR"]
    date = cols["DOCUMENT_DATE"]
    dtype = cols["DOCUMENT_TYPE"]
    url = cols["HYPERLINK"]
    uniq = cols["UNIQUE_ID"]

    by_site: Dict[int, List[Tuple[int, int, int]]] = collections.defaultdict(list)
    stats = {"rows": n, "no_site_id": 0, "no_date": 0, "no_url": 0}
    for i in range(n):
        sid = _i(site_id[i])
        if sid is None:
            stats["no_site_id"] += 1
            continue
        yr = _i(date[i])
        if yr is None:
            stats["no_date"] += 1
        if not _s(url[i]):
            stats["no_url"] += 1
        # Sort key: newest first, then UNIQUE_ID so ties are broken by a stable
        # source id rather than by read order. Undated docs sort last (-1 sentinel
        # negates to +1, which is greater than -1963).
        by_site[sid].append((-(yr if yr is not None else -1), _i(uniq[i]) or 0, i))

    index: Dict[int, Dict[str, Any]] = {}
    for sid, rows in by_site.items():
        rows.sort()
        docs = []
        for _, _, i in rows[:MAX_DOCS_PER_SITE]:
            docs.append(
                {
                    "title": _s(title[i]),
                    "author": _s(author[i]),
                    "date": _i(date[i]),
                    "type": _s(dtype[i]),
                    "url": _s(url[i]),
                }
            )
        index[sid] = {"count": len(rows), "docs": docs}

    stats["sites_indexed"] = len(index)
    logger.info(
        "document index: %d rows -> %d site ids (%d rows with no SITE_ID)",
        n,
        len(index),
        stats["no_site_id"],
    )
    return index, stats


# --- occurrences ------------------------------------------------------------

OCCURRENCE_COLUMNS = [
    "SITE_ID",
    "SITE_NAME",
    "ALTERNATE_NAMES",
    "PRIMARY_COMMODITY",
    "COMMODITIES",
    "ORE_MINERALS",
    "GANGUE",
    "LOCATION_DESCRIPTION",
    "LEGAL_DESCRIPTION",
    "LOCATION_ACCURACY",
    "COUNTY",
    "LATITUDE",
    "LONGITUDE",
    "MINING_DISTRICT",
    "LOCATION_SOURCE",
    "ASSAYS",
    "PRODUCTION",
    "COMMENTS",
]

# uid prefix per source layer — "gs-181" / "mm-1". Needed because the two layers
# number their sites in separate id spaces.
UID_PREFIX = {LAYER_GOLD_SILVER: "gs", LAYER_METALLIC: "mm"}


def build_occurrences(gdb: Path, out_dir: Path, built_at: str) -> Dict[str, Any]:
    doc_index, doc_stats = build_document_index(gdb)
    resolver = PointResolver()
    unknown_accuracy: collections.Counter = collections.Counter()
    odd_booleans: collections.Counter = collections.Counter()
    accuracy_dist: collections.Counter = collections.Counter()
    commodity_dist: collections.Counter = collections.Counter()
    # Reported per layer as well as in total: the published distributions people
    # check this against (1467 rows / 649 assays / 450 production / 24 district
    # centroids) are gold-silver-only, and a combined total hides a regression.
    per_layer: Dict[str, Dict[str, Any]] = {}
    with_docs = 0
    duplicates = 0
    assays_true = 0
    production_true = 0

    seen: set = set()
    rows: List[Tuple[str, int, Dict[str, Any]]] = []

    for layer in (LAYER_GOLD_SILVER, LAYER_METALLIC):
        prefix = UID_PREFIX[layer]
        geom, cols, n = read_layer(gdb, layer, columns=OCCURRENCE_COLUMNS)
        labels = [
            f"{layer} SITE_ID={_i(cols['SITE_ID'][i])} ({_s(cols['SITE_NAME'][i])})"
            for i in range(n)
        ]
        points = resolver.resolve(geom, cols["LONGITUDE"], cols["LATITUDE"], n, labels)
        kept = 0
        layer_stats = {
            "rows_read": n,
            "features": 0,
            "assays": 0,
            "production": 0,
            "accuracy_class": collections.Counter(),
            "commodity_primary": collections.Counter(),
        }
        for i in range(n):
            site_id = _i(cols["SITE_ID"][i])
            if site_id is None:
                logger.warning("%s row %d has no SITE_ID; skipped", layer, i)
                continue
            key = (layer, site_id)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            pt = points[i]
            if pt is None:
                continue  # resolver already warned with the row's identity
            raw_accuracy = _s(cols["LOCATION_ACCURACY"][i])
            klass = ACCURACY_CLASS.get(raw_accuracy)
            if klass is None:
                klass = ACCURACY_UNKNOWN
                if raw_accuracy:
                    unknown_accuracy[raw_accuracy] += 1
            accuracy_dist[klass] += 1
            layer_stats["accuracy_class"][klass] += 1

            assays = _yes(cols["ASSAYS"][i], "ASSAYS", odd_booleans)
            production = _yes(cols["PRODUCTION"][i], "PRODUCTION", odd_booleans)
            assays_true += int(assays)
            production_true += int(production)
            layer_stats["assays"] += int(assays)
            layer_stats["production"] += int(production)
            commodity = _s(cols["PRIMARY_COMMODITY"][i])
            commodity_dist[commodity] += 1
            layer_stats["commodity_primary"][commodity] += 1

            # Both layers key the same document table on a bare SITE_ID; their id
            # spaces are disjoint, which is what makes this lookup unambiguous
            # (see build_document_index).
            doc = doc_index.get(site_id)
            doc_count = doc["count"] if doc else 0
            docs = doc["docs"] if doc else []
            if doc_count:
                with_docs += 1

            props = {
                "site_id": site_id,
                "uid": f"{prefix}-{site_id}",
                "name": _s(cols["SITE_NAME"][i]),
                "alternate_names": _s(cols["ALTERNATE_NAMES"][i]),
                "commodity_primary": _s(cols["PRIMARY_COMMODITY"][i]),
                "commodities": _s(cols["COMMODITIES"][i]),
                "ore_minerals": _s(cols["ORE_MINERALS"][i]),
                "gangue": _s(cols["GANGUE"][i]),
                "district": _s(cols["MINING_DISTRICT"][i]),
                "county": _s(cols["COUNTY"][i]),
                "assays": assays,
                "production": production,
                "location_accuracy": raw_accuracy,
                "accuracy_class": klass,
                "legal_description": _s(cols["LEGAL_DESCRIPTION"][i]),
                "location_source": _s(cols["LOCATION_SOURCE"][i]),
                "comments": _s(cols["COMMENTS"][i]),
                "source_layer": layer,
                "doc_count": doc_count,
                "docs": docs,
            }
            rows.append(
                (
                    prefix,
                    site_id,
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [pt[0], pt[1]]},
                        "properties": props,
                    },
                )
            )
            kept += 1
        layer_stats["features"] = kept
        layer_stats["accuracy_class"] = dict(layer_stats["accuracy_class"].most_common())
        layer_stats["commodity_primary"] = dict(layer_stats["commodity_primary"].most_common())
        per_layer[layer] = layer_stats

    # Deterministic: natural uid order — layer prefix, then numeric site id. A
    # tracked file that reshuffles on rebuild produces a useless diff.
    rows.sort(key=lambda r: (r[0], r[1]))
    features = [f for _, _, f in rows]

    if unknown_accuracy:
        logger.warning(
            "LOCATION_ACCURACY strings not in ACCURACY_CLASS (mapped to %r): %s",
            ACCURACY_UNKNOWN,
            ", ".join(f"{s!r} x{n}" for s, n in unknown_accuracy.most_common()),
        )
    if odd_booleans:
        logger.warning(
            "ASSAYS/PRODUCTION values other than '' or 'yes': %s",
            ", ".join(f"{s} x{n}" for s, n in odd_booleans.most_common()),
        )

    note = (
        "Positions are the geodatabase geometry reprojected from EPSG:2927, not the "
        "LATITUDE/LONGITUDE attribute columns (they disagree by up to ~130 m). "
        "accuracy_class=district_centroid rows are placed at a mining-district centre "
        "and must never be used as benchmark ground truth. doc_count is the full "
        "scanned-document count; docs holds at most the 3 newest."
    )
    path = out_dir / "wa_occurrences.geojson"
    size = write_geojson(
        path,
        {
            "source": SOURCE_NAME,
            "built_at": built_at,
            "layers": [LAYER_GOLD_SILVER, LAYER_METALLIC, LAYER_DOCUMENTS],
            "count": len(features),
            "note": note,
        },
        features,
    )

    return {
        "path": path,
        "bytes": size,
        "features": len(features),
        "per_layer": per_layer,
        "duplicates_dropped": duplicates,
        "from_geometry": resolver.from_geometry,
        "from_attributes": resolver.from_attributes,
        "dropped_no_position": resolver.unpositioned,
        "rejected_out_of_bounds": resolver.rejected,
        "sites_with_docs": with_docs,
        "document_rows": doc_stats["rows"],
        "document_rows_no_site_id": doc_stats["no_site_id"],
        "assays_true": assays_true,
        "production_true": production_true,
        "accuracy_class": dict(accuracy_dist.most_common()),
        "commodity_primary": dict(commodity_dist.most_common()),
        "unknown_accuracy_strings": dict(unknown_accuracy),
    }


# --- mining districts -------------------------------------------------------

DISTRICT_COLUMNS = [
    "DistrictID",
    "DistrictNm",
    "Other_Name",
    "County",
    "Primary_Comm",
    "Other_Comm",
    "Discovery",
    "Prod_Years",
    "Prod_Amnt",
    "Prod_Unit",
    "Dep_Type",
    "District_Link",
    "Prod_Link",
    "District_Cite",
    "Notes",
]


def build_districts(gdb: Path, out_dir: Path, built_at: str) -> Dict[str, Any]:
    geom, cols, n = read_layer(gdb, LAYER_DISTRICTS, columns=DISTRICT_COLUMNS)
    transformer = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)

    raw = shapely.force_2d(shapely.from_wkb(geom))
    wgs = shapely.transform(
        raw, lambda c: np.column_stack(transformer.transform(c[:, 0], c[:, 1]))
    )
    simplified = shapely.simplify(wgs, DISTRICT_SIMPLIFY_DEG, preserve_topology=True)
    rounded = shapely.transform(simplified, lambda c: np.round(c, COORD_DECIMALS))

    coords_before = int(sum(shapely.get_num_coordinates(g) for g in wgs))
    coords_after = int(sum(shapely.get_num_coordinates(g) for g in rounded))
    area_before = float(shapely.area(wgs).sum())
    area_after = float(shapely.area(rounded).sum())
    invalid = [
        _s(cols["DistrictNm"][i]) for i in range(n) if not shapely.is_valid(rounded[i])
    ]
    if invalid:
        logger.warning("simplification produced invalid geometry for: %s", ", ".join(invalid))

    empty_cols = [c for c in DISTRICT_COLUMNS if not any(_s(v) for v in cols[c].tolist())]
    if empty_cols:
        logger.warning(
            "district columns NULL for all %d rows (kept in the schema as empty strings): %s",
            n,
            ", ".join(empty_cols),
        )

    rows: List[Tuple[str, Dict[str, Any]]] = []
    for i in range(n):
        g = rounded[i]
        if g is None or g.is_empty:
            logger.warning("district %s has no geometry; skipped", _s(cols["DistrictNm"][i]))
            continue
        # simplify() unwraps single-part MultiPolygons to Polygon; re-wrap so every
        # feature in the file has one geometry type and the map styling is uniform.
        if g.geom_type == "Polygon":
            g = shapely.multipolygons([g])
        district_id = _s(cols["DistrictID"][i])
        rows.append(
            (
                district_id,
                {
                    "type": "Feature",
                    "geometry": shapely.geometry.mapping(g),
                    "properties": {
                        "district_id": district_id,
                        "name": _s(cols["DistrictNm"][i]),
                        "other_name": _s(cols["Other_Name"][i]),
                        "county": _s(cols["County"][i]),
                        "commodity_primary": _s(cols["Primary_Comm"][i]),
                        "other_commodities": _s(cols["Other_Comm"][i]),
                        "discovery": _s(cols["Discovery"][i]),
                        "production_years": _s(cols["Prod_Years"][i]),
                        "production_amount": _s(cols["Prod_Amnt"][i]),
                        "production_unit": _s(cols["Prod_Unit"][i]),
                        "deposit_type": _s(cols["Dep_Type"][i]),
                        "district_link": _s(cols["District_Link"][i]),
                        "production_link": _s(cols["Prod_Link"][i]),
                        "citation": _s(cols["District_Cite"][i]),
                        "notes": _s(cols["Notes"][i]),
                    },
                },
            )
        )

    rows.sort(key=lambda r: r[0])
    features = [f for _, f in rows]

    path = out_dir / "wa_mining_districts.geojson"
    size = write_geojson(
        path,
        {
            "source": SOURCE_NAME,
            "built_at": built_at,
            "layers": [LAYER_DISTRICTS],
            "count": len(features),
            "note": (
                f"Boundaries simplified to {DISTRICT_SIMPLIFY_DEG} deg (~55 m); total area "
                f"changed by {abs(area_after - area_before) / area_before:.2e}. "
                "Attribute columns other than name/other_name/county/district_link/citation "
                "are NULL for every row in this release of the geodatabase."
            ),
        },
        features,
    )
    return {
        "path": path,
        "bytes": size,
        "features": len(features),
        "coords_before": coords_before,
        "coords_after": coords_after,
        "area_rel_change": abs(area_after - area_before) / area_before,
        "all_null_columns": empty_cols,
        "invalid_after_simplify": invalid,
    }


# --- IAML -------------------------------------------------------------------

IAML_SITE_COLUMNS = [
    "IAML_ID",
    "site_name",
    "hazard",
    "visited",
    "mining_district",
    "county",
    "production",
    "years_of_operation",
    "underground_mine",
    "surface_mine",
    "mill",
    "site_dd_long",
    "site_dd_lat",
]
IAML_FEATURE_COLUMNS = [
    "IAML_ID",
    "site_name",
    "featureID",
    "feature_description",
    "development",
    "dumps",
    "water",
    "structure",
    "long",
    "lat",
]

# Every string key both kinds carry, so the popup renderer never type-checks.
_IAML_STRING_KEYS = (
    "hazard",
    "mining_district",
    "county",
    "production",
    "years_of_operation",
    "underground_mine",
    "surface_mine",
    "mill",
    "visited",
    "feature_description",
)


def build_iaml(gdb: Path, out_dir: Path, built_at: str) -> Dict[str, Any]:
    resolver = PointResolver()
    rows: List[Tuple[str, Dict[str, Any]]] = []

    geom, cols, n = read_layer(gdb, LAYER_IAML_SITES, columns=IAML_SITE_COLUMNS)
    labels = [
        f"IAML site {_s(cols['IAML_ID'][i])} ({_s(cols['site_name'][i])})" for i in range(n)
    ]
    points = resolver.resolve(geom, cols["site_dd_long"], cols["site_dd_lat"], n, labels)
    # Feature records carry no county/district of their own; inherit the parent
    # site's so a feature popup is not blank on the two fields a user filters by.
    parent: Dict[str, Tuple[str, str]] = {}
    n_sites = 0
    for i in range(n):
        iaml_id = _s(cols["IAML_ID"][i])
        district = _s(cols["mining_district"][i])
        county = _s(cols["county"][i])
        parent[iaml_id] = (district, county)
        pt = points[i]
        if pt is None:
            continue  # resolver already warned with the row's identity
        props = {
            "iaml_id": iaml_id,
            "name": _s(cols["site_name"][i]),
            "kind": "site",
            "hazard": _s(cols["hazard"][i]),
            "mining_district": district,
            "county": county,
            "production": _s(cols["production"][i]),
            "years_of_operation": _s(cols["years_of_operation"][i]),
            "underground_mine": _s(cols["underground_mine"][i]),
            "surface_mine": _s(cols["surface_mine"][i]),
            "mill": _s(cols["mill"][i]),
            "visited": _s(cols["visited"][i]),
            "feature_description": "",
        }
        rows.append((iaml_id, _point_feature(pt, props)))
        n_sites += 1

    geom, cols, n = read_layer(gdb, LAYER_IAML_FEATURES, columns=IAML_FEATURE_COLUMNS)
    # iaml_id for a feature is its own featureID ('1008-3'), not the parent site id:
    # it has to be unique for the sort to be deterministic, and the parent is
    # recoverable from the prefix.
    feature_ids = [
        _s(cols["featureID"][i]) or f"{_s(cols['IAML_ID'][i])}-?{i}" for i in range(n)
    ]
    labels = [
        f"IAML feature {feature_ids[i]} ({_s(cols['site_name'][i])})" for i in range(n)
    ]
    points = resolver.resolve(geom, cols["long"], cols["lat"], n, labels)
    n_features = 0
    for i in range(n):
        site_id = _s(cols["IAML_ID"][i])
        feature_id = feature_ids[i]
        pt = points[i]
        if pt is None:
            continue  # resolver already warned with the row's identity
        district, county = parent.get(site_id, ("", ""))
        props = {
            "iaml_id": feature_id,
            "name": _s(cols["site_name"][i]),
            "kind": "feature",
            "hazard": "",
            "mining_district": district,
            "county": county,
            "production": "",
            "years_of_operation": "",
            "underground_mine": "",
            "surface_mine": "",
            "mill": "",
            "visited": "",
            "feature_description": _s(cols["feature_description"][i]),
            # int flags, features only — 1 means the field crew recorded that class
            # of feature here (a shaft/adit, a dump, standing water, a structure).
            "development": _i(cols["development"][i]) or 0,
            "dumps": _i(cols["dumps"][i]) or 0,
            "water": _i(cols["water"][i]) or 0,
            "structure": _i(cols["structure"][i]) or 0,
        }
        rows.append((feature_id, _point_feature(pt, props)))
        n_features += 1

    ids = [k for k, _ in rows]
    if len(set(ids)) != len(ids):
        dupes = [k for k, c in collections.Counter(ids).items() if c > 1]
        logger.warning("iaml_id is not unique; sort order is ambiguous for: %s", dupes[:10])
    rows.sort(key=lambda r: r[0])
    features = [f for _, f in rows]

    path = out_dir / "wa_iaml.geojson"
    size = write_geojson(
        path,
        {
            "source": SOURCE_NAME,
            "built_at": built_at,
            "layers": [LAYER_IAML_SITES, LAYER_IAML_FEATURES],
            "count": len(features),
            "note": (
                "Inventory of Abandoned Mine Lands. kind='site' rows are the site "
                "record; kind='feature' rows are individual field-mapped openings, "
                "dumps, water bodies and structures, and their iaml_id is the source "
                "featureID ('<site>-<n>'). Features inherit mining_district and county "
                "from their parent site."
            ),
        },
        features,
    )
    return {
        "path": path,
        "bytes": size,
        "features": len(features),
        "sites": n_sites,
        "site_features": n_features,
        "from_geometry": resolver.from_geometry,
        "from_attributes": resolver.from_attributes,
        "dropped_no_position": resolver.unpositioned,
        "rejected_out_of_bounds": resolver.rejected,
    }


def _point_feature(pt: Tuple[float, float], props: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [pt[0], pt[1]]},
        "properties": props,
    }


# --- writing / verification -------------------------------------------------


def _render_geojson(
    collection_props: Dict[str, Any], features: List[Dict[str, Any]]
) -> str:
    out: List[str] = ['{"type":"FeatureCollection",']
    out.append('"properties":' + json.dumps(collection_props, separators=(",", ":")) + ",")
    out.append('"features":[')
    last = len(features) - 1
    for i, f in enumerate(features):
        out.append(json.dumps(f, separators=(",", ":")) + ("," if i < last else ""))
    out.append("]}")
    return "\n".join(out) + "\n"


def write_geojson(
    path: Path, collection_props: Dict[str, Any], features: List[Dict[str, Any]]
) -> int:
    """Write a FeatureCollection with one feature per line, idempotently.

    Compact JSON keeps a tracked file small; one feature per line keeps its diff
    readable. A single-line 3 MB GeoJSON is unreviewable in a pull request.

    **The rewrite is idempotent, and it has to be.** These three files are tracked
    in git — the frontend fetches them as map overlays — so a rebuild that changed
    nothing but the `built_at` stamp would show up as a 3,314-line diff and train
    everyone to ignore diffs on them. So: render once, and if the only difference
    from what is already on disk is the timestamp, keep the old timestamp and
    leave the file alone. A real data change still gets a fresh stamp, which is
    the only time the stamp carries information.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _render_geojson(collection_props, features)

    if path.exists() and "built_at" in collection_props:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            old_stamp = (previous.get("properties") or {}).get("built_at")
            if old_stamp:
                # Re-render with the old stamp; identical output means the data
                # is unchanged and the file should not be touched at all.
                as_before = _render_geojson(
                    {**collection_props, "built_at": old_stamp}, features
                )
                if as_before == path.read_text(encoding="utf-8"):
                    size = path.stat().st_size
                    logger.info(
                        "%s unchanged: %d features, %s (kept built_at %s)",
                        path,
                        len(features),
                        _human(size),
                        old_stamp,
                    )
                    return size
        except (OSError, ValueError) as exc:
            # Unreadable or hand-edited previous file: just overwrite it.
            logger.debug("could not compare against existing %s (%s)", path, exc)

    path.write_text(text, encoding="utf-8")
    size = path.stat().st_size
    logger.info("wrote %s: %d features, %s", path, len(features), _human(size))
    return size


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _iter_coords(geometry: Dict[str, Any]) -> Iterable[Sequence[float]]:
    """Yield every position in any GeoJSON geometry, at any nesting depth."""

    def walk(node: Any) -> Iterable[Sequence[float]]:
        if (
            isinstance(node, (list, tuple))
            and node
            and isinstance(node[0], (int, float))
        ):
            yield node
            return
        for child in node:
            yield from walk(child)

    yield from walk(geometry.get("coordinates", []))


def verify_bounds(path: Path, id_key: str) -> Dict[str, Any]:
    """Re-open a written file and assert every vertex is inside WA_BOUNDS.

    Not paranoia: the first run of this script wrote IAML feature 5501-1 at
    latitude 89.99999986 from a corrupt source northing. A check on the artifact
    catches that class of bug; a check on the loop that wrote it does not.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    min_lon, min_lat, max_lon, max_lat = wa_bounds()
    offenders: List[Tuple[str, float, float]] = []
    vertices = 0
    for f in data["features"]:
        ident = str(f["properties"].get(id_key, "?"))
        for pos in _iter_coords(f["geometry"]):
            vertices += 1
            lon, lat = float(pos[0]), float(pos[1])
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                offenders.append((ident, lon, lat))
    if offenders:
        logger.error("%s: %d vertices outside WA_BOUNDS", path.name, len(offenders))
        for ident, lon, lat in offenders[:20]:
            logger.error("   %s at %.6f,%.6f", ident, lon, lat)
    else:
        logger.info(
            "%s: all %d vertices across %d features inside WA_BOUNDS",
            path.name,
            vertices,
            len(data["features"]),
        )
    return {
        "features": len(data["features"]),
        "vertices": vertices,
        "outside_wa_bounds": offenders,
    }


def verify_occurrences(path: Path) -> Dict[str, Any]:
    """Re-open the written file with plain `json` and check it is usable.

    Verifies the artifact rather than the build loop: the runtime only ever sees
    this file, so this is the check that matters. Bounds are the grid module's
    WA_BOUNDS — a point outside them would raise AOIOutOfRangeError downstream.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data["features"]
    min_lon, min_lat, max_lon, max_lat = wa_bounds()
    outliers: List[Tuple[str, str, float, float]] = []
    accuracy: collections.Counter = collections.Counter()
    benchmark_grade = 0
    gold_primary = 0
    docs_present = 0
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            outliers.append((p["uid"], p["name"], lon, lat))
        accuracy[p["accuracy_class"]] += 1
        if p["doc_count"]:
            docs_present += 1
        is_gold = p["commodity_primary"].startswith("Gold")
        gold_primary += int(is_gold)
        if is_gold and p["assays"] and p["production"]:
            benchmark_grade += 1
        # Contract: these are real booleans, never "yes"/"" strings.
        assert isinstance(p["assays"], bool), f"{p['uid']} assays is {type(p['assays'])}"
        assert isinstance(p["production"], bool), p["uid"]

    result = {
        "features": len(feats),
        "collection_properties": sorted(data.get("properties", {}).keys()),
        "outside_wa_bounds": outliers,
        "accuracy_class": dict(accuracy.most_common()),
        "gold_primary": gold_primary,
        "gold_with_assays_and_production": benchmark_grade,
        "sites_with_docs": docs_present,
    }
    if outliers:
        logger.error(
            "%d features outside WA_BOUNDS %s:", len(outliers), (min_lon, min_lat, max_lon, max_lat)
        )
        for uid, name, lon, lat in outliers[:20]:
            logger.error("   %s %r at %.6f,%.6f", uid, name, lon, lat)
    else:
        logger.info("all %d features inside WA_BOUNDS", len(feats))
    logger.info(
        "gold-primary sites: %d, of which assays AND production (benchmark grade): %d",
        gold_primary,
        benchmark_grade,
    )
    return result


# --- CLI --------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "what",
        nargs="?",
        default="all",
        choices=["all", "occurrences", "districts", "iaml"],
        help="which extract to build (default: all)",
    )
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--gdb", default=str(DEFAULT_GDB), help="source file geodatabase")
    ap.add_argument(
        "--built-at",
        default=None,
        help="override the built_at timestamp; pass a fixed value to prove the "
        "output is byte-identical across rebuilds",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    gdb = Path(args.gdb)
    if not gdb.exists():
        # data/raw/ is gitignored and absent on a fresh clone. Fail loudly with the
        # provenance pointer rather than writing an empty file that looks built.
        logger.error("No geodatabase at %s — see data/README.md for how to fetch it", gdb)
        return 2
    out_dir = Path(args.out_dir)
    built_at = args.built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    summary: Dict[str, Any] = {}
    if args.what in ("all", "occurrences"):
        summary["occurrences"] = build_occurrences(gdb, out_dir, built_at)
        summary["occurrences_verified"] = verify_occurrences(out_dir / "wa_occurrences.geojson")
    if args.what in ("all", "districts"):
        summary["districts"] = build_districts(gdb, out_dir, built_at)
        summary["districts_verified"] = verify_bounds(
            out_dir / "wa_mining_districts.geojson", "district_id"
        )
    if args.what in ("all", "iaml"):
        summary["iaml"] = build_iaml(gdb, out_dir, built_at)
        summary["iaml_verified"] = verify_bounds(out_dir / "wa_iaml.geojson", "iaml_id")

    print()
    print(json.dumps(summary, indent=2, default=str))

    # Non-zero exit if anything landed outside Washington: a file like that will
    # blow up downstream, so it should fail the build rather than be committed.
    failed = [
        k for k in ("occurrences_verified", "districts_verified", "iaml_verified")
        if summary.get(k, {}).get("outside_wa_bounds")
    ]
    if failed:
        logger.error("verification failed for: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
