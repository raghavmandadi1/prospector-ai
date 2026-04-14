"""
Grid generation for spatial analysis.

Divides an AOI polygon into a regular grid of square cells at a given
ground resolution. Grid cells are used as the unit of analysis for all
specialist agents.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from shapely.geometry import shape, box, mapping
from shapely.ops import transform
import pyproj


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

    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            cell_utm = box(x, y, x + resolution_m, y + resolution_m)
            if cell_utm.intersects(aoi_utm):
                # Clip cell to AOI boundary
                clipped = cell_utm.intersection(aoi_utm)
                if clipped.is_empty:
                    y += resolution_m
                    continue
                # Project back to WGS84
                cell_wgs84 = transform(project_to_wgs84, clipped)
                b = cell_wgs84.bounds  # (min_lon, min_lat, max_lon, max_lat)
                cells.append(
                    GridCell(
                        cell_id=str(uuid.uuid4()),
                        geometry=mapping(cell_wgs84),
                        bbox=b,
                    )
                )
            y += resolution_m
        x += resolution_m

    return cells
