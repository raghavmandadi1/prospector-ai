"""
Statewide bedrock geology and mapped structures, from WA DNR 1:24,000.

Source is the Washington Geological Survey *Surface Geology 1:24k* geodatabase
(82,692 unit polygons, 12,416 faults, 3,350 folds, 2,467 dikes), converted to
``data/derived/wa_geology.sqlite`` by ``scripts/build_geology_store.py``. Runtime
access needs only ``sqlite3`` and ``shapely``: an R*Tree virtual table narrows
statewide coverage to an AOI window in a millisecond, and geometry comes back as
WGS84 WKB.

This is the dataset that ends the structure agent's data blackout. Until now it
had neither a knowledge file nor a single mapped fault, and it carries the
highest gold weight of any agent (0.30) — so the single largest contributor to
every gold composite was a model guessing at structure from coordinates.

**Two calibration warnings that belong next to the data, not in a docstring
nobody reads.**

*Fault density partly measures mapping intensity.* At 1:24,000 the source is a
mosaic of quadrangle maps compiled by different authors over decades. Quad
boundaries produce real, visible steps in fault density that are cartographic,
not tectonic. A cell is not more prospective because a more thorough geologist
mapped it. `quad` is carried on every record so that step is at least visible.

*Unit labels are quad-local.* `GUNIT_TXT` values like ``Evs(t)`` and ``Ev(p)``
are scoped to the publication they came from — the same rock can carry different
labels either side of a quad line, and the 24k labels do **not** match the
OF01-501 weights-of-evidence codes (``Evsf``, ``Eck``, ``Eco`` and the rest are
absent from all 2,186 distinct values here). That is precisely why
``spatial/wofe_grid.py`` exists as a separate source: OF-00-495 is the only
raster keyed to the published contrasts. Do not try to match 24k labels against
the WofE table by string similarity — it will look like it works.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely import from_wkb

from app.config import DERIVED_DIR
from app.spatial.geometry import LocalMetric

logger = logging.getLogger(__name__)

GEOLOGY_DB = DERIVED_DIR / "wa_geology.sqlite"

#: Citation string for `data_sources_used`.
GEOLOGY_CITATION = "WA_DNR_WGS_Surface_Geology_24k"

#: Optimum fault buffer from the USGS OF01-501 weights-of-evidence study of NE
#: Washington: normal faults trending 345°–030° correlate best with epithermal
#: gold at 1,700 m. Cells are 250–2000 m across, so this is between one and
#: seven cell widths — it is a real spatial term, not a rounding error.
WOFE_FAULT_BUFFER_KM = 1.7

#: Optimum lithologic buffer from the same study: mineralisation extends about
#: 150 m beyond a mapped contact, so a cell within 150 m of a favourable unit
#: behaves as if it contained it.
WOFE_LITHOLOGY_BUFFER_KM = 0.15


def in_favourable_trend(azimuth_deg: Optional[float]) -> bool:
    """Does a structure trend NW-to-NNE, the OF01-501 favourable band?

    The published band is 345°–030°, i.e. the arc through north. A fault has no
    direction — a trace at 350° and one at 170° are the same structure walked
    the other way — so `azimuth_deg` is stored folded into [0, 180). Folding
    345°–360° gives 165°–180°, and 000°–030° stays 0°–30°. Hence the band
    becomes two intervals, and testing only ``az <= 30`` would silently discard
    every NNW-trending fault, which is most of the Republic graben.
    """
    if azimuth_deg is None:
        return False
    az = float(azimuth_deg) % 180.0
    return az <= 30.0 or az >= 165.0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass
class GeologyWindow:
    """Everything the store holds inside one AOI window, already projected."""

    metric: LocalMetric
    unit_props: List[Dict[str, Any]] = field(default_factory=list)
    unit_geoms: Any = None
    lin_props: List[Dict[str, Any]] = field(default_factory=list)
    lin_geoms: Any = None

    @property
    def has_units(self) -> bool:
        return bool(self.unit_props)

    @property
    def has_structures(self) -> bool:
        return bool(self.lin_props)


class GeologyStore:
    """Read-only view over ``wa_geology.sqlite``.

    Opened per process and reused. A missing or unreadable database is not an
    error: ``available`` goes false, every query returns empty, and the run
    proceeds with a thinner prompt. `data/derived/` is gitignored and absent on
    a fresh clone, so that is the normal state until someone runs the build
    script.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else GEOLOGY_DB
        self._conn: Optional[sqlite3.Connection] = None
        self._meta: Optional[Dict[str, Any]] = None
        self._units: Optional[Dict[str, Dict[str, Any]]] = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._connect() is not None

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        if not self.path.exists():
            logger.info(
                "No geology store at %s — lithology and structure agents will "
                "run without mapped geology (build it with "
                "scripts/build_geology_store.py)",
                self.path,
            )
            return None
        try:
            # check_same_thread=False: the orchestrator builds context from an
            # asyncio task, and reads here are read-only and serialised by the
            # GIL for our access pattern.
            conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            self._conn = conn
            return conn
        except Exception as exc:
            logger.warning("Could not open geology store %s: %s", self.path, exc)
            return None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def meta(self) -> Dict[str, Any]:
        if self._meta is not None:
            return self._meta
        conn = self._connect()
        out: Dict[str, Any] = {}
        if conn is not None:
            try:
                for row in conn.execute("SELECT key, value FROM meta"):
                    out[row["key"]] = row["value"]
            except Exception as exc:
                logger.warning("Geology store meta unreadable: %s", exc)
        self._meta = out
        return out

    # -- unit descriptions -------------------------------------------------

    def unit_descriptions(self) -> Dict[str, Dict[str, Any]]:
        """`gunit_txt` -> {name, age, lithology, description}, loaded once.

        2,186 rows; small enough to hold entirely and far cheaper than a query
        per polygon per cell.
        """
        if self._units is not None:
            return self._units
        conn = self._connect()
        out: Dict[str, Dict[str, Any]] = {}
        if conn is not None:
            try:
                for row in conn.execute(
                    "SELECT gunit_txt, name, age, lithology, description FROM unit"
                ):
                    out[row["gunit_txt"]] = {
                        "name": row["name"],
                        "age": row["age"],
                        "lithology": row["lithology"],
                        "description": row["description"],
                    }
            except Exception as exc:
                logger.warning("Geology unit table unreadable: %s", exc)
        self._units = out
        return out

    # -- windowed reads ----------------------------------------------------

    def window(
        self, bbox: Sequence[float], metric: LocalMetric, max_features: int = 20000
    ) -> GeologyWindow:
        """Load and project every unit polygon and structure line in a bbox."""
        empty = GeologyWindow(metric=metric)
        conn = self._connect()
        if conn is None:
            return empty

        min_lon, min_lat, max_lon, max_lat = (
            bbox[0],
            bbox[1],
            bbox[2],
            bbox[3],
        )
        descriptions = self.unit_descriptions()

        unit_props: List[Dict[str, Any]] = []
        unit_geoms: List[Any] = []
        lin_props: List[Dict[str, Any]] = []
        lin_geoms: List[Any] = []

        try:
            poly_rows = conn.execute(
                """
                SELECT p.gunit_txt, p.age_lithology, p.quad, p.wkb
                  FROM poly_idx i JOIN poly p ON p.id = i.id
                 WHERE i.max_lon >= ? AND i.min_lon <= ?
                   AND i.max_lat >= ? AND i.min_lat <= ?
                 LIMIT ?
                """,
                (min_lon, max_lon, min_lat, max_lat, max_features),
            ).fetchall()
        except Exception as exc:
            logger.warning("Geology polygon query failed: %s", exc)
            poly_rows = []

        for row in poly_rows:
            geom = _project_wkb(row["wkb"], metric)
            if geom is None:
                continue
            code = row["gunit_txt"]
            desc = descriptions.get(code, {})
            unit_props.append(
                {
                    "unit": code,
                    "name": desc.get("name"),
                    "age": desc.get("age"),
                    "lithology": desc.get("lithology") or row["age_lithology"],
                    "quad": row["quad"],
                }
            )
            unit_geoms.append(geom)

        try:
            lin_rows = conn.execute(
                """
                SELECT l.kind, l.descr, l.name, l.gunit_txt, l.quad,
                       l.azimuth_deg, l.length_m, l.wkb
                  FROM lin_idx i JOIN lin l ON l.id = i.id
                 WHERE i.max_lon >= ? AND i.min_lon <= ?
                   AND i.max_lat >= ? AND i.min_lat <= ?
                 LIMIT ?
                """,
                (min_lon, max_lon, min_lat, max_lat, max_features),
            ).fetchall()
        except Exception as exc:
            logger.warning("Geology structure query failed: %s", exc)
            lin_rows = []

        for row in lin_rows:
            geom = _project_wkb(row["wkb"], metric)
            if geom is None:
                continue
            lin_props.append(
                {
                    "kind": row["kind"],
                    "descr": row["descr"],
                    "name": row["name"],
                    "unit": row["gunit_txt"],
                    "quad": row["quad"],
                    "azimuth_deg": row["azimuth_deg"],
                    "length_m": row["length_m"],
                }
            )
            lin_geoms.append(geom)

        if len(poly_rows) >= max_features or len(lin_rows) >= max_features:
            # Never truncate silently — a capped window means the agents saw
            # part of the geology and had no way to know.
            logger.warning(
                "Geology window hit the %d-feature cap (%d polys, %d lines); "
                "AOI may be larger than this store is meant to serve",
                max_features,
                len(poly_rows),
                len(lin_rows),
            )

        logger.info(
            "Geology window: %d unit polygons, %d structures",
            len(unit_props),
            len(lin_props),
        )
        return GeologyWindow(
            metric=metric,
            unit_props=unit_props,
            unit_geoms=np.asarray(unit_geoms, dtype=object) if unit_geoms else None,
            lin_props=lin_props,
            lin_geoms=np.asarray(lin_geoms, dtype=object) if lin_geoms else None,
        )


def _project_wkb(blob: Any, metric: LocalMetric):
    """WKB (WGS84) -> shapely geometry in the AOI's metre frame."""
    if not blob:
        return None
    try:
        geom = from_wkb(bytes(blob))
    except Exception:
        return None
    if geom is None or geom.is_empty:
        return None
    try:
        projected = metric.project(geom)
    except Exception:
        return None
    if projected is None or projected.is_empty:
        return None
    return projected


# ---------------------------------------------------------------------------
# Per-cell facts
# ---------------------------------------------------------------------------


def geology_for_cell(
    window: GeologyWindow,
    cell_geom_projected,
    max_units: int,
) -> List[Dict[str, Any]]:
    """Which rock units underlie a cell, and in what proportion.

    Returned largest-first with an area fraction, so a prompt can say "62%
    Sanpoil andesite, 31% Quaternary till" rather than naming one unit and
    implying the cell is uniform. Cells straddling a contact are exactly the
    interesting ones — orogenic gold at Blewett and Monte Cristo concentrates at
    schist–amphibolite contacts precisely because of the competency contrast —
    so flattening a cell to its modal unit throws away the signal.
    """
    if not window.has_units:
        return []

    cell_area = cell_geom_projected.area
    if cell_area <= 0:
        return []

    hits = shapely.intersects(cell_geom_projected, window.unit_geoms)
    idx = np.nonzero(hits)[0]
    if idx.size == 0:
        return []

    by_unit: Dict[str, Dict[str, Any]] = {}
    for i in idx:
        i = int(i)
        try:
            inter = shapely.intersection(cell_geom_projected, window.unit_geoms[i])
        except Exception:
            continue
        area = float(shapely.area(inter))
        if area <= 0:
            continue
        p = window.unit_props[i]
        key = str(p.get("unit"))
        entry = by_unit.setdefault(
            key,
            {
                "unit": key,
                "name": p.get("name"),
                "age": p.get("age"),
                "lithology": p.get("lithology"),
                "area": 0.0,
            },
        )
        entry["area"] += area

    ranked = sorted(by_unit.values(), key=lambda e: -e["area"])[:max_units]
    out = []
    for e in ranked:
        frac = round(e.pop("area") / cell_area, 3)
        if frac <= 0.0:
            continue
        e["frac"] = frac
        out.append({k: v for k, v in e.items() if v not in (None, "")})
    return out


def structures_for_cell(
    window: GeologyWindow,
    cell_geom_projected,
    max_named: int,
    buffer_km: float = WOFE_FAULT_BUFFER_KM,
) -> Dict[str, Any]:
    """Mapped structures in and near a cell, with the OF01-501 trend test.

    Reports counts by kind, distance to the nearest of each kind, the principal
    azimuths within ``buffer_km``, and whether any of those falls in the
    favourable NW-to-NNE band. Also flags fault *intersections* inside the cell,
    which are dilatational jogs and the highest-value structural target — a
    single fault trace and two faults crossing are very different prospects and
    a bare count cannot tell them apart.
    """
    if not window.has_structures:
        return {}

    d_m = shapely.distance(cell_geom_projected, window.lin_geoms)
    d_km = d_m / 1000.0
    near = np.nonzero(d_km <= buffer_km)[0]
    if near.size == 0:
        nearest_i = int(np.argmin(d_km))
        return {
            "count": 0,
            "nearest_km": round(float(d_km[nearest_i]), 2),
            "nearest_kind": window.lin_props[nearest_i].get("kind"),
            "buffer_km": buffer_km,
        }

    kinds: Dict[str, int] = {}
    in_cell_by_kind: Dict[str, List[int]] = {}
    azimuths: List[float] = []
    names: List[str] = []
    nearest_by_kind: Dict[str, float] = {}

    for i in near:
        i = int(i)
        p = window.lin_props[i]
        kind = str(p.get("kind") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
        km = float(d_km[i])
        if kind not in nearest_by_kind or km < nearest_by_kind[kind]:
            nearest_by_kind[kind] = km
        if km == 0.0:
            in_cell_by_kind.setdefault(kind, []).append(i)
        az = p.get("azimuth_deg")
        if az is not None:
            azimuths.append(round(float(az) % 180.0, 1))
        nm = p.get("name")
        if nm and nm not in names:
            names.append(str(nm))

    favourable = [az for az in azimuths if in_favourable_trend(az)]

    out: Dict[str, Any] = {
        "count": int(near.size),
        "kinds": kinds,
        "buffer_km": buffer_km,
        "nearest_km": {k: round(v, 2) for k, v in sorted(nearest_by_kind.items())},
        # Distinct azimuths, coarsest-to-finest reading: a cell cut by three
        # sub-parallel splays is structurally different from one at a junction.
        "azimuths": sorted(set(azimuths))[:8],
        "favourable_trend": bool(favourable),
        "favourable_azimuths": sorted(set(favourable))[:6],
    }
    if names:
        out["named"] = names[:max_named]

    crossings = _count_intersections(window, in_cell_by_kind.get("fault", []))
    if crossings:
        out["fault_intersections_in_cell"] = crossings
    return out


def _count_intersections(window: GeologyWindow, fault_idx: Sequence[int]) -> int:
    """Pairwise crossings among faults inside one cell.

    Bounded deliberately: a cell with 30 fault segments is a mapping artifact of
    one polyline split at quad edges, not thirty faults, and the O(n²) check on
    it would be both slow and meaningless.
    """
    if len(fault_idx) < 2 or len(fault_idx) > 12:
        return 0
    n = 0
    for a in range(len(fault_idx)):
        ga = window.lin_geoms[fault_idx[a]]
        for b in range(a + 1, len(fault_idx)):
            try:
                if shapely.crosses(ga, window.lin_geoms[fault_idx[b]]) or shapely.touches(
                    ga, window.lin_geoms[fault_idx[b]]
                ):
                    n += 1
            except Exception:
                continue
    return n


def summarize_units(
    window: GeologyWindow, limit: int = 40
) -> List[Dict[str, Any]]:
    """AOI-wide unit list for the `geology_units` context key.

    Keeps the existing prompt shape working: `lithology_agent` renders
    `spatial_context["geology_units"]` as JSON, and that branch predates per-cell
    facts. Deduplicated by unit code — the raw window can hold hundreds of
    polygons of the same formation.
    """
    if not window.has_units:
        return []
    seen: Dict[str, Dict[str, Any]] = {}
    for p in window.unit_props:
        code = str(p.get("unit"))
        if code in seen:
            seen[code]["polygons"] += 1
            continue
        seen[code] = {
            "geologic_unit": code,
            "name": p.get("name"),
            "age": p.get("age"),
            "rock_type": p.get("lithology"),
            "polygons": 1,
            "source": GEOLOGY_CITATION,
        }
    ranked = sorted(seen.values(), key=lambda e: -e["polygons"])[:limit]
    return [{k: v for k, v in e.items() if v not in (None, "")} for e in ranked]


def summarize_structures(
    window: GeologyWindow, limit: int = 60
) -> List[Dict[str, Any]]:
    """AOI-wide structure list for the existing `fault_traces` context key."""
    if not window.has_structures:
        return []
    out = []
    for p, geom in zip(window.lin_props, window.lin_geoms):
        az = p.get("azimuth_deg")
        out.append(
            {
                "type": p.get("kind"),
                "name": p.get("name"),
                "description": p.get("descr"),
                "azimuth_deg": round(float(az), 1) if az is not None else None,
                "length_km": round((p.get("length_m") or 0.0) / 1000.0, 2),
                "favourable_trend": in_favourable_trend(az),
                "source": GEOLOGY_CITATION,
            }
        )
    # Longest first: a 12 km graben-bounding fault matters more than a 200 m
    # splay, and the list is capped.
    out.sort(key=lambda e: -(e.get("length_km") or 0.0))
    return [
        {k: v for k, v in e.items() if v not in (None, "")} for e in out[:limit]
    ]


_STORE: Optional[GeologyStore] = None


def get_store() -> GeologyStore:
    """Process-wide store. Cheap when the database is absent."""
    global _STORE
    if _STORE is None:
        _STORE = GeologyStore()
    return _STORE
