#!/usr/bin/env python3
"""
Build data/derived/wa_geology.sqlite — the statewide surface-geology store.

Source: data/raw/ger_portal_surface_geology_24k/WGS_Surface_Geology_24k.gdb
(WA DNR / Washington Geological Survey, Surface Geology 1:24,000). 209 MB of
ESRI File Geodatabase that no code in this repo has ever read. This script is
what finally grounds the structure agent: 82,692 geologic unit polygons plus
~18,300 faults, folds, dikes and volcanic vents, in a file that runtime code can
open with nothing but `sqlite3` + `shapely` — no pyogrio, no GDAL, no PostGIS.

    .venv/bin/python scripts/build_geology_store.py            # full build
    .venv/bin/python scripts/build_geology_store.py --limit 500 --no-vacuum

Schema is fixed by the change-set contract (tables meta, unit, poly, poly_idx,
lin, lin_idx + the lin_kind index). Do not "improve" it here without changing
`backend/app/spatial/local_store.py`, which reads it.

Design notes worth knowing before you edit this:

* **No spatial push-down.** `bbox=`/`mask=` return 0 features on this geodatabase
  (stale `.spx` sidecars), so every layer is read in full. Do not add a bbox
  filter expecting it to work — it will silently produce an empty store.
* **Paged reads, not one big read.** `pyogrio.raw.read` materialises a whole
  layer; `geologic_unit_poly` alone is 158.6 MB of WKB across 9.79 M vertices.
  Doing the obvious thing (read all, parse all, reproject all, simplify all)
  peaks at **1184 MB RSS** — measured. Paging with `skip_features`/
  `max_features` peaks at **373 MB** at the default batch of 4000, of which
  272 MB is GDAL's own footprint once the geodatabase is open, so the part this
  script controls is about 100 MB. Peak is not linear in batch size because of
  that floor: 500 -> 331 MB but 24.7 s, 4000 -> 373 MB and 12.1 s,
  20000 -> 653 MB and 10.8 s. 4000 is the knee.
  Verified that paging indexes correctly on this OpenFileGDB: feature 5000
  fetched via a page is byte-identical to row 5000 of a full read.
  The true process peak (~403 MB) comes from `verify()`, which deliberately
  parses every stored geometry once to cross-check the rtree.
* **EPSG:2927 → EPSG:4326 for storage, EPSG:5070 for measurement.** The
  geodatabase is NAD83(HARN) / Washington South in US survey feet. Stored WKB is
  WGS84 so it drops straight into GeoJSON and the rtree; azimuth and length are
  computed in Conus Albers metres because you cannot do trigonometry on degrees
  and get a bearing.
* **GDAL emits `organizePolygons() received a polygon with more than 100 parts`**
  on a handful of Quaternary units. It is a performance warning, not corruption;
  it is left unsuppressed so a real problem would still be visible.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from math import atan2, degrees
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
import pyogrio
import pyogrio.raw
import pyproj
import shapely

logger = logging.getLogger("build_geology_store")

REPO_ROOT = Path(__file__).resolve().parents[1]
GDB_PATH = (
    REPO_ROOT
    / "data"
    / "raw"
    / "ger_portal_surface_geology_24k"
    / "WGS_Surface_Geology_24k.gdb"
)
OUT_PATH = REPO_ROOT / "data" / "derived" / "wa_geology.sqlite"

# Cite exactly this string in agent `data_sources_used` (contract invariant 8).
SOURCE_NAME = "WA_DNR_WGS_Surface_Geology_24k"
STORE_VERSION = "1"

SRC_CRS = "EPSG:2927"  # NAD83(HARN) / Washington South, US survey feet
WGS84 = "EPSG:4326"
METRIC_CRS = "EPSG:5070"  # NAD83 / Conus Albers — the analysis CRS (grid.py)

# Douglas-Peucker tolerance for the unit polygons, in DEGREES because that is
# the CRS the geometry is stored in. Simplification error is bounded by the
# tolerance, and degrees are anisotropic: at WA latitudes (~47.5 N) one degree
# of latitude is ~111,132 m and one of longitude ~75,200 m, so 0.00025 deg means
# at worst ~27.8 m N-S and ~18.8 m E-W of positional slop. That is under a
# quarter of the finest cell on RESOLUTION_LADDER (125 m), so a 125 m cell still
# lands on the right unit; going coarser (0.001 deg ≈ 111 m) would start
# swapping units between adjacent fine cells. Measured effect: WKB shrinks to
# ~14% of unsimplified.
SIMPLIFY_DEG = 0.00025

# Post-build rtree round-trip windows, (min_lon, min_lat, max_lon, max_lat).
#
# REPUBLIC_WINDOW is the Republic graben, the most-cited district in
# knowledge/historical/gold.md. MEASURED RESULT: it returns ZERO features, and
# that is a property of the source data, not of this index — the 1:24,000
# mapping is published quad by quad and only 342 quads exist statewide. There is
# no 24k coverage anywhere in lon -119.3..-118.4 / lat 48.3..49.0, so the whole
# Republic-Curlew-Toroda Creek corridor is a hole. Any consumer must degrade
# gracefully over Republic rather than assume geology is always there.
#
# CONTROL_WINDOW is therefore what the build actually asserts on: the
# Marcus/Boyds/Bangs Mountain quads north of Kettle Falls, inside the
# Colville-Metaline gold country and verified to be mapped. It is cross-checked
# against a linear scan of every stored geometry, so a wrong index is caught by
# arithmetic rather than by hoping the window has data in it.
REPUBLIC_WINDOW = (-118.80, 48.60, -118.68, 48.70)
CONTROL_WINDOW = (-118.25, 48.62, -118.00, 48.75)

# OF01-501's favourable structural trend is 345 deg - 030 deg. Azimuths are
# folded into [0,180) (see _principal_azimuth), which splits that band in two.
FAVOURABLE_TREND = "az <= 30 or az >= 165"

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE unit (
  gunit_txt TEXT PRIMARY KEY,
  name TEXT, age TEXT, lithology TEXT, age_lithology TEXT, description TEXT
);

CREATE TABLE poly (
  id INTEGER PRIMARY KEY,
  gunit_txt TEXT, age_lithology TEXT, quad TEXT, pub_source TEXT,
  wkb BLOB NOT NULL
);
CREATE VIRTUAL TABLE poly_idx USING rtree(id, min_lon, max_lon, min_lat, max_lat);

CREATE TABLE lin (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  code INTEGER, descr TEXT, name TEXT, gunit_txt TEXT, quad TEXT,
  azimuth_deg REAL,
  length_m REAL,
  wkb BLOB NOT NULL
);
CREATE VIRTUAL TABLE lin_idx USING rtree(id, min_lon, max_lon, min_lat, max_lat);
CREATE INDEX lin_kind ON lin(kind);
"""

# kind -> (layer name, column mapping). `None` means the layer has no such
# column: dikes and vents carry a GUNIT_TXT but no name, faults and folds the
# reverse. `fold.DIR_FLG` (vergence direction) exists in the source but has no
# home in the contract schema — see the report if you want it.
LIN_LAYERS: List[Tuple[str, str, Dict[str, Optional[str]]]] = [
    (
        "fault",
        "fault",
        {
            "code": "FAULT_CD",
            "descr": "FAULT_DESC",
            "name": "FAULT_NM",
            "gunit_txt": None,
            "quad": "QUAD_NAME",
        },
    ),
    (
        "fold",
        "fold",
        {
            "code": "FOLD_CD",
            "descr": "FOLD_DESC",
            "name": "FOLD_NM",
            "gunit_txt": None,
            "quad": "QUAD_NAME",
        },
    ),
    (
        "dike",
        "dike",
        {
            "code": "DIKE_CD",
            "descr": "DIKE_DESC",
            "name": None,
            "gunit_txt": "GUNIT_TXT",
            "quad": "QUAD_NAME",
        },
    ),
    (
        "vent",
        "volcanic_vent",
        {
            "code": "VENT_CD",
            "descr": "VENT_DESC",
            "name": None,
            "gunit_txt": "GUNIT_TXT",
            "quad": "QUAD_NAME",
        },
    ),
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _txt(value) -> Optional[str]:
    """Normalise a geodatabase text cell to a non-empty str or None.

    The gdb uses '' rather than NULL for absent text; SQLite NULL is the honest
    representation and lets callers use `IS NOT NULL` instead of `!= ''`.
    """
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _transformer(src: str, dst: str) -> pyproj.Transformer:
    return pyproj.Transformer.from_crs(src, dst, always_xy=True)


def _reproject(geoms: np.ndarray, tf: pyproj.Transformer) -> np.ndarray:
    """Reproject an array of geometries in place.

    `shapely.set_coordinates` mutates its argument, so `geoms` must be a freshly
    parsed array that nothing else holds a reference to. Doing it as one flat
    (N,2) coordinate transform rather than per-geometry is ~50x faster: 576k
    vertices in 0.17 s for a 4000-polygon batch.
    """
    coords = shapely.get_coordinates(geoms)
    if len(coords) == 0:
        return geoms
    x, y = tf.transform(coords[:, 0], coords[:, 1])
    return shapely.set_coordinates(geoms, np.column_stack([x, y]))


def _batches(
    layer: str,
    columns: Optional[List[str]],
    batch_size: int,
    limit: Optional[int],
    gdb: Path,
) -> Iterator[Tuple[int, np.ndarray, Dict[str, np.ndarray]]]:
    """Yield `(offset, wkb_array, {column: values})` pages of a layer.

    NOTE: `meta["fields"]` comes back in the geodatabase's own column order, not
    the order of `columns=`, so callers must index by name. Getting this wrong
    silently swaps whole columns.
    """
    total = int(pyogrio.read_info(gdb, layer=layer)["features"])
    if limit is not None:
        total = min(total, limit)
    offset = 0
    while offset < total:
        n = min(batch_size, total - offset)
        meta, _, geom, fields = pyogrio.raw.read(
            gdb,
            layer=layer,
            columns=columns,
            force_2d=True,
            skip_features=offset,
            max_features=n,
        )
        names = list(meta["fields"])
        yield offset, geom, {nm: fields[names.index(nm)] for nm in names}
        offset += n


def _principal_azimuth(line) -> Optional[float]:
    """Trend of a line in EPSG:5070 metres, degrees clockwise from north, [0,180).

    Folded into a half-circle because a fault or fold trace has an orientation
    but no direction: a trace running 020 deg and one running 200 deg are the
    same structure, and leaving them apart would put half of a N-S fault set in
    one bin and half in another. The consequence for callers is that the
    OF01-501 favourable band 345 deg - 030 deg becomes `az <= 30 or az >= 165`.

    First-to-last vertex, per the contract — a chord, not a fitted line. For a
    MultiLineString the longest component sets the trend; averaging unrelated
    splays would invent a direction no mapped structure has. Returns None when
    there is no chord to measure (single vertex, or first vertex == last, i.e. a
    closed trace such as a mapped fold loop).
    """
    if line is None or line.is_empty:
        return None
    type_id = shapely.get_type_id(line)
    if type_id == 5:  # MultiLineString
        parts = shapely.get_parts(line)
        if len(parts) == 0:
            return None
        line = max(parts, key=lambda p: p.length)
    elif type_id != 1:  # not a LineString either (e.g. a vent Point)
        return None
    coords = shapely.get_coordinates(line)
    if len(coords) < 2:
        return None
    dx = float(coords[-1][0] - coords[0][0])
    dy = float(coords[-1][1] - coords[0][1])
    if dx == 0.0 and dy == 0.0:
        return None
    return degrees(atan2(dx, dy)) % 180.0


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def build_unit(con: sqlite3.Connection, gdb: Path, limit: Optional[int]) -> dict:
    """Load `unit_description`, deduped on GUNIT_TXT.

    9,637 rows collapse to 2,186 distinct unit labels because the 24k mapping is
    published quad by quad and the same label is re-described in every quad it
    appears in. When several rows share a label we keep the one with the longest
    UNIT_DESCRIPTION: the descriptions are not contradictory, just of differing
    completeness, and the fullest one is the one an agent can reason from.
    """
    meta, _, _, fields = pyogrio.raw.read(
        gdb,
        layer="unit_description",
        read_geometry=False,
        max_features=limit,
    )
    names = list(meta["fields"])
    col = lambda n: fields[names.index(n)]  # noqa: E731

    gunit = col("GUNIT_TXT")
    best: Dict[str, Tuple[int, tuple]] = {}
    for i in range(len(gunit)):
        key = _txt(gunit[i])
        if key is None:
            continue
        description = _txt(col("UNIT_DESCRIPTION")[i])
        row = (
            key,
            _txt(col("GEOLOGIC_UNIT_NAME")[i]),
            # AGE is the coarse era ("Quaternary"); GEOLOGIC_UNIT_AGE is finer
            # ("Holocene and Pleistocene"). The contract names AGE, so AGE wins,
            # but 186 rows leave it blank and the finer column is better than
            # nothing there.
            _txt(col("AGE")[i]) or _txt(col("GEOLOGIC_UNIT_AGE")[i]),
            _txt(col("LITHOLOGY")[i]),
            _txt(col("AGE_LITHOLOGY")[i]),
            description,
        )
        length = len(description or "")
        if key not in best or length > best[key][0]:
            best[key] = (length, row)

    rows = [v[1] for v in best.values()]
    con.executemany(
        "INSERT INTO unit (gunit_txt, name, age, lithology, age_lithology, description) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    logger.info("unit: %d source rows -> %d distinct gunit_txt", len(gunit), len(rows))
    return {"source_rows": int(len(gunit)), "rows": len(rows)}


def build_poly(
    con: sqlite3.Connection,
    gdb: Path,
    batch_size: int,
    limit: Optional[int],
    tolerance: float,
) -> dict:
    """Stream `geologic_unit_poly` into `poly` + `poly_idx`.

    One transaction per batch. Fallback rule from the contract: if simplifying
    yields an empty or invalid geometry we store the *unsimplified* one rather
    than drop the polygon — a polygon missing from the store is a hole in the
    map with no error attached to it, which is far worse than a fat polygon.
    """
    tf = _transformer(SRC_CRS, WGS84)
    columns = ["GUNIT_TXT", "AGE_LITHOLOGY", "QUAD_NAME", "PUB_SOURCE"]
    next_id = 1
    stats = {
        "rows": 0,
        "null_geometry_skipped": 0,
        "fallback_unsimplified": 0,
        "fallback_source_also_invalid": 0,
        "source_invalid": 0,
        "wkb_bytes": 0,
    }
    t0 = time.time()

    for offset, geom_wkb, cols in _batches(
        "geologic_unit_poly", columns, batch_size, limit, gdb
    ):
        keep = np.array([g is not None for g in geom_wkb])
        stats["null_geometry_skipped"] += int((~keep).sum())

        geoms = _reproject(shapely.from_wkb(geom_wkb), tf)
        simplified = shapely.simplify(geoms, tolerance, preserve_topology=True)

        src_valid = shapely.is_valid(geoms)
        simp_ok = shapely.is_valid(simplified) & ~shapely.is_empty(simplified)
        stats["source_invalid"] += int((~src_valid & keep).sum())

        rows: List[tuple] = []
        idx_rows: List[tuple] = []
        for i in range(len(geoms)):
            if not keep[i]:
                continue
            g = simplified[i]
            if not simp_ok[i]:
                g = geoms[i]
                stats["fallback_unsimplified"] += 1
                if not src_valid[i]:
                    stats["fallback_source_also_invalid"] += 1
            wkb = shapely.to_wkb(g)
            minx, miny, maxx, maxy = g.bounds
            rows.append(
                (
                    next_id,
                    _txt(cols["GUNIT_TXT"][i]),
                    _txt(cols["AGE_LITHOLOGY"][i]),
                    _txt(cols["QUAD_NAME"][i]),
                    _txt(cols["PUB_SOURCE"][i]),
                    wkb,
                )
            )
            idx_rows.append((next_id, minx, maxx, miny, maxy))
            stats["wkb_bytes"] += len(wkb)
            next_id += 1

        con.executemany(
            "INSERT INTO poly (id, gunit_txt, age_lithology, quad, pub_source, wkb) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.executemany(
            "INSERT INTO poly_idx (id, min_lon, max_lon, min_lat, max_lat) "
            "VALUES (?, ?, ?, ?, ?)",
            idx_rows,
        )
        con.commit()
        stats["rows"] += len(rows)
        logger.info(
            "poly: %6d/%s stored (offset %d, %.1fs elapsed)",
            stats["rows"],
            "?" if limit is None else limit,
            offset,
            time.time() - t0,
        )

    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def build_lin(
    con: sqlite3.Connection,
    gdb: Path,
    batch_size: int,
    limit: Optional[int],
) -> dict:
    """Stream fault + fold + dike + volcanic_vent into the single `lin` table.

    Four source layers, one destination, because every consumer asks the same
    question of all of them ("what structures are near this cell, and which way
    do they run"). `kind` keeps them separable and `lin_kind` makes that cheap.

    Geometry is stored in WGS84; `azimuth_deg` and `length_m` are measured on a
    second, EPSG:5070 copy of the same features. Vents are Points, so they get
    `azimuth_deg = NULL` and `length_m = 0.0` — they are in this table for
    proximity, not for trend.
    """
    tf_wgs = _transformer(SRC_CRS, WGS84)
    tf_metric = _transformer(SRC_CRS, METRIC_CRS)
    next_id = 1
    stats: dict = {
        "rows": 0,
        "by_kind": {},
        "null_geometry_skipped": 0,
        "azimuth_null_nonvent": 0,
        "wkb_bytes": 0,
    }
    t0 = time.time()

    for kind, layer, mapping in LIN_LAYERS:
        columns = sorted({c for c in mapping.values() if c})
        kind_rows = 0
        for offset, geom_wkb, cols in _batches(
            layer, columns, batch_size, limit, gdb
        ):
            # dtype=bool explicitly: an empty page would otherwise give a float
            # array and `~keep` would raise instead of being a no-op.
            keep = np.array([g is not None for g in geom_wkb], dtype=bool)
            stats["null_geometry_skipped"] += int((~keep).sum())

            # Parsed twice on purpose: set_coordinates mutates, so the WGS84 and
            # the metric copy cannot share geometry objects. ~18k features total,
            # so the second parse is free.
            geoms = _reproject(shapely.from_wkb(geom_wkb), tf_wgs)
            metric = _reproject(shapely.from_wkb(geom_wkb), tf_metric)
            lengths = shapely.length(metric)

            def cell(field: str, i: int, cols=cols, mapping=mapping):
                """Value of a mapped column, or None if this layer lacks it."""
                src = mapping[field]
                return None if src is None else cols[src][i]

            rows: List[tuple] = []
            idx_rows: List[tuple] = []
            for i in range(len(geoms)):
                if not keep[i]:
                    continue
                azimuth = None if kind == "vent" else _principal_azimuth(metric[i])
                if azimuth is None and kind != "vent":
                    stats["azimuth_null_nonvent"] += 1
                wkb = shapely.to_wkb(geoms[i])
                minx, miny, maxx, maxy = geoms[i].bounds
                rows.append(
                    (
                        next_id,
                        kind,
                        _int(cell("code", i)),
                        _txt(cell("descr", i)),
                        _txt(cell("name", i)),
                        _txt(cell("gunit_txt", i)),
                        _txt(cell("quad", i)),
                        azimuth,
                        float(lengths[i]),
                        wkb,
                    )
                )
                idx_rows.append((next_id, minx, maxx, miny, maxy))
                stats["wkb_bytes"] += len(wkb)
                next_id += 1
                kind_rows += 1

            con.executemany(
                "INSERT INTO lin (id, kind, code, descr, name, gunit_txt, quad, "
                "azimuth_deg, length_m, wkb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            con.executemany(
                "INSERT INTO lin_idx (id, min_lon, max_lon, min_lat, max_lat) "
                "VALUES (?, ?, ?, ?, ?)",
                idx_rows,
            )
            con.commit()
            stats["rows"] += len(rows)

        stats["by_kind"][kind] = kind_rows
        logger.info("lin: %s -> %d rows (%.1fs elapsed)", kind, kind_rows, time.time() - t0)

    stats["seconds"] = round(time.time() - t0, 1)
    return stats


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def _rtree_query(con: sqlite3.Connection, table: str, window: tuple) -> List[int]:
    """Ids whose stored bbox intersects `window` = (min_lon, min_lat, max_lon, max_lat).

    Standard bbox overlap test. The parameter order below is deliberately
    scrambled relative to the window tuple — each stored bound is compared with
    the *opposite* bound of the query — and getting it wrong is the classic way
    to build an index that returns plausible-looking nonsense.
    """
    min_lon, min_lat, max_lon, max_lat = window
    return [
        r[0]
        for r in con.execute(
            f"SELECT id FROM {table} WHERE min_lon <= ? AND max_lon >= ? "
            f"AND min_lat <= ? AND max_lat >= ?",
            (max_lon, min_lon, max_lat, min_lat),
        )
    ]


def _scan_query(con: sqlite3.Connection, table: str, window: tuple) -> List[int]:
    """Same question answered without the index: parse every WKB and test it.

    This is the actual proof that the rtree is populated correctly. Comparing
    the index against geographic expectation only tells you whether the window
    you picked happens to have data; comparing it against a linear scan of the
    stored geometry tells you whether the index agrees with the truth it was
    built from. ~82k geometries parse in well under a second — but they all exist
    at once, which makes this function, not the build loop, the process's memory
    high-water mark (~403 MB vs ~373 MB).
    """
    min_lon, min_lat, max_lon, max_lat = window
    ids: List[int] = []
    rows = con.execute(f"SELECT id, wkb FROM {table}").fetchall()
    geoms = shapely.from_wkb([r[1] for r in rows])
    bounds = shapely.bounds(geoms)
    for i, (row_id, _) in enumerate(rows):
        bx0, by0, bx1, by1 = bounds[i]
        if bx0 <= max_lon and bx1 >= min_lon and by0 <= max_lat and by1 >= min_lat:
            ids.append(row_id)
    return ids


def _window_report(con: sqlite3.Connection, window: tuple) -> dict:
    poly_ids = _rtree_query(con, "poly_idx", window)
    lin_ids = _rtree_query(con, "lin_idx", window)

    units: Counter = Counter()
    if poly_ids:
        marks = ",".join("?" * len(poly_ids))
        for (gunit,) in con.execute(
            f"SELECT gunit_txt FROM poly WHERE id IN ({marks})", poly_ids
        ):
            units[gunit or "(null)"] += 1
    kinds: Counter = Counter()
    if lin_ids:
        marks = ",".join("?" * len(lin_ids))
        for (kind,) in con.execute(
            f"SELECT kind FROM lin WHERE id IN ({marks})", lin_ids
        ):
            kinds[kind] += 1

    return {
        "window": list(window),
        "polys": len(poly_ids),
        "lins": len(lin_ids),
        "top_units": units.most_common(8),
        "lin_kinds": dict(kinds),
    }


def verify(con: sqlite3.Connection) -> dict:
    """Prove the rtree indexes answer queries, measure coverage, profile trends.

    A silently mis-populated rtree (lon/lat swapped, or bounds left in the
    source CRS) is the easiest way to ship a store that looks fine and returns
    nothing at runtime. Three independent checks here: index vs linear scan on a
    control window, a coverage census so the source's quad-by-quad holes are
    recorded in `meta`, and the fault-azimuth histogram that the structure
    agent's favourable-trend rule depends on.
    """
    checks: dict = {
        "republic": _window_report(con, REPUBLIC_WINDOW),
        "control": _window_report(con, CONTROL_WINDOW),
    }

    # Index vs. brute force on the control window, for both rtrees.
    for table, idx in (("poly", "poly_idx"), ("lin", "lin_idx")):
        via_index = set(_rtree_query(con, idx, CONTROL_WINDOW))
        via_scan = set(_scan_query(con, table, CONTROL_WINDOW))
        checks[f"{table}_index_matches_scan"] = via_index == via_scan
        checks[f"{table}_index_hits"] = len(via_index)
        checks[f"{table}_scan_hits"] = len(via_scan)

    # Coverage census. The 24k mapping is not statewide; count the occupied
    # 7.5-minute (0.125 deg) tiles so downstream code can be told honestly how
    # much of WA has any geology at all.
    tiles = {
        (int(lo // 0.125), int(la // 0.125))
        for lo, _, la, _ in con.execute(
            "SELECT min_lon, max_lon, min_lat, max_lat FROM poly_idx"
        )
    }
    checks["coverage"] = {
        "distinct_quad_names": con.execute(
            "SELECT count(DISTINCT quad) FROM poly"
        ).fetchone()[0],
        "occupied_quarter_quad_tiles_0p125deg": len(tiles),
        "poly_extent": list(
            con.execute(
                "SELECT min(min_lon), min(min_lat), max(max_lon), max(max_lat) "
                "FROM poly_idx"
            ).fetchone()
        ),
        "note": (
            "1:24k mapping is published quad by quad and is NOT statewide. "
            "No coverage over the Republic / Curlew / Toroda Creek corridor."
        ),
    }

    # How well `poly.gunit_txt` joins to `unit.gunit_txt`. A caller that assumes
    # the join always lands will silently drop unit names and ages.
    unmatched = con.execute(
        "SELECT gunit_txt, count(*) FROM poly "
        "WHERE gunit_txt IS NULL OR gunit_txt NOT IN (SELECT gunit_txt FROM unit) "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    checks["unit_join"] = {
        "poly_rows_without_unit_row": sum(n for _, n in unmatched),
        "distinct_labels_without_unit_row": len(unmatched),
        "top_unmatched": [[k, n] for k, n in unmatched[:10]],
        "note": (
            "dominated by non-geologic map polygons (wtr, wa, ice, marsh); the "
            "rest are quad-local variants, ~11 of which resolve if you strip "
            "surrounding parentheses."
        ),
    }

    # Fault azimuth distribution over the whole state, in 15-degree bins.
    azimuths = [
        r[0]
        for r in con.execute(
            "SELECT azimuth_deg FROM lin WHERE kind = 'fault' AND azimuth_deg IS NOT NULL"
        )
    ]
    bins: Counter = Counter()
    for az in azimuths:
        bins[int(az // 15) * 15] += 1
    checks["fault_azimuths_measured"] = len(azimuths)
    checks["fault_azimuth_bins_15deg"] = {str(k): bins[k] for k in sorted(bins)}
    checks["fault_favourable_trend"] = sum(
        1 for az in azimuths if az <= 30.0 or az >= 165.0
    )
    checks["fault_favourable_rule"] = FAVOURABLE_TREND
    return checks


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--gdb", default=str(GDB_PATH), help="source geodatabase")
    ap.add_argument("--out", default=str(OUT_PATH), help="sqlite file to write")
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="read at most N features per layer (smoke test)",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=4000,
        help="features per read/insert transaction (default 4000)",
    )
    ap.add_argument(
        "--simplify",
        type=float,
        default=SIMPLIFY_DEG,
        metavar="DEG",
        help=f"polygon simplify tolerance in degrees (default {SIMPLIFY_DEG})",
    )
    ap.add_argument(
        "--vacuum",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="VACUUM at the end (default on)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    gdb = Path(args.gdb)
    if not gdb.exists():
        logger.error(
            "geodatabase not found: %s\n"
            "data/raw/ is gitignored and absent on a fresh clone — download the "
            "WA DNR Surface Geology 24k package (see data/README.md).",
            gdb,
        )
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        # Rebuild rather than append: ids restart at 1 every run, so appending
        # would collide on the primary key and silently desync the rtrees.
        logger.info("removing existing %s", out)
        out.unlink()

    t0 = time.time()
    con = sqlite3.connect(out)
    # This file is a derived artifact rebuilt from data/raw/ in a couple of
    # minutes; durability against a mid-build crash is not worth ~100k fsyncs.
    con.execute("PRAGMA journal_mode = MEMORY")
    con.execute("PRAGMA synchronous = OFF")
    con.executescript(DDL)
    con.commit()

    counts = {
        "unit": build_unit(con, gdb, args.limit),
        "poly": build_poly(con, gdb, args.batch_size, args.limit, args.simplify),
        "lin": build_lin(con, gdb, args.batch_size, args.limit),
    }
    counts["simplify_tolerance_deg"] = args.simplify
    counts["source_crs"] = SRC_CRS
    counts["stored_crs"] = WGS84
    counts["measurement_crs"] = METRIC_CRS
    counts["limit"] = args.limit

    checks = verify(con)
    counts["verification"] = checks

    con.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [
            ("built_at", datetime.now(timezone.utc).isoformat()),
            ("source", SOURCE_NAME),
            ("version", STORE_VERSION),
            ("counts", json.dumps(counts)),
        ],
    )
    con.commit()

    if args.vacuum:
        logger.info("vacuuming")
        con.execute("VACUUM")
    con.close()

    size_mb = out.stat().st_size / 1e6
    elapsed = time.time() - t0

    print()
    print(f"Wrote {out}  ({size_mb:.1f} MB, {elapsed:.1f}s wall)")
    print(f"  unit  {counts['unit']['rows']:>7d} rows "
          f"(from {counts['unit']['source_rows']} source rows)")
    print(f"  poly  {counts['poly']['rows']:>7d} rows  "
          f"simplify={args.simplify} deg  "
          f"wkb={counts['poly']['wkb_bytes'] / 1e6:.1f} MB")
    print(f"        unsimplified fallbacks: {counts['poly']['fallback_unsimplified']} "
          f"(of which source geometry also invalid: "
          f"{counts['poly']['fallback_source_also_invalid']}); "
          f"invalid in source: {counts['poly']['source_invalid']}")
    print(f"  lin   {counts['lin']['rows']:>7d} rows  {counts['lin']['by_kind']}")
    print(f"        azimuth NULL on non-vent lines: "
          f"{counts['lin']['azimuth_null_nonvent']} (closed or single-vertex traces)")
    cov = checks["coverage"]
    print(f"  coverage: {cov['distinct_quad_names']} distinct QUAD_NAMEs, "
          f"{cov['occupied_quarter_quad_tiles_0p125deg']} occupied 0.125-deg tiles")
    print(f"            extent {[round(v, 3) for v in cov['poly_extent']]}")
    print(f"  unit join: {checks['unit_join']['poly_rows_without_unit_row']} poly rows "
          f"({checks['unit_join']['distinct_labels_without_unit_row']} labels) have no "
          f"unit row; top {checks['unit_join']['top_unmatched'][:4]}")
    print()
    for label, key in (("Republic", "republic"), ("control (Marcus/Boyds)", "control")):
        w = checks[key]
        print(f"  rtree window {label} {tuple(w['window'])}:")
        print(f"    polys={w['polys']}  lins={w['lins']} {w['lin_kinds']}")
        print(f"    top units: {w['top_units']}")
    print(f"  index vs linear scan on the control window: "
          f"poly {checks['poly_index_hits']}=={checks['poly_scan_hits']} "
          f"{checks['poly_index_matches_scan']}, "
          f"lin {checks['lin_index_hits']}=={checks['lin_scan_hits']} "
          f"{checks['lin_index_matches_scan']}")
    print()
    print(f"  fault trend: {checks['fault_favourable_trend']}/"
          f"{checks['fault_azimuths_measured']} in the OF01-501 favourable band "
          f"({FAVOURABLE_TREND})")
    print(f"    15-deg bins: {checks['fault_azimuth_bins_15deg']}")

    if checks["republic"]["polys"] == 0 and args.limit is None:
        print()
        print("WARNING: the Republic window is empty. This is the source data, "
              "not the index — the 1:24k mapping has no quads over Republic "
              "(verified: nothing in lon -119.3..-118.4 / lat 48.3..49.0). "
              "Consumers must degrade over that district.")

    if args.limit is not None:
        # Layers are read in feature order, which has nothing to do with
        # geography, so a truncated build contains no particular window. The
        # index/scan agreement check below is still meaningful; the window
        # counts are not.
        print(f"\nNOTE: --limit {args.limit} was set; window counts above are "
              "not meaningful. Run without --limit for the real check.")
        return 0 if checks["poly_index_matches_scan"] else 1

    ok = (
        checks["poly_index_matches_scan"]
        and checks["lin_index_matches_scan"]
        and checks["control"]["polys"] > 0
        and checks["control"]["lins"] > 0
    )
    if not ok:
        print()
        print("FAIL: the control window came back empty or the rtree disagrees "
              "with a linear scan of the stored geometry. The index is wrong "
              "(check the lon/lat column order in the INSERT) or the "
              "reprojection failed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
