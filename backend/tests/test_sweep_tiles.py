"""Tile generation for regional sweeps — the acceptance criteria from §43.

The load-bearing test here is `test_tiles_do_not_inflate_like_generate_grid`.
"Steps for Raghav 3.0" §37/§38 size tiles at 10x10 = 100 cells and 12x12 = 144
with a halo, "still under MAX_LLM_CELLS = 150". That is true of the grid and
false of `generate_grid()`, which returns 110 and 156 for the same nominal
blocks — and 156 trips the coarsening loop in `orchestrator.run_analysis`,
silently halving the resolution of an entire sweep.

That test pins both halves: what the index arithmetic gives, and what the
polygon path gives. If someone "simplifies" tiles.py to call generate_grid, it
fails with the numbers in the assertion message.
"""
import json

import pytest
from shapely.geometry import box, mapping

from app.scoring.grid import (
    AOIOutOfRangeError,
    cell_polygon_wgs84,
    generate_grid,
    parse_cell_id,
)
from app.sweeps.tiles import (
    TILE_BLOCK,
    Tile,
    refine_tiles,
    region_cell_ids,
    tile_at,
    tile_id_for,
    tiles_for_region,
)

# Anchor used throughout: the NF Snoqualmie / Buena Vista corridor.
CORRIDOR = mapping(box(-121.68, 47.57, -121.40, 47.76))


# --- the §37/§38 arithmetic ------------------------------------------------


def test_full_tile_is_exactly_block_squared():
    t = tile_at(100, 100, 1000)
    assert t.cell_count == TILE_BLOCK**2 == 100
    assert len(t.core_cell_ids) == len(set(t.core_cell_ids)), "no duplicate core cells"


def test_one_cell_halo_is_exactly_4n_plus_4():
    t = tile_at(100, 100, 1000)
    assert len(t.halo_cell_ids) == 4 * TILE_BLOCK + 4 == 44
    # 100 + 44 = 144, which is the whole reason §38 chose a 10x10 block.
    assert t.prompt_cell_count == 144
    assert t.prompt_cell_count <= 150, "must stay under MAX_LLM_CELLS"


def test_two_cell_halo_grows_as_specced():
    t = tile_at(100, 100, 1000, halo=2)
    # (10+4)^2 - 10^2 = 96
    assert len(t.halo_cell_ids) == 96
    assert t.prompt_cell_count == 196


def test_halo_and_core_are_disjoint():
    t = tile_at(100, 100, 1000)
    assert not (set(t.core_cell_ids) & set(t.halo_cell_ids))


def test_tiles_do_not_inflate_like_generate_grid():
    """The measurement that kills §37's method.

    Build a tile polygon exactly on EPSG:5070 cell boundaries and hand it to
    generate_grid: it comes back with more cells than the block contains,
    because it admits any square that merely touches the AOI. Index arithmetic
    does not.
    """
    from app.scoring import grid as g

    res = 1000
    col, row = g.cell_indices_for_point(-121.55, 47.65, res)
    x0 = g.GRID_ORIGIN_X + col * res
    y0 = g.GRID_ORIGIN_Y + row * res

    import pyproj
    from shapely.ops import transform

    back = pyproj.Transformer.from_crs(
        "EPSG:5070", "EPSG:4326", always_xy=True
    ).transform

    for n in (10, 12):
        poly = transform(back, box(x0, y0, x0 + n * res, y0 + n * res))
        via_polygon = len(generate_grid(json.loads(json.dumps(mapping(poly))), res))
        assert via_polygon > n * n, (
            f"generate_grid on a {n}x{n} aligned block returned {via_polygon}, "
            f"expected inflation over {n * n} — if this now equals {n * n}, "
            "generate_grid was fixed and tiles.py's warning can be relaxed"
        )

    # 12x12 via polygon exceeds the cap; via index arithmetic it does not.
    poly12 = transform(back, box(x0, y0, x0 + 12 * res, y0 + 12 * res))
    assert len(generate_grid(json.loads(json.dumps(mapping(poly12))), res)) > 150
    assert tile_at(col // TILE_BLOCK, row // TILE_BLOCK, res).prompt_cell_count <= 150


# --- tile identity ---------------------------------------------------------


def test_tile_id_is_not_a_valid_cell_id():
    """A tile id must never be mistaken for a cell id.

    grid._CELL_ID_RE hardcodes six-digit col/row; the tile id uses a B/T shape
    with four digits, so parse_cell_id rejects it loudly rather than naming the
    wrong square of earth.
    """
    tid = tile_id_for(12, 34, 1000, TILE_BLOCK)
    assert tid == "wa5070-1000m-B10-T0012-0034"
    with pytest.raises(ValueError):
        parse_cell_id(tid)


def test_tile_id_is_stable_and_encodes_block():
    assert tile_id_for(1, 2, 1000, 10) != tile_id_for(1, 2, 1000, 20)
    assert tile_id_for(1, 2, 1000, 10) == tile_id_for(1, 2, 1000, 10)
    # Same ground at a different resolution is a different tile.
    assert tile_id_for(1, 2, 1000, 10) != tile_id_for(1, 2, 2000, 10)


def test_core_cell_ids_parse_as_cell_ids_at_the_tile_resolution():
    t = tile_at(100, 100, 1000)
    for cid in t.core_cell_ids:
        res, _, _ = parse_cell_id(cid)
        assert res == 1000


# --- region tiling ---------------------------------------------------------


def test_tiles_partition_the_region_with_no_overlap():
    tiles = tiles_for_region(CORRIDOR, 1000)
    assert tiles
    # region_cell_ids raises on any duplicate — a cell scored by two tiles would
    # be double-counted in region-wide normalization.
    ids = region_cell_ids(tiles)
    assert len(ids) == len(set(ids))
    assert len(ids) == sum(t.cell_count for t in tiles)


def test_adjacent_tiles_share_no_core_cells():
    tiles = tiles_for_region(CORRIDOR, 1000)
    for i, a in enumerate(tiles):
        for b in tiles[i + 1 :]:
            assert not (set(a.core_cell_ids) & set(b.core_cell_ids))


def test_every_region_cell_is_core_in_exactly_one_tile():
    from shapely.geometry import shape

    region = shape(CORRIDOR)
    tiles = tiles_for_region(CORRIDOR, 1000)
    core = set(region_cell_ids(tiles))
    # Every core cell really does touch the region...
    for cid in core:
        res, c, r = parse_cell_id(cid)
        assert cell_polygon_wgs84(c, r, res).intersects(region)
    # ...and nothing touching the region was left out. Checked against the
    # independent path: generate_grid's cell set must be a subset of the cores.
    from_grid = {c.cell_id for c in generate_grid(CORRIDOR, 1000)}
    assert from_grid <= core, f"tiling missed {len(from_grid - core)} cells the grid found"


def test_no_tile_exceeds_the_llm_cap():
    for res in (1000, 2000):
        for t in tiles_for_region(CORRIDOR, res):
            assert t.prompt_cell_count <= 150, f"{t.tile_id} would trigger coarsening"


def test_tiling_is_deterministic():
    a = tiles_for_region(CORRIDOR, 1000)
    b = tiles_for_region(CORRIDOR, 1000)
    assert [t.tile_id for t in a] == [t.tile_id for t in b]
    assert [t.core_cell_ids for t in a] == [t.core_cell_ids for t in b]


def test_clipped_edge_tiles_still_get_a_full_halo():
    """A tile clipped by the region edge keeps context on every retained cell.

    The halo is computed from the resulting core, not from the unclipped block,
    so a cell on a ragged edge is not silently reasoned about with less context
    than its neighbours.
    """
    tiles = tiles_for_region(CORRIDOR, 1000)
    partial = [t for t in tiles if t.cell_count < TILE_BLOCK**2]
    assert partial, "the corridor should produce at least one clipped tile"
    for t in partial:
        core = set(t.core_cell_ids)
        halo = set(t.halo_cell_ids)
        for cid in t.core_cell_ids:
            res, c, r = parse_cell_id(cid)
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    from app.scoring.grid import make_cell_id

                    nb = make_cell_id(c + dc, r + dr, res)
                    assert nb in core or nb in halo, f"{cid} missing neighbour {nb}"


def test_region_outside_washington_raises():
    with pytest.raises(AOIOutOfRangeError):
        tiles_for_region(mapping(box(-100.0, 40.0, -99.0, 41.0)), 1000)


# --- coarse -> fine refinement (§41.1) --------------------------------------


def test_refine_expands_coarse_cells_to_their_children():
    coarse = tile_at(50, 50, 2000)
    fine = refine_tiles(coarse.core_cell_ids[:4], 1000)
    fine_ids = set(region_cell_ids(fine))
    # Each 2000 m cell is exactly four 1000 m cells.
    assert len(fine_ids) == 4 * 4
    # And every child really is contained by its parent.
    from app.scoring.grid import parent_cell_id

    parents = {parent_cell_id(cid, 2000) for cid in fine_ids}
    assert parents == set(coarse.core_cell_ids[:4])


def test_refine_refuses_to_go_up_the_ladder():
    t = tile_at(50, 50, 1000)
    with pytest.raises(ValueError):
        refine_tiles(t.core_cell_ids[:1], 2000)
