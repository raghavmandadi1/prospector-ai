"""
Grid generation for spatial analysis.

Divides an AOI polygon into a regular grid of square cells at a given
ground resolution. Grid cells are used as the unit of analysis for all
specialist agents.
"""
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from shapely.geometry import shape, box, mapping
from shapely.ops import transform
import pyproj

from app.models.agent_result import ScoredCell


@dataclass
class GridCell:
    """A single grid cell within the AOI."""
    cell_id: str
    geometry: Dict[str, Any]  # GeoJSON geometry
    # Bounding box of the cell in WGS84
    bbox: tuple  # (min_lon, min_lat, max_lon, max_lat)
    # Additional properties added during spatial context queries
    properties: Dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "geometry": self.geometry,
            "bbox": list(self.bbox),
            "properties": self.properties,
        }


def generate_grid(aoi_geojson: Dict[str, Any], resolution_m: float = 1000) -> List[GridCell]:
    """
    Divide an AOI polygon into a regular grid of cells at the given resolution.

    Args:
        aoi_geojson: GeoJSON Feature or FeatureCollection containing the AOI polygon.
                     Must be in WGS84 (EPSG:4326).
        resolution_m: Target cell size in meters. Approximately converted to
                      degrees at the centroid latitude.

    Returns:
        List of GridCell objects covering the AOI. Cells are clipped to the
        AOI boundary — only cells intersecting the polygon are returned.
    """
    # Extract geometry from Feature or FeatureCollection
    if aoi_geojson.get("type") == "FeatureCollection":
        geometries = [f["geometry"] for f in aoi_geojson["features"]]
        from shapely.ops import unary_union
        aoi_shape = unary_union([shape(g) for g in geometries])
    elif aoi_geojson.get("type") == "Feature":
        aoi_shape = shape(aoi_geojson["geometry"])
    else:
        # Raw geometry object
        aoi_shape = shape(aoi_geojson)

    # Project to a metric CRS for accurate cell sizing
    # Use UTM zone based on AOI centroid
    centroid = aoi_shape.centroid
    utm_zone = int((centroid.x + 180) / 6) + 1
    hemisphere = "north" if centroid.y >= 0 else "south"
    utm_crs = pyproj.CRS(f"+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84")
    wgs84 = pyproj.CRS("EPSG:4326")

    project_to_utm = pyproj.Transformer.from_crs(wgs84, utm_crs, always_xy=True).transform
    project_to_wgs84 = pyproj.Transformer.from_crs(utm_crs, wgs84, always_xy=True).transform

    aoi_utm = transform(project_to_utm, aoi_shape)
    bounds = aoi_utm.bounds  # (minx, miny, maxx, maxy) in meters

    minx, miny, maxx, maxy = bounds
    cells = []

    col = 0
    x = minx
    while x < maxx:
        row = 0
        y = miny
        while y < maxy:
            cell_utm = box(x, y, x + resolution_m, y + resolution_m)
            if cell_utm.intersects(aoi_utm):
                # Clip cell to AOI boundary
                clipped = cell_utm.intersection(aoi_utm)
                if clipped.is_empty:
                    y += resolution_m
                    row += 1
                    continue
                # Project back to WGS84
                cell_wgs84 = transform(project_to_wgs84, clipped)
                b = cell_wgs84.bounds  # (min_lon, min_lat, max_lon, max_lat)
                cells.append(
                    GridCell(
                        cell_id=f"c{col}_r{row}",
                        geometry=mapping(cell_wgs84),
                        bbox=b,
                    )
                )
            y += resolution_m
            row += 1
        x += resolution_m
        col += 1

    return cells


def interpolate_to_fine_grid(
    coarse_cells: List[ScoredCell],
    aoi_geojson: Dict[str, Any],
    fine_resolution_m: float,
    coarse_resolution_m: float,
    idw_power: float = 2.0,
    k_neighbors: int = 4,
) -> List[ScoredCell]:
    """
    Downscale coarse LLM-scored cells to a finer display grid.

    The LLM scores a coarse analysis grid (bounded cell count); this projects
    those scores onto the requested fine grid (e.g. 100 m) using inverse-
    distance weighting over the k nearest coarse cell centers. Evidence and
    data sources are inherited from the nearest coarse cell, flagged as
    interpolated so the provenance stays honest.
    """
    if not coarse_cells:
        return []

    fine_grid = generate_grid(aoi_geojson, fine_resolution_m)

    # Coarse cell centers from geometry bounds (WGS84 degrees are fine for
    # relative distances at AOI scale; latitudes are near-constant)
    centers = []
    for cc in coarse_cells:
        g = shape(cc.geometry)
        c = g.centroid
        centers.append((c.x, c.y, cc))

    # Pre-scale longitude by cos(mean latitude) so degree distances are ~isotropic
    mean_lat = sum(c[1] for c in centers) / len(centers)
    lon_scale = math.cos(math.radians(mean_lat))

    fine_scored: List[ScoredCell] = []
    for cell in fine_grid:
        cx = (cell.bbox[0] + cell.bbox[2]) / 2
        cy = (cell.bbox[1] + cell.bbox[3]) / 2

        dists = []
        for (px, py, cc) in centers:
            dx = (cx - px) * lon_scale
            dy = cy - py
            dists.append((dx * dx + dy * dy, cc))
        dists.sort(key=lambda t: t[0])
        nearest = dists[: max(1, k_neighbors)]

        # Exact / near-exact hit: adopt the coarse cell values directly
        if nearest[0][0] < 1e-16:
            parent = nearest[0][1]
            score, confidence = parent.score, parent.confidence
        else:
            wsum = 0.0
            score = 0.0
            confidence = 0.0
            for d2, cc in nearest:
                w = 1.0 / (d2 ** (idw_power / 2.0))
                wsum += w
                score += w * cc.score
                confidence += w * cc.confidence
            score /= wsum
            confidence /= wsum
            parent = nearest[0][1]

        fine_scored.append(
            ScoredCell(
                cell_id=cell.cell_id,
                geometry=cell.geometry,
                score=round(min(max(score, 0.0), 1.0), 4),
                confidence=round(min(max(confidence, 0.0), 1.0), 4),
                evidence=(
                    [
                        f"Interpolated from {coarse_resolution_m:.0f}m analysis "
                        f"cell {parent.cell_id}"
                    ]
                    # Keep payload small — full evidence is available via the
                    # parent cell in the per-agent breakdown
                    + parent.evidence[:6]
                ),
                data_sources_used=parent.data_sources_used,
                parent_cell_id=parent.cell_id,
            )
        )

    return fine_scored
