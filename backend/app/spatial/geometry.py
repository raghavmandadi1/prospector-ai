"""
Distance and area helpers for AOI-scale spatial work.

One decision runs through this module: **distances are computed in a local
equirectangular metre frame, not in degrees and not in EPSG:5070.**

Degrees are wrong in a way that silently biases results — a degree of longitude
is 78 km at Washington's latitude and a degree of latitude is 111 km, so any
comparison of "distance in degrees" ranks north–south neighbours as ~1.4×
further away than east–west ones at the same true distance. That is the kind of
error that produces a plausible-looking map and no error message.

EPSG:5070 would be defensible, and the analysis grid is defined in it. But
Conus Albers is equal-*area*, which means it is not conformal: it distorts local
distance by up to a percent or so across the state, and the distortion varies
with position, so two AOIs are not directly comparable. A local frame pinned to
the AOI's own centre is accurate to well under 0.1% over the tens of kilometres
an AOI spans, and it is cheap.

`app.toponyms.matcher._km_between` already does the same thing for point pairs
with the same constants. This module generalises it to shapely geometries.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

import shapely
from shapely.geometry import shape

#: Metres per degree of latitude, WGS84 mean. Varies by ~0.5% pole to equator;
#: at Washington's latitudes 110_574 is within a few tens of metres per degree.
M_PER_DEG_LAT = 110_574.0
#: Metres per degree of longitude at the equator. Scaled by cos(lat) in use.
M_PER_DEG_LON_EQUATOR = 111_320.0


@dataclass(frozen=True)
class LocalMetric:
    """A local equirectangular projection pinned to one reference point.

    ``x`` grows east and ``y`` grows north, both in metres from the reference
    point. Build one per AOI with :meth:`for_bbox` and use it for every distance
    in that AOI so all of them share the same frame.
    """

    lon0: float
    lat0: float
    #: Metres per degree of longitude at ``lat0``.
    mx: float
    #: Metres per degree of latitude.
    my: float = M_PER_DEG_LAT

    @classmethod
    def for_bbox(cls, bbox: Sequence[float]) -> "LocalMetric":
        """Frame centred on a (min_lon, min_lat, max_lon, max_lat) box."""
        lon0 = (bbox[0] + bbox[2]) / 2.0
        lat0 = (bbox[1] + bbox[3]) / 2.0
        return cls.for_point(lon0, lat0)

    @classmethod
    def for_point(cls, lon: float, lat: float) -> "LocalMetric":
        return cls(
            lon0=lon,
            lat0=lat,
            mx=M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat)),
        )

    def xy(self, lon: float, lat: float) -> Tuple[float, float]:
        return ((lon - self.lon0) * self.mx, (lat - self.lat0) * self.my)

    def project(self, geom):
        """Project a shapely geometry into the metre frame.

        Uses ``shapely.transform``, which operates on the whole coordinate array
        at once — a per-vertex Python callback over a few thousand fault traces
        is the difference between milliseconds and seconds.
        """
        if geom is None or geom.is_empty:
            return geom
        return shapely.transform(
            geom,
            lambda coords: shapely.creation.empty(0)
            if len(coords) == 0
            else _shift_scale(coords, self.lon0, self.lat0, self.mx, self.my),
        )

    def km(self, geom_a, geom_b) -> float:
        """Distance between two already-projected geometries, kilometres."""
        return geom_a.distance(geom_b) / 1000.0


def _shift_scale(coords, lon0: float, lat0: float, mx: float, my: float):
    """Vectorised (lon, lat) -> (x, y) for a shapely coordinate array."""
    out = coords.copy()
    out[:, 0] = (coords[:, 0] - lon0) * mx
    out[:, 1] = (coords[:, 1] - lat0) * my
    return out


def km_between(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle-ish distance between two points, kilometres.

    Same equirectangular approximation as ``matcher._km_between`` and
    deliberately identical in behaviour — two modules disagreeing about how far
    apart two points are would be a nasty class of bug to chase.
    """
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    dx = (lon2 - lon1) * math.cos(mean_lat) * (M_PER_DEG_LON_EQUATOR / 1000.0)
    dy = (lat2 - lat1) * (M_PER_DEG_LAT / 1000.0)
    return math.hypot(dx, dy)


def bbox_of(geojson: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """Bounding box of any GeoJSON geometry, Feature, or FeatureCollection."""
    geom = _as_geometry(geojson)
    if geom is None:
        return None
    try:
        return tuple(shape(geom).bounds)  # type: ignore[return-value]
    except Exception:
        return None


def pad_bbox(
    bbox: Sequence[float], km: float
) -> Tuple[float, float, float, float]:
    """Expand a lon/lat box by roughly ``km`` on every side.

    Used to catch records just outside the AOI: a working 800 m beyond the
    polygon edge is still the most relevant fact about the cell next to it, and
    an AOI boundary is an artifact of where the user happened to stop drawing.
    """
    lat_mid = (bbox[1] + bbox[3]) / 2.0
    dlat = km * 1000.0 / M_PER_DEG_LAT
    dlon = km * 1000.0 / max(
        1.0, M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat_mid))
    )
    return (bbox[0] - dlon, bbox[1] - dlat, bbox[2] + dlon, bbox[3] + dlat)


def _as_geometry(geojson: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reduce a GeoJSON object of any wrapping depth to a bare geometry."""
    if not geojson:
        return None
    kind = geojson.get("type")
    if kind == "FeatureCollection":
        feats = geojson.get("features") or []
        if not feats:
            return None
        if len(feats) == 1:
            return _as_geometry(feats[0])
        return {
            "type": "GeometryCollection",
            "geometries": [
                g for g in (_as_geometry(f) for f in feats) if g is not None
            ],
        }
    if kind == "Feature":
        return geojson.get("geometry")
    return geojson


def aoi_shape(aoi_geojson: Dict[str, Any]):
    """Shapely geometry for an AOI given in any GeoJSON wrapping."""
    geom = _as_geometry(aoi_geojson)
    return shape(geom) if geom else None
