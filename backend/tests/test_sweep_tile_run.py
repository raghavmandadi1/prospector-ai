"""A sweep tile through the whole orchestrator, with the LLM stubbed.

Three properties, each of which fails silently if broken:

1. A tile is scored at the resolution it was tiled at. The spec's 12x12 halo
   arithmetic put a tile at 156 cells through `generate_grid`, over
   MAX_LLM_CELLS = 150, which trips the coarsening loop and halves the
   resolution of the entire sweep with only an INFO log. The tile seam exists to
   bypass that path; this asserts it did.
2. Exactly the core cells are scored. `_build_spatial_context` writes everything
   it is handed into `spatial_context["grid_cells"]`, and `BaseAgent.run()` takes
   its work list from that key — so the halo has to be put back before the agents
   run, or every tile scores 144 cells and caches 44 of them wrongly.
3. Coverage counts describe the scored cells, not the looked-up ones.
"""
import pytest

from app.agents import orchestrator as orch
from app.agents.orchestrator import OrchestratorAgent
from app.sweeps.tiles import tile_at, tiles_for_region
from tests.test_orchestrator_integration import stubbed  # noqa: F401  (fixture)
from tests.test_run_record_and_cache import AOI

pytestmark = pytest.mark.asyncio

AGENTS = ["lithology", "structure"]


async def run_tile(tile, config_extra=None):
    events = []

    async def emit(payload):
        events.append(payload)

    config = {
        "resolution_m": 2000,  # deliberately WRONG for the tile — it must be ignored
        "enabled_agents": AGENTS,
        "tile": tile.model_dump(),
    }
    config.update(config_extra or {})
    final, agents = await OrchestratorAgent(api_key="stub").run_analysis(
        job_id=f"job-{tile.tile_id}",
        aoi_geojson=AOI,
        target_mineral="gold",
        config=config,
        emit_fn=emit,
    )
    return final, agents, events


def _corridor_tile(resolution_m=1000):
    """A tile that actually overlaps the shared test AOI, so facts exist."""
    from shapely.geometry import shape

    tiles = tiles_for_region(AOI["geometry"], resolution_m)
    assert tiles
    # The biggest one, so the test exercises a full-ish block.
    return max(tiles, key=lambda t: t.cell_count)


async def test_tile_is_scored_at_its_own_resolution(stubbed):  # noqa: F811
    tile = _corridor_tile(1000)
    final, _, events = await run_tile(tile)

    scored = final["scored_cells"]
    assert scored, "a tile run must produce cells"
    # config asked for 2000 m; the tile is 1000 m and the tile wins.
    assert all(c["cell_id"].startswith("wa5070-1000m-") for c in scored)
    # ...and no coarsening was announced, because none happened.
    assert not [e for e in events if e.get("event") == "grid_info"]


async def test_only_core_cells_are_scored_never_the_halo(stubbed):  # noqa: F811
    tile = _corridor_tile(1000)
    final, agents, _ = await run_tile(tile)

    scored_ids = {c["cell_id"] for c in final["scored_cells"]}
    core = set(tile.core_cell_ids)
    halo = set(tile.halo_cell_ids)

    assert scored_ids == core, (
        f"expected exactly the {len(core)} core cells, got {len(scored_ids)}"
    )
    assert not (scored_ids & halo), "a halo cell was scored"
    # Per agent too — the engine could mask a leak by dropping unknown cells.
    for agent_id, result in agents.items():
        cells = result["scored_cells"] if isinstance(result, dict) else result.scored_cells
        ids = {c["cell_id"] if isinstance(c, dict) else c.cell_id for c in cells}
        assert not (ids & halo), f"{agent_id} scored halo cells"
        assert ids <= core, f"{agent_id} scored cells outside the tile core"


async def test_halo_is_offered_as_context_but_not_as_work(stubbed):  # noqa: F811
    tile = _corridor_tile(1000)
    _, _, events = await run_tile(tile)

    ctx = next(e for e in events if e.get("event") == "spatial_context")
    # Coverage describes what was scored...
    assert ctx["coverage"]["cells_total"] == tile.cell_count
    # ...and says separately how much context was in play.
    assert ctx["coverage"]["context_cells"] == len(tile.halo_cell_ids)
    assert tile.halo_cell_ids, "this tile should have a halo to test"


async def test_a_tile_with_no_halo_behaves_like_a_plain_run(stubbed):  # noqa: F811
    """The degenerate case must not need a special path."""
    tile = _corridor_tile(1000)
    bare = tile.__class__(
        tile_id=tile.tile_id,
        resolution_m=tile.resolution_m,
        block=tile.block,
        tile_col=tile.tile_col,
        tile_row=tile.tile_row,
        core_cell_ids=tile.core_cell_ids,
        halo_cell_ids=(),
    )
    final, _, events = await run_tile(bare)
    assert {c["cell_id"] for c in final["scored_cells"]} == set(tile.core_cell_ids)
    ctx = next(e for e in events if e.get("event") == "spatial_context")
    assert "context_cells" not in ctx["coverage"]


async def test_run_tile_updates_the_manifest_and_accumulates_cells(stubbed):  # noqa: F811
    """The sweep loop end to end for one tile: manifest transitions, cells
    accumulate to disk, and the outcome is classified from what was produced."""
    counter, tmp_path = stubbed
    from app.sweeps.runner import create_sweep, load_cells, run_tile

    m = create_sweep(AOI["geometry"], "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    assert m.tiles
    tid = max(m.tiles, key=lambda t: t["cell_count"])["tile_id"]
    assert m.tile(tid)["status"] == "pending"

    outcome = await run_tile(m, tid, api_key="stub")

    assert outcome.status == "complete", outcome.reason
    assert m.tile(tid)["status"] == "complete"
    assert m.tile(tid)["cells_scored"] > 0
    assert m.tile(tid)["run_id"]
    # Cells landed outside the manifest, keyed by cell id, without geometry.
    cells = load_cells(m.sweep_id, tmp_path)
    assert len(cells) == m.tile(tid)["cells_scored"]
    assert all("geometry" not in c for c in cells.values())
    assert set(cells) <= set(m.tile(tid)["core_cell_ids"])
    # Totals rolled up.
    assert m.doc["totals"]["complete"] == 1
    assert len(m.pending_tiles()) == len(m.tiles) - 1


async def test_a_whole_small_sweep_completes_and_normalizes_at_region_scope(stubbed):  # noqa: F811
    """Every tile, then one region-wide normalization — the workstream's point.

    Also the regression guard for checkerboarding: if any tile's cells kept
    their per-tile scope, the merged set would carry a mix and the legend would
    be lying about part of the map.
    """
    counter, tmp_path = stubbed
    from app.sweeps.runner import create_sweep, finalize_sweep, run_tile, sweep_cells

    m = create_sweep(AOI["geometry"], "gold", 4000, AGENTS, sweeps_dir=tmp_path)
    for t in list(m.tiles):
        await run_tile(m, t["tile_id"], api_key="stub")

    assert m.is_complete, [t["error"] for t in m.tiles if t["error"]]
    merged = finalize_sweep(m)
    assert merged
    # Every cell carries the region scope — a mix would mean some tile's own
    # normalization survived, which is exactly the checkerboard bug.
    assert all(c["normalization_scope"] == "region" for c in merged)
    assert len({c["cell_id"] for c in merged}) == len(merged), "no duplicated cells"

    # The stub returns one score for every cell, so this run is the uniform
    # case, and the right behaviour is flat — NOT invented hotspots. Asserting
    # a spread here would be asserting a bug.
    scores = {round(c["score"], 6) for c in merged}
    if len(scores) == 1:
        assert {c["relative_score"] for c in merged} == {0.5}
        assert {c["tier"] for c in merged} == {"low"}
    else:
        pcts = sorted(c["percentile"] for c in merged)
        assert pcts[0] < pcts[-1]

    ranked = sweep_cells(m)
    assert ranked[0]["percentile"] >= ranked[-1]["percentile"], "must be best-first"
    assert all("geometry" in c for c in ranked), "geometry regenerates from cell_id"


async def test_a_cancelled_tile_is_released_back_to_pending(stubbed):  # noqa: F811
    """Cancellation must leave the sweep resumable, not broken."""
    import asyncio

    counter, tmp_path = stubbed
    from app.sweeps.runner import create_sweep, run_tile

    m = create_sweep(AOI["geometry"], "gold", 4000, AGENTS, sweeps_dir=tmp_path)
    tid = m.tiles[0]["tile_id"]

    async def emit(payload):
        # Cancel from inside the run, the way a client disconnect does.
        if payload.get("event") == "agent_started":
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_tile(m, tid, api_key="stub", emit_fn=emit)

    assert m.tile(tid)["status"] == "pending", "an interrupted tile is not failed"
    assert tid in [t["tile_id"] for t in m.pending_tiles()]


async def test_tile_cells_are_unclipped_squares(stubbed):  # noqa: F811
    """Clipping a sweep cell to its tile would draw the tile grid on the map —
    an artifact of how the work was divided, not a fact about the ground."""
    from shapely.geometry import shape

    from app.scoring.grid import cell_id_to_geojson

    tile = _corridor_tile(1000)
    final, _, _ = await run_tile(tile)
    for c in final["scored_cells"][:5]:
        drawn = shape(c["geometry"])
        canonical = shape(cell_id_to_geojson(c["cell_id"]))
        assert drawn.equals(canonical) or drawn.symmetric_difference(canonical).area < 1e-12
