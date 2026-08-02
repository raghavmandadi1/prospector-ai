"""
Acceptance tests for the fixed, globally-anchored analysis grid.

These encode the guarantees the cache and the benchmark depend on. If any of
them fails, cached scores are being served for the wrong ground and benchmark
deltas are noise — so they are worth more than their line count suggests.

Run:  .venv/bin/python -m pytest backend/tests/test_grid.py -q
"""
import math

import pytest
from shapely.geometry import shape

from app.scoring.grid import (
    AOIOutOfRangeError,
    GRID_ORIGIN_X,
    GRID_ORIGIN_Y,
    RESOLUTION_LADDER,
    cell_id_to_bbox,
    cell_id_for_point,
    coarsen,
    generate_grid,
    make_cell_id,
    parent_cell_id,
    parse_cell_id,
    snap_to_ladder,
)

# Monte Cristo, Snohomish County — inside the western Cascades.
MONTE_CRISTO = (-121.44, 48.03)
# Republic, Ferry County — NE Washington, east of the UTM 10N/11N boundary at
# 120°W. The single most-cited district in knowledge/historical/gold.md.
REPUBLIC = (-118.74, 48.65)


def square_aoi(lon, lat, half_deg=0.05):
    """A small square AOI centred on a point, as a GeoJSON Feature."""
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - half_deg, lat - half_deg],
                    [lon + half_deg, lat - half_deg],
                    [lon + half_deg, lat + half_deg],
                    [lon - half_deg, lat + half_deg],
                    [lon - half_deg, lat - half_deg],
                ]
            ],
        },
    }


# --- §2.2 the ladder ------------------------------------------------------


def test_ladder_doubles_at_every_step():
    for lo, hi in zip(RESOLUTION_LADDER, RESOLUTION_LADDER[1:]):
        assert hi == lo * 2


def test_grid_origin_aligns_to_coarsest_step():
    """Every level must share the origin corner or the quadtree does not nest."""
    coarsest = RESOLUTION_LADDER[-1]
    assert GRID_ORIGIN_X % coarsest == 0
    assert GRID_ORIGIN_Y % coarsest == 0


def test_snap_to_ladder_picks_nearest_step():
    assert snap_to_ladder(1000) == 1000
    assert snap_to_ladder(800) == 1000
    assert snap_to_ladder(100) == 125
    assert snap_to_ladder(999_999) == 8000


def test_coarsen_walks_up_and_saturates():
    assert coarsen(125) == 250
    assert coarsen(1000) == 2000
    assert coarsen(8000) == 8000  # top of the ladder, no infinite loop


# --- §2.4 acceptance criteria ---------------------------------------------


def test_overlapping_aois_share_identical_cells():
    """Two different polygons over the same ground → identical ids AND geometry.

    This is the criterion the whole caching design rests on.
    """
    a = generate_grid(square_aoi(*MONTE_CRISTO, half_deg=0.05), 1000)
    b = generate_grid(square_aoi(-121.42, 48.05, half_deg=0.05), 1000)

    shared = {c.cell_id for c in a} & {c.cell_id for c in b}
    assert len(shared) > 20, "expected substantial overlap between the two AOIs"

    a_by_id = {c.cell_id: c for c in a}
    b_by_id = {c.cell_id: c for c in b}
    for cid in shared:
        assert a_by_id[cid].bbox == pytest.approx(b_by_id[cid].bbox, abs=1e-9)
        assert shape(a_by_id[cid].geometry).equals(shape(b_by_id[cid].geometry))


def test_cell_id_round_trips_to_a_bbox_with_no_other_state():
    cell_id = cell_id_for_point(*MONTE_CRISTO, 1000)
    min_lon, min_lat, max_lon, max_lat = cell_id_to_bbox(cell_id)
    assert min_lon < MONTE_CRISTO[0] < max_lon
    assert min_lat < MONTE_CRISTO[1] < max_lat


def test_cell_id_is_stable_across_processes():
    """Ids must be a pure function of coordinates — no process-local state."""
    import subprocess
    import sys

    code = (
        "from app.scoring.grid import cell_id_for_point;"
        f"print(cell_id_for_point({MONTE_CRISTO[0]}, {MONTE_CRISTO[1]}, 1000))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == cell_id_for_point(*MONTE_CRISTO, 1000)


def test_finer_cells_are_children_of_the_coarser_cell():
    """Requesting 500 m after a 1000 m run yields children of the 1000 m cells."""
    coarse = {c.cell_id for c in generate_grid(square_aoi(*MONTE_CRISTO), 1000)}
    fine = generate_grid(square_aoi(*MONTE_CRISTO), 500)

    # Interior fine cells (not AOI-edge slivers) must map into the coarse set.
    mapped = sum(1 for c in fine if parent_cell_id(c.cell_id, 1000) in coarse)
    assert mapped == len(fine), "every fine cell should sit inside an analysis cell"


def test_each_coarse_cell_contains_exactly_four_children():
    parent = cell_id_for_point(*MONTE_CRISTO, 1000)
    res, col, row = parse_cell_id(parent)
    children = [
        make_cell_id(col * 2 + dc, row * 2 + dr, res // 2)
        for dc in (0, 1)
        for dr in (0, 1)
    ]
    assert len({parent_cell_id(c, res) for c in children}) == 1
    assert parent_cell_id(children[0], res) == parent


def test_parent_of_a_cell_two_levels_up():
    cid = cell_id_for_point(*MONTE_CRISTO, 250)
    assert parent_cell_id(cid, 1000) == cell_id_for_point(*MONTE_CRISTO, 1000)


def test_parent_cell_id_rejects_off_ladder_and_inverted_requests():
    cid = cell_id_for_point(*MONTE_CRISTO, 500)
    with pytest.raises(ValueError):
        parent_cell_id(cid, 250)  # finer than the cell
    with pytest.raises(ValueError):
        parent_cell_id(cid, 750)  # not a multiple


# --- §2.3 canonical vs display geometry -----------------------------------


def test_canonical_geometry_is_unclipped_and_display_is_clipped():
    aoi = square_aoi(*MONTE_CRISTO, half_deg=0.03)
    aoi_shape = shape(aoi["geometry"])
    cells = generate_grid(aoi, 1000)

    edge = [c for c in cells if not shape(c.geometry).within(aoi_shape)]
    assert edge, "a square AOI must have cells straddling its boundary"

    # Clipping happens in EPSG:5070 and the result is reprojected, so clipped
    # edges bow very slightly against a straight WGS84 line. Test the invariant
    # (display is the square ∩ the AOI) by containment with a curvature-sized
    # buffer rather than by exact area, which that bowing makes meaningless on
    # small corner slivers.
    tol = 1e-5  # degrees, ~1 m — the measured bow over a 1 km cell edge is ~0.5 m
    for c in edge:
        canonical = shape(c.geometry)
        display = shape(c.display_geometry)
        assert display.area < canonical.area
        assert display.within(canonical.buffer(tol))
        assert display.within(aoi_shape.buffer(tol))
        # And the bow really is curvature-scale, not a mis-clip
        assert display.difference(aoi_shape).area < 0.01 * canonical.area


def test_canonical_cells_are_equal_area_across_the_state():
    """Equal-area CRS: a 1000 m cell is ~1 km² in the west and the northeast.

    A per-AOI UTM zone (what this replaced) or a single hardcoded zone would
    both fail this at one end of the state or the other.
    """
    import pyproj
    from shapely.ops import transform

    geod = pyproj.Geod(ellps="WGS84")
    for lon, lat in (MONTE_CRISTO, REPUBLIC):
        cells = generate_grid(square_aoi(lon, lat, half_deg=0.02), 1000)
        areas = [abs(geod.geometry_area_perimeter(shape(c.geometry))[0]) for c in cells]
        for a in areas:
            assert a == pytest.approx(1_000_000, rel=0.02)


# --- statewide coverage ---------------------------------------------------


def test_northeast_washington_is_supported():
    """Republic is at 118.7°W — outside UTM zone 10N.

    knowledge/historical/gold.md cites Republic more than any other district,
    so a grid that cannot represent it is not fit for this project.
    """
    cells = generate_grid(square_aoi(*REPUBLIC), 1000)
    assert len(cells) > 50
    ids = {c.cell_id for c in cells}
    assert cell_id_for_point(*REPUBLIC, 1000) in ids


def test_west_and_northeast_cells_never_collide():
    west = generate_grid(square_aoi(*MONTE_CRISTO), 1000)
    east = generate_grid(square_aoi(*REPUBLIC), 1000)
    assert not ({c.cell_id for c in west} & {c.cell_id for c in east})


def test_aoi_outside_washington_fails_loudly():
    with pytest.raises(AOIOutOfRangeError):
        generate_grid(square_aoi(-106.8, 39.2), 1000)  # Colorado


# --- cell counts ----------------------------------------------------------


def test_cell_count_scales_with_resolution():
    aoi = square_aoi(*MONTE_CRISTO, half_deg=0.05)
    counts = {r: len(generate_grid(aoi, r)) for r in (500, 1000, 2000)}
    assert counts[500] > counts[1000] > counts[2000]
    # ~4x per ladder step, allowing for boundary effects
    assert 3.0 < counts[500] / counts[1000] < 5.0
