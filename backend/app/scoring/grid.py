"""
Grid generation for spatial analysis.

Divides an AOI polygon into cells of a **fixed, globally-anchored grid**, so a
given ``cell_id`` always names the same square of ground regardless of which
polygon the user happened to draw. That stability is what makes cell-level
caching, run records, and benchmarking possible at all — see
``docs/07_stable_cell_ids.md``.

Two things follow from the anchoring:

* **One projection for all of Washington.** Cell indices are computed in
  EPSG:5070 (NAD83 / Conus Albers), an equal-area projection covering the whole
  state. A per-AOI UTM zone — what this module used to do — makes indices
  incomparable between runs, and hardcoding a single UTM zone would exclude
  either NE or western Washington (zone 10N ends at 120°W, and Republic,
  Colville, Metaline and Toroda Creek all sit east of it).
* **A nesting resolution ladder.** Every step is exactly 2× the one below and
  shares the same origin, so each cell at level *n* is exactly four cells at
  level *n−1*. ``parent_cell_id`` is then exact containment rather than a
  nearest-neighbour guess, and coarse cached scores stay reusable at finer
  display resolutions.

Cells carry two geometries: ``geometry`` is the full unclipped square (what the
LLM reasons about and what gets cached), ``display_geometry`` is that square
intersected with the AOI (what the map draws).
"""
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shapely.geometry import shape, box, mapping
from shapely.ops import transform, unary_union
import pyproj

from app.models.agent_result import ScoredCell

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed grid definition. None of these may change without invalidating every
# cached score and every stored run record — the cell_id prefix carries the
# grid version so a change is at least detectable rather than silent.
# ---------------------------------------------------------------------------

#: Analysis CRS. NAD83 / Conus Albers — equal-area, covers all of Washington
#: with no zone boundary running through the middle of the state.
EPSG_ANALYSIS = 5070

#: Short tag embedded in every cell_id, identifying the grid this ID belongs to.
GRID_TAG = "wa5070"

#: Grid origin in EPSG:5070 metres, southwest of Washington and divisible by the
#: coarsest ladder step so every level of the quadtree shares this corner.
GRID_ORIGIN_X = -2_240_000
GRID_ORIGIN_Y = 2_656_000

#: Allowed cell sizes, metres. Each is exactly 2× its predecessor.
RESOLUTION_LADDER = [125, 250, 500, 1000, 2000, 4000, 8000]

#: Sanity envelope for an AOI, in WGS84 degrees. Washington plus a margin.
#: An AOI outside this is a bug (or a user pointing the tool at the wrong
#: continent) and fails loudly rather than producing meaningless indices.
WA_BOUNDS = (-125.5, 45.0, -116.0, 49.5)  # (min_lon, min_lat, max_lon, max_lat)

_CELL_ID_RE = re.compile(
    rf"^{GRID_TAG}-(?P<res>\d+)m-(?P<col>\d{{6}})-(?P<row>\d{{6}})$"
)

# Transformers are expensive to construct and are stateless once built.
_TO_GRID = pyproj.Transformer.from_crs(
    "EPSG:4326", f"EPSG:{EPSG_ANALYSIS}", always_xy=True
).transform
_TO_WGS84 = pyproj.Transformer.from_crs(
    f"EPSG:{EPSG_ANALYSIS}", "EPSG:4326", always_xy=True
).transform


class AOIOutOfRangeError(ValueError):
    """Raised when an AOI falls outside the Washington sanity envelope."""


@dataclass
class GridCell:
    """A single cell of the fixed analysis grid, intersecting the AOI."""

    cell_id: str
    #: Full, unclipped square in WGS84. Canonical — cached and sent to the LLM.
    geometry: Dict[str, Any]
    #: The square intersected with the AOI. Rendering only.
    display_geometry: Dict[str, Any]
    #: Bounding box of the *canonical* square, WGS84
    #: (min_lon, min_lat, max_lon, max_lat).
    bbox: Tuple[float, float, float, float]
    resolution_m: int
    col: int
    row: int
    #: Populated during spatial context queries.
    properties: Dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "geometry": self.geometry,
            "display_geometry": self.display_geometry,
            "bbox": list(self.bbox),
            "resolution_m": self.resolution_m,
            "col": self.col,
            "row": self.row,
            "properties": self.properties,
        }


# ---------------------------------------------------------------------------
# Resolution ladder
# ---------------------------------------------------------------------------


def snap_to_ladder(resolution_m: float) -> int:
    """Return the ladder step nearest to ``resolution_m`` (geometric distance).

    Callers may pass anything; the grid only ever builds on ladder steps, so a
    request for 800 m becomes 1000 m rather than silently producing a grid that
    cannot nest with any other run.
    """
    r = max(float(resolution_m), 1.0)
    return min(RESOLUTION_LADDER, key=lambda step: abs(math.log(step / r)))


def coarsen(resolution_m: int) -> int:
    """Next step up the ladder. Returns the input if already at the top."""
    for step in RESOLUTION_LADDER:
        if step > resolution_m:
            return step
    return RESOLUTION_LADDER[-1]


def is_ladder_resolution(resolution_m: float) -> bool:
    return int(resolution_m) in RESOLUTION_LADDER


# ---------------------------------------------------------------------------
# Cell identity — the whole point of this module
# ---------------------------------------------------------------------------


def make_cell_id(col: int, row: int, resolution_m: int) -> str:
    return f"{GRID_TAG}-{int(resolution_m)}m-{col:06d}-{row:06d}"


def parse_cell_id(cell_id: str) -> Tuple[int, int, int]:
    """``cell_id`` → ``(resolution_m, col, row)``. Raises ValueError if malformed."""
    m = _CELL_ID_RE.match(cell_id or "")
    if not m:
        raise ValueError(f"Not a {GRID_TAG} cell id: {cell_id!r}")
    return int(m.group("res")), int(m.group("col")), int(m.group("row"))


def cell_indices_for_point(lon: float, lat: float, resolution_m: int) -> Tuple[int, int]:
    """Grid indices of the cell containing a WGS84 point."""
    x, y = _TO_GRID(lon, lat)
    col = math.floor((x - GRID_ORIGIN_X) / resolution_m)
    row = math.floor((y - GRID_ORIGIN_Y) / resolution_m)
    return col, row


def cell_id_for_point(lon: float, lat: float, resolution_m: int) -> str:
    """Cell id containing a WGS84 point, at a ladder resolution."""
    res = snap_to_ladder(resolution_m)
    col, row = cell_indices_for_point(lon, lat, res)
    return make_cell_id(col, row, res)


def cell_polygon_wgs84(col: int, row: int, resolution_m: int):
    """Shapely polygon of a cell's canonical square, in WGS84."""
    x0 = GRID_ORIGIN_X + col * resolution_m
    y0 = GRID_ORIGIN_Y + row * resolution_m
    return transform(_TO_WGS84, box(x0, y0, x0 + resolution_m, y0 + resolution_m))


def cell_id_to_bbox(cell_id: str) -> Tuple[float, float, float, float]:
    """Recover a cell's WGS84 bbox from its id alone — no other state needed.

    This is what lets run records and benchmark reports store bare cell ids and
    still be locatable on a map.
    """
    res, col, row = parse_cell_id(cell_id)
    return cell_polygon_wgs84(col, row, res).bounds


def cell_id_to_geojson(cell_id: str) -> Dict[str, Any]:
    res, col, row = parse_cell_id(cell_id)
    return mapping(cell_polygon_wgs84(col, row, res))


def cells_from_ids(cell_ids: Iterable[str]) -> List["GridCell"]:
    """GridCells for an explicit list of ids — no AOI, no clipping.

    The sweep path needs this. A tile's cell set comes from index arithmetic
    (``app.sweeps.tiles``), not from intersecting a polygon, because handing a
    tile-shaped polygon back to ``generate_grid`` inflates the count past
    ``MAX_LLM_CELLS`` and silently coarsens the run. Rebuilding cells from their
    ids keeps the tile exactly the size the tiler computed.

    ``display_geometry`` is the canonical square rather than a clipped one:
    clipping to a tile boundary would draw the tile grid on the map, which is an
    artifact of how the work was divided and not a fact about the ground.
    """
    cells: List[GridCell] = []
    for cid in cell_ids:
        res, col, row = parse_cell_id(cid)
        square = cell_polygon_wgs84(col, row, res)
        geom = mapping(square)
        cells.append(
            GridCell(
                cell_id=cid,
                geometry=geom,
                display_geometry=geom,
                bbox=square.bounds,
                resolution_m=res,
                col=col,
                row=row,
            )
        )
    return cells


def parent_cell_id(cell_id: str, parent_resolution_m: int) -> str:
    """The coarser cell that exactly contains ``cell_id``.

    Exact containment, not a nearest-neighbour lookup: the ladder doubles and
    shares an origin, so indices divide cleanly.
    """
    res, col, row = parse_cell_id(cell_id)
    parent_res = int(parent_resolution_m)
    if parent_res < res:
        raise ValueError(
            f"Parent resolution {parent_res}m is finer than cell resolution {res}m"
        )
    if parent_res % res != 0:
        raise ValueError(
            f"Resolutions {res}m and {parent_res}m are not on the same ladder"
        )
    factor = parent_res // res
    return make_cell_id(col // factor, row // factor, parent_res)


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------


def _aoi_shape(aoi_geojson: Dict[str, Any]):
    if aoi_geojson.get("type") == "FeatureCollection":
        geometries = [f["geometry"] for f in aoi_geojson["features"]]
        return unary_union([shape(g) for g in geometries])
    if aoi_geojson.get("type") == "Feature":
        return shape(aoi_geojson["geometry"])
    return shape(aoi_geojson)


def _assert_in_range(aoi_shape) -> None:
    min_lon, min_lat, max_lon, max_lat = aoi_shape.bounds
    w_lon, w_lat, e_lon, n_lat = WA_BOUNDS
    if min_lon < w_lon or max_lon > e_lon or min_lat < w_lat or max_lat > n_lat:
        raise AOIOutOfRangeError(
            f"AOI bounds ({min_lon:.4f}, {min_lat:.4f}, {max_lon:.4f}, "
            f"{max_lat:.4f}) fall outside the Washington grid envelope "
            f"{WA_BOUNDS}. GeoProspector's knowledge bases, districts and "
            f"reference data are Washington-specific."
        )


def generate_grid(
    aoi_geojson: Dict[str, Any], resolution_m: float = 1000
) -> List[GridCell]:
    """Cells of the fixed grid that intersect the AOI.

    ``resolution_m`` is snapped to the nearest ladder step. Cells are returned
    with the canonical unclipped square as ``geometry`` and the AOI-clipped
    version as ``display_geometry``; a cell is included when its square
    intersects the AOI at all.
    """
    aoi_shape = _aoi_shape(aoi_geojson)
    _assert_in_range(aoi_shape)

    res = snap_to_ladder(resolution_m)
    aoi_grid = transform(_TO_GRID, aoi_shape)
    minx, miny, maxx, maxy = aoi_grid.bounds

    col_start = math.floor((minx - GRID_ORIGIN_X) / res)
    col_end = math.floor((maxx - GRID_ORIGIN_X) / res)
    row_start = math.floor((miny - GRID_ORIGIN_Y) / res)
    row_end = math.floor((maxy - GRID_ORIGIN_Y) / res)

    cells: List[GridCell] = []
    for col in range(col_start, col_end + 1):
        x0 = GRID_ORIGIN_X + col * res
        for row in range(row_start, row_end + 1):
            y0 = GRID_ORIGIN_Y + row * res
            square = box(x0, y0, x0 + res, y0 + res)
            if not square.intersects(aoi_grid):
                continue
            clipped = square.intersection(aoi_grid)
            if clipped.is_empty:
                continue
            square_wgs84 = transform(_TO_WGS84, square)
            clipped_wgs84 = transform(_TO_WGS84, clipped)
            cells.append(
                GridCell(
                    cell_id=make_cell_id(col, row, res),
                    geometry=mapping(square_wgs84),
                    display_geometry=mapping(clipped_wgs84),
                    bbox=square_wgs84.bounds,
                    resolution_m=res,
                    col=col,
                    row=row,
                )
            )

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

    Scores and confidences are IDW-interpolated over the k nearest coarse cell
    centres, which smooths the blockiness of the analysis grid. ``parent_cell_id``
    is *not* interpolated: since the ladder nests, the containing coarse cell is
    computed exactly from the cell id, so the evidence drawer always shows the
    analysis cell the fine cell actually sits inside — even where that differs
    from the nearest centre (which it does along every coarse-cell edge).
    """
    if not coarse_cells:
        return []

    fine_res = snap_to_ladder(fine_resolution_m)
    coarse_res = snap_to_ladder(coarse_resolution_m)
    fine_grid = generate_grid(aoi_geojson, fine_res)

    by_id = {c.cell_id: c for c in coarse_cells}

    # Coarse cell centres in grid metres — a projected CRS, so plain Euclidean
    # distance is correct here without the latitude fudge the old code needed.
    centers = []
    for cc in coarse_cells:
        try:
            res, col, row = parse_cell_id(cc.cell_id)
        except ValueError:
            g = shape(cc.geometry).centroid
            centers.append((*_TO_GRID(g.x, g.y), cc))
            continue
        centers.append(
            (
                GRID_ORIGIN_X + (col + 0.5) * res,
                GRID_ORIGIN_Y + (row + 0.5) * res,
                cc,
            )
        )

    fine_scored: List[ScoredCell] = []
    for cell in fine_grid:
        cx = GRID_ORIGIN_X + (cell.col + 0.5) * fine_res
        cy = GRID_ORIGIN_Y + (cell.row + 0.5) * fine_res

        # Sort on distance only. On a regularly anchored grid a fine cell centre
        # is frequently *exactly* equidistant from two coarse centres, and a
        # bare sorted() would then fall through to comparing ScoredCells.
        dists = sorted(
            (((cx - px) ** 2 + (cy - py) ** 2, cc) for px, py, cc in centers),
            key=lambda t: t[0],
        )
        nearest = dists[: max(1, k_neighbors)]

        if nearest[0][0] < 1e-9:
            score, confidence = nearest[0][1].score, nearest[0][1].confidence
        else:
            wsum = score = confidence = 0.0
            for d2, cc in nearest:
                w = 1.0 / (d2 ** (idw_power / 2.0))
                wsum += w
                score += w * cc.score
                confidence += w * cc.confidence
            score /= wsum
            confidence /= wsum

        # Exact quadtree containment; fall back to nearest centre only when the
        # containing cell was not part of the analysis set (AOI edge slivers).
        try:
            parent = by_id.get(parent_cell_id(cell.cell_id, coarse_res))
        except ValueError:
            parent = None
        if parent is None:
            parent = nearest[0][1]

        fine_scored.append(
            ScoredCell(
                cell_id=cell.cell_id,
                geometry=cell.display_geometry,
                score=round(min(max(score, 0.0), 1.0), 4),
                confidence=round(min(max(confidence, 0.0), 1.0), 4),
                evidence=(
                    [
                        f"Interpolated from {coarse_res}m analysis "
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
