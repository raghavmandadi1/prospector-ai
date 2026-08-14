"""CSV export and coordinate formatting, shared by sweeps and pan points.

There is no export endpoint anywhere else in this project, which has been the
largest missing capability for a while: a scored grid you cannot get out of the
browser is hard to act on in the field. Both "Steps for Raghav 3.0" §42 (ranked
sweep results) and §48.4 (ranked coincidence points) require CSV, and §48.4
additionally requires coordinates in **both** decimal degrees and UTM so the
list is usable on a handheld.

ON THE UTM ZONE
---------------
§48.4 says "UTM 10N". That is right for the NF Snoqualmie corridor and wrong for
a third of the state: zone 10N ends at 120°W, and Republic, Metaline, Toroda
Creek and Colville all sit east of it — Republic being the most-cited district
in the gold knowledge base. This is the same reason the analysis grid is
EPSG:5070 rather than UTM (docs/07_stable_cell_ids.md §2).

So the zone is computed per row and written into its own column rather than
assumed. A northing and easting without a zone is not a position, and silently
projecting Republic into 10N would put it about 200 km from where it is.
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pyproj

#: Cache transformers by zone — building one per row is measurably slow over a
#: few thousand cells and they are stateless once built.
_UTM_CACHE: Dict[int, Any] = {}


def utm_zone_for(lon: float) -> int:
    """UTM zone number for a longitude. Washington spans 10 and 11."""
    return int(math.floor((lon + 180.0) / 6.0) + 1)


def to_utm(lon: float, lat: float) -> Tuple[float, float, str]:
    """(easting, northing, zone_label) in the zone that actually contains the point."""
    zone = utm_zone_for(lon)
    tr = _UTM_CACHE.get(zone)
    if tr is None:
        tr = pyproj.Transformer.from_crs(
            "EPSG:4326", f"EPSG:{32600 + zone}", always_xy=True
        ).transform
        _UTM_CACHE[zone] = tr
    easting, northing = tr(lon, lat)
    return round(easting, 1), round(northing, 1), f"{zone}N"


def to_dms(value: float, is_lat: bool) -> str:
    """Decimal degrees → degrees/minutes/seconds, the form on most GPS units.

    Rounds to hundredths of a second FIRST and then carries, rather than
    truncating each field in turn. The naive order produces 47°38'60.00" for
    47.65 — 0.65 x 60 is 38.99999999999999 in binary floating point, so the
    minutes truncate to 38 and the leftover seconds round up to a full 60. That
    is not a valid coordinate and a GPS will either reject it or read it as the
    wrong minute.
    """
    hemi = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    total_hundredths = round(abs(value) * 3600 * 100)
    d, rem = divmod(total_hundredths, 360000)
    m, rem = divmod(rem, 6000)
    s = rem / 100.0
    return f"{d}°{m:02d}'{s:05.2f}\"{hemi}"


def centroid_of(geometry: Optional[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    if not geometry:
        return None
    try:
        from shapely.geometry import shape

        c = shape(geometry).centroid
        return (c.x, c.y)
    except Exception:
        return None


def coordinate_columns(lon: float, lat: float) -> Dict[str, Any]:
    """Every coordinate representation a field user might want, one row's worth."""
    easting, northing, zone = to_utm(lon, lat)
    return {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "lat_dms": to_dms(lat, True),
        "lon_dms": to_dms(lon, False),
        "utm_zone": zone,
        "utm_easting": easting,
        "utm_northing": northing,
    }


def rows_to_csv(rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> str:
    """Render rows to CSV text with a fixed column order.

    Fixed rather than inferred from the first row: a row missing an optional key
    would otherwise silently shift every later column, and nobody checks a CSV's
    header against its 400th line.
    """
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in columns})
    return buf.getvalue()


SCORED_CELL_COLUMNS = [
    "rank",
    "cell_id",
    "score",
    "confidence",
    "relative_score",
    "percentile",
    "tier",
    "normalization_scope",
    "novelty",
    "nearest_occurrence_km",
    "nearest_occurrence_name",
    "lat",
    "lon",
    "lat_dms",
    "lon_dms",
    "utm_zone",
    "utm_easting",
    "utm_northing",
    "evidence",
    "data_sources_used",
]


def scored_cells_to_csv(cells: Iterable[Dict[str, Any]]) -> str:
    """Ranked scored cells as CSV, coordinates in DD, DMS and UTM.

    Assumes ``cells`` is already in the order you want ranked — this does not
    re-sort, because the caller knows whether it is ranking by regional
    percentile, by absolute score, or by something else, and a second opinion
    here would silently override it.
    """
    rows: List[Dict[str, Any]] = []
    for i, c in enumerate(cells, start=1):
        row: Dict[str, Any] = {
            "rank": i,
            "cell_id": c.get("cell_id"),
            "score": c.get("score"),
            "confidence": c.get("confidence"),
            "relative_score": c.get("relative_score"),
            "percentile": c.get("percentile"),
            "tier": c.get("tier"),
            "normalization_scope": c.get("normalization_scope"),
            "novelty": c.get("novelty"),
            "nearest_occurrence_km": c.get("nearest_occurrence_km"),
            "nearest_occurrence_name": c.get("nearest_occurrence_name"),
            # Newlines inside a quoted CSV field are legal but break `grep` and
            # a lot of spreadsheet imports; evidence is joined onto one line.
            "evidence": " | ".join(c.get("evidence") or []),
            "data_sources_used": " | ".join(c.get("data_sources_used") or []),
        }
        centre = centroid_of(c.get("geometry"))
        if centre is None and c.get("cell_id"):
            try:
                from app.scoring.grid import cell_id_to_geojson

                centre = centroid_of(cell_id_to_geojson(c["cell_id"]))
            except Exception:
                centre = None
        if centre is not None:
            row.update(coordinate_columns(centre[0], centre[1]))
        rows.append(row)
    return rows_to_csv(rows, SCORED_CELL_COLUMNS)
