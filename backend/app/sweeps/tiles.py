"""Tile generation for regional sweeps.

A tile is a square block of analysis cells on the fixed EPSG:5070 grid
(``scoring/grid.py``), aligned to the same origin. A tile never straddles a cell
boundary, and tile boundaries fall on cell boundaries at every level of the
resolution ladder — so a 1000 m tile is exactly four 500 m tiles and a coarse
sweep can be refined in place without redrawing anything.

WHY THIS MODULE EXISTS AT ALL, RATHER THAN CALLING ``generate_grid()``
---------------------------------------------------------------------
"Steps for Raghav 3.0" §37 sizes tiles at 10 x 10 = 100 cells, and §38 adds a
one-cell halo for 12 x 12 = 144, "still under ``MAX_LLM_CELLS = 150``".

That arithmetic is right about the grid and wrong about ``generate_grid()``.
Measured at the corridor anchor (-121.55, 47.65) at 1000 m, handing
``generate_grid`` a polygon built exactly on EPSG:5070 cell boundaries returns:

    nominal  10x10 = 100  ->  110 cells
    nominal  12x12 = 144  ->  156 cells      <-- over MAX_LLM_CELLS = 150
    nominal  20x20 = 400  ->  451 cells

Two causes compound. ``generate_grid`` admits any cell whose square merely
*touches* the AOI, and it rejects only a clipped geometry that ``is_empty`` — a
degenerate edge-contact sliver is not empty. On top of that the tile polygon
makes a 5070 -> WGS84 -> 5070 round trip, and the curved edges of a projected
square pick up cells beyond the intended span.

The failure is silent and expensive: 156 > 150 trips the coarsening loop in
``orchestrator.run_analysis``, which halves the resolution of the entire sweep
and logs it at INFO. You get a 2000 m map you believe is 1000 m.

So tiles are built by **pure index arithmetic** — ``make_cell_id`` and
``cell_polygon_wgs84`` from the cell indices, never a polygon handed back to the
grid generator. That yields exactly 100 core + 44 halo = 144, as specced.

THE HALO IS CONTEXT, NOT CELLS
------------------------------
§38 describes the halo as extra cells that are scored and then discarded. That
cannot work here: the prompt unit is ``BATCH_SIZE = 50`` (``base_agent.py``), not
the tile, and batches are formed over the *cache-filtered* cell list — so a halo
cell is not guaranteed to share a prompt with the edge cell it exists to
contextualise, and on a warm cache it can be filtered out of the prompt
entirely. §38 also requires halo cells be excluded from the cache write, and
there is no per-cell opt-out in ``_store_in_cache``.

``Tile.halo_cell_ids`` therefore names cells that travel as prompt *context* and
are never batched, never scored and never cached. Exclusion is structural rather
than a filter someone can forget to apply.

TILE COUNT IS DRIVEN BY ALIGNMENT, NOT ONLY BY AREA
---------------------------------------------------
The grid origin is fixed, so a region cannot be nudged to sit tidily on block
boundaries — that is the price of stable cell ids and it is worth paying. But it
means a region straddling block edges produces small partial tiles alongside
full ones. Measured on the proxy corridor at 1000 m: 11 tiles holding 498 cells,
ranging from 2 cells to 100.

That matters for cost, because per-tile overhead does not scale with tile size:
each tile pays one ``build_local_context()`` and at least one batch per agent
regardless of whether it holds 2 cells or 50. The cost preview (§40.3) must
estimate from the actual tile-size distribution rather than from
``area / tile_area``, or it will under-count a ragged region badly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from app.scoring.grid import (
    AOIOutOfRangeError,
    GRID_TAG,
    WA_BOUNDS,
    cell_polygon_wgs84,
    make_cell_id,
    parse_cell_id,
    snap_to_ladder,
)

logger = logging.getLogger(__name__)

#: Cells per tile side. 10 x 10 = 100 core cells, comfortably under
#: MAX_LLM_CELLS = 150 once the 44-cell halo is added as context.
TILE_BLOCK = 10

#: Rings of context cells around the core block. 1 is the spec default; 2 is
#: available but pushes a full tile's prompt to 100 core + 96 context.
TILE_HALO = 1

#: Tile id shape. Deliberately NOT parseable by ``grid._CELL_ID_RE``, which
#: hardcodes six-digit col/row — so passing a tile id where a cell id is wanted
#: raises rather than silently naming the wrong square of earth.
_TILE_ID_FMT = "{tag}-{res}m-B{block:02d}-T{col:04d}-{row:04d}"


@dataclass(frozen=True)
class Tile:
    """One tile of a regional sweep.

    ``core_cell_ids`` are scored and retained. ``halo_cell_ids`` are prompt
    context only — see the module docstring.
    """

    tile_id: str
    resolution_m: int
    block: int
    #: Tile indices (cell indices divided by ``block``), not cell indices.
    tile_col: int
    tile_row: int
    core_cell_ids: Tuple[str, ...]
    halo_cell_ids: Tuple[str, ...]

    @property
    def cell_count(self) -> int:
        return len(self.core_cell_ids)

    @property
    def prompt_cell_count(self) -> int:
        """Cells that appear in a prompt — core plus context."""
        return len(self.core_cell_ids) + len(self.halo_cell_ids)

    def core_polygon(self):
        """Shapely union of the core cell squares, WGS84. For map preview."""
        return unary_union([_polygon_for(cid) for cid in self.core_cell_ids])

    def core_geojson(self) -> Dict[str, Any]:
        return mapping(self.core_polygon())

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return self.core_polygon().bounds

    def model_dump(self) -> Dict[str, Any]:
        return {
            "tile_id": self.tile_id,
            "resolution_m": self.resolution_m,
            "block": self.block,
            "tile_col": self.tile_col,
            "tile_row": self.tile_row,
            "core_cell_ids": list(self.core_cell_ids),
            "halo_cell_ids": list(self.halo_cell_ids),
            "cell_count": self.cell_count,
            "prompt_cell_count": self.prompt_cell_count,
        }


def tile_id_for(tile_col: int, tile_row: int, resolution_m: int, block: int) -> str:
    """Stable id for a tile, from its tile indices.

    ``block`` is part of the id because the same ground tiled 10-wide and
    20-wide are different units of work with different halos, and a sweep
    manifest that conflated them would resume onto the wrong cell set.
    """
    return _TILE_ID_FMT.format(
        tag=GRID_TAG,
        res=int(resolution_m),
        block=int(block),
        col=int(tile_col),
        row=int(tile_row),
    )


def _polygon_for(cell_id: str):
    res, col, row = parse_cell_id(cell_id)
    return cell_polygon_wgs84(col, row, res)


def _grid_box(x0: float, y0: float, res: int):
    """A cell's square in EPSG:5070 metres — no projection, no round trip."""
    return box(x0, y0, x0 + res, y0 + res)


def _assert_region_in_range(region_geojson: Dict[str, Any]) -> None:
    min_lon, min_lat, max_lon, max_lat = shape(region_geojson).bounds
    w, s, e, n = WA_BOUNDS
    if min_lon < w or max_lon > e or min_lat < s or max_lat > n:
        raise AOIOutOfRangeError(
            f"Region bounds ({min_lon:.3f}, {min_lat:.3f}, {max_lon:.3f}, "
            f"{max_lat:.3f}) fall outside the Washington envelope {WA_BOUNDS}"
        )


def tile_at(
    tile_col: int,
    tile_row: int,
    resolution_m: int,
    block: int = TILE_BLOCK,
    halo: int = TILE_HALO,
    core_filter: Optional[Set[Tuple[int, int]]] = None,
) -> Optional[Tile]:
    """Build one tile from its tile indices, by index arithmetic only.

    ``core_filter``, when given, restricts core cells to that set of
    ``(col, row)`` indices — used at a region's ragged edge so a sweep does not
    spend LLM calls on cells outside the polygon. The halo is computed from the
    *resulting* core, so every retained cell keeps full context regardless of
    where the region boundary fell.

    Returns None if the filter leaves no core cells.
    """
    res = snap_to_ladder(resolution_m)
    col0 = tile_col * block
    row0 = tile_row * block

    core_idx: List[Tuple[int, int]] = []
    for dc in range(block):
        for dr in range(block):
            idx = (col0 + dc, row0 + dr)
            if core_filter is None or idx in core_filter:
                core_idx.append(idx)
    if not core_idx:
        return None

    core_set = set(core_idx)
    # Halo = every cell within Chebyshev distance `halo` of a core cell, minus
    # the core itself. For a full block with halo=1 this is exactly 4*block + 4.
    halo_set: Set[Tuple[int, int]] = set()
    for c, r in core_idx:
        for dc in range(-halo, halo + 1):
            for dr in range(-halo, halo + 1):
                idx = (c + dc, r + dr)
                if idx not in core_set:
                    halo_set.add(idx)

    core_sorted = sorted(core_idx)
    halo_sorted = sorted(halo_set)
    return Tile(
        tile_id=tile_id_for(tile_col, tile_row, res, block),
        resolution_m=res,
        block=block,
        tile_col=tile_col,
        tile_row=tile_row,
        core_cell_ids=tuple(make_cell_id(c, r, res) for c, r in core_sorted),
        halo_cell_ids=tuple(make_cell_id(c, r, res) for c, r in halo_sorted),
    )


def tiles_for_region(
    region_geojson: Dict[str, Any],
    resolution_m: int,
    block: int = TILE_BLOCK,
    halo: int = TILE_HALO,
    clip_to_region: bool = True,
) -> List[Tile]:
    """Every tile needed to cover ``region_geojson``, in a stable order.

    Deterministic: the same region and resolution always produce the same tiles
    in the same order, which is what makes a sweep resumable and a re-sweep
    cache-hit rather than recompute.

    With ``clip_to_region`` (the default), core cells are restricted to those
    whose canonical square intersects the region, so edge tiles are smaller
    rather than spending calls on ground nobody asked about. Every cell of the
    region lands in exactly one tile's core.
    """
    res = snap_to_ladder(resolution_m)
    _assert_region_in_range(region_geojson)

    # Cell selection happens ENTIRELY IN EPSG:5070, mirroring generate_grid()
    # exactly, so the two agree cell-for-cell over the same ground.
    #
    # Doing it in WGS84 instead does not work, and fails in a way that looks
    # plausible: a 5070 square transformed to WGS84 is a curved quadrilateral,
    # and a lon/lat-aligned region is a rotated curved shape in 5070, so neither
    # the index span taken from WGS84 bbox corners nor the intersects test
    # survives the round trip. Measured on the corridor at 1000 m, the WGS84
    # version silently dropped 85 cells that generate_grid finds.
    #
    # Note this is a different concern from the inflation documented at the top
    # of this module. That one is about handing generate_grid a *tile*-shaped
    # polygon that has already been round-tripped; this is about selecting cells
    # for the *region*, where generate_grid's own logic is correct.
    from shapely.ops import transform as _shapely_transform

    from app.scoring.grid import GRID_ORIGIN_X, GRID_ORIGIN_Y, _TO_GRID

    region_grid = _shapely_transform(_TO_GRID, shape(region_geojson))
    minx, miny, maxx, maxy = region_grid.bounds
    c_lo = int((minx - GRID_ORIGIN_X) // res)
    c_hi = int((maxx - GRID_ORIGIN_X) // res)
    r_lo = int((miny - GRID_ORIGIN_Y) // res)
    r_hi = int((maxy - GRID_ORIGIN_Y) // res)

    covered: Set[Tuple[int, int]] = set()
    for c in range(c_lo, c_hi + 1):
        x0 = GRID_ORIGIN_X + c * res
        for r in range(r_lo, r_hi + 1):
            y0 = GRID_ORIGIN_Y + r * res
            if _grid_box(x0, y0, res).intersects(region_grid):
                covered.add((c, r))

    if not covered:
        logger.warning("Region intersects no cells at %d m — nothing to sweep", res)
        return []

    tile_idx = sorted({(c // block, r // block) for c, r in covered})
    tiles: List[Tile] = []
    for tc, tr in tile_idx:
        t = tile_at(
            tc,
            tr,
            res,
            block=block,
            halo=halo,
            core_filter=covered if clip_to_region else None,
        )
        if t is not None:
            tiles.append(t)

    logger.info(
        "Region tiled at %d m: %d tiles, %d core cells, %d prompt cells",
        res,
        len(tiles),
        sum(t.cell_count for t in tiles),
        sum(t.prompt_cell_count for t in tiles),
    )
    return tiles


def region_cell_ids(tiles: Sequence[Tile]) -> List[str]:
    """Every core cell id across a tile list, deduplicated and ordered.

    Cores are disjoint by construction, so a duplicate here is a tiling bug —
    it is asserted rather than quietly collapsed, because a cell scored twice
    would be double-counted in the region-wide normalization of §39.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for t in tiles:
        for cid in t.core_cell_ids:
            if cid in seen:
                raise ValueError(
                    f"{cid} is a core cell of more than one tile — tiles must partition "
                    "the region, or region-wide normalization double-counts it"
                )
            seen.add(cid)
            out.append(cid)
    return out


def refine_tiles(
    coarse_cell_ids: Iterable[str],
    fine_resolution_m: int,
    block: int = TILE_BLOCK,
    halo: int = TILE_HALO,
) -> List[Tile]:
    """Tiles covering the children of ``coarse_cell_ids`` at a finer resolution.

    This is §41.1's genuine re-analysis at finer resolution — distinct from
    ``grid.interpolate_to_fine_grid()``, which IDW-downscales coarse scores for
    display and invents no new evidence. Keep the names apart.

    Because the ladder nests exactly, a coarse cell's children are a contiguous
    index block and no geometry test is needed.
    """
    fine = snap_to_ladder(fine_resolution_m)
    covered: Set[Tuple[int, int]] = set()
    for cid in coarse_cell_ids:
        res, col, row = parse_cell_id(cid)
        if fine > res:
            raise ValueError(
                f"refine_tiles: {fine} m is coarser than {cid}'s {res} m — "
                "refinement only goes down the ladder"
            )
        factor = res // fine
        if res % fine:
            raise ValueError(f"{res} m is not an integer multiple of {fine} m")
        for dc in range(factor):
            for dr in range(factor):
                covered.add((col * factor + dc, row * factor + dr))

    tile_idx = sorted({(c // block, r // block) for c, r in covered})
    tiles = []
    for tc, tr in tile_idx:
        t = tile_at(tc, tr, fine, block=block, halo=halo, core_filter=covered)
        if t is not None:
            tiles.append(t)
    return tiles
