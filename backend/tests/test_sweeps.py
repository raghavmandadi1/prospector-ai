"""Sweep manifests, the tile-success classifier, estimates, diffs and export.

The most important test here is `test_the_real_all_agents_failed_record_is_not_complete`.
"Steps for Raghav 3.0" §40.2 says "resume = skip tiles marked complete, retry
failed", which assumes a tile that returned cleanly did some work. There is a
run record on disk proving otherwise: all six agents failed, zero LLM calls, and
top-level status "completed", because `run_analysis` sets that unconditionally.

Resuming on that signal marks a corridor complete having scored nothing, and a
grid of zero-confidence placeholders renders as barren ground rather than as a
failure. So the classifier is fed the real artifact, with a synthetic twin that
runs even when data/runs/ is empty (it is gitignored, so on a fresh clone it is).
"""
import json
from pathlib import Path

import pytest
from shapely.geometry import box, mapping

from app.export import scored_cells_to_csv, to_dms, to_utm, utm_zone_for
from app.sweeps.diff import diff_sweeps
from app.sweeps.estimate import batches_for, estimate_sweep
from app.sweeps.manifest import (
    SWEEP_COMPLETE,
    SWEEP_PARTIAL,
    SweepManifest,
    classify_tile,
    delete_sweep,
    list_sweeps,
)
from app.sweeps.runner import create_sweep, load_cells, store_cells
from app.sweeps.tiles import tiles_for_region

REGION = mapping(box(-121.68, 47.57, -121.40, 47.76))
AGENTS = ["lithology", "structure"]
REPO_ROOT = Path(__file__).resolve().parents[2]


def cell(cid, score=0.5, conf=0.7, **kw):
    return dict({"cell_id": cid, "score": score, "confidence": conf}, **kw)


def agents_all(status):
    return {a: {"agent_id": a, "status": status} for a in AGENTS}


# ===========================================================================
# tile-success classifier — never trust a clean return
# ===========================================================================


def test_a_normal_tile_is_complete():
    o = classify_tile([cell("a"), cell("b")], agents_all("completed"), {"llm_calls": 4})
    assert o.status == "complete"
    assert o.cells_scored == 2


def test_every_agent_failed_is_not_complete_however_it_returned():
    o = classify_tile([cell("a")], agents_all("failed"), {"llm_calls": 0})
    assert o.status == "failed"
    assert "every agent failed" in o.reason


def test_a_full_grid_of_placeholders_is_not_complete():
    """The engine fills unscored cells at confidence 0 so grid coverage is never
    lost. That is right for the map and fatal for a completion signal: a tile
    where nothing parsed still returns a full, plausible-looking grid."""
    placeholders = [cell(f"c{i}", score=0.0, conf=0.0) for i in range(50)]
    o = classify_tile(placeholders, agents_all("completed"), {"llm_calls": 6})
    assert o.status == "failed"
    assert "placeholders" in o.reason


def test_no_agents_at_all_is_not_complete():
    assert classify_tile([cell("a")], {}, {"llm_calls": 1}).status == "failed"


def test_a_fully_cached_tile_is_complete_despite_zero_llm_calls():
    """A re-sweep that hits cache on everything makes no calls and is the
    workflow the cache exists for. Zero calls must not be read as failure."""
    o = classify_tile(
        [cell("a"), cell("b")], agents_all("completed"), {"llm_calls": 0}, cache_hits=2
    )
    assert o.status == "complete"


def test_cells_from_nowhere_are_refused():
    """No calls, no cache hits, yet scored cells appeared — something is wrong
    and marking the tile complete would bake it in."""
    o = classify_tile([cell("a")], agents_all("completed"), {"llm_calls": 0}, cache_hits=0)
    assert o.status == "failed"


def test_partial_agent_failure_still_completes():
    """One agent down is a degraded tile, not a lost one — the composite just
    carries less weight. Failing the tile would abandon five agents' work."""
    mixed = {"lithology": {"status": "completed"}, "structure": {"status": "failed"}}
    o = classify_tile([cell("a")], mixed, {"llm_calls": 2})
    assert o.status == "complete"
    assert o.agents_failed == 1


def test_the_real_all_agents_failed_record_is_not_complete():
    """Fed the actual artifact, not a synthetic one.

    data/runs/ is gitignored, so this skips on a fresh clone. The synthetic
    twins above cover the same logic unconditionally; this one proves the
    classifier matches reality rather than my model of it.
    """
    runs = sorted((REPO_ROOT / "data" / "runs").glob("*.json"))
    bad = None
    for p in runs:
        doc = json.loads(p.read_text())
        results = doc.get("agent_results") or {}
        if results and all(r.get("status") == "failed" for r in results.values()):
            bad = doc
            break
    if bad is None:
        pytest.skip("no all-agents-failed run record on disk to check against")

    assert bad["status"] == "completed", "the record really does claim success"
    outcome = classify_tile(
        bad.get("composite_cells") or [],
        bad["agent_results"],
        (bad.get("outputs") or {}).get("usage") or {},
    )
    assert outcome.status == "failed", (
        "the classifier must disagree with the record's own status field"
    )


# ===========================================================================
# manifest lifecycle and resume
# ===========================================================================


def test_create_seeds_every_tile_as_pending(tmp_path):
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    assert m.tiles
    assert all(t["status"] == "pending" for t in m.tiles)
    assert len(m.pending_tiles()) == len(m.tiles)
    assert m.path.exists()


def test_manifest_round_trips_through_disk(tmp_path):
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    again = SweepManifest.load(m.sweep_id, sweeps_dir=tmp_path)
    assert again.sweep_id == m.sweep_id
    assert [t["tile_id"] for t in again.tiles] == [t["tile_id"] for t in m.tiles]


def test_an_interrupted_tile_goes_back_to_pending_not_failed(tmp_path):
    """A closed laptop lid is not a bad tile. Marking it failed would make a
    resumable sweep look broken and could make Resume skip it."""
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    tid = m.tiles[0]["tile_id"]
    m.mark_tile_running(tid)
    assert m.tile(tid)["status"] == "running"
    m.release_tile(tid)
    assert m.tile(tid)["status"] == "pending"
    assert tid in [t["tile_id"] for t in m.pending_tiles()]


def test_a_running_tile_read_back_from_disk_counts_as_pending(tmp_path):
    """Nothing but an interruption can leave a tile `running` in a manifest
    being read back, so resume must pick it up."""
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    tid = m.tiles[0]["tile_id"]
    m.mark_tile_running(tid)
    reloaded = SweepManifest.load(m.sweep_id, sweeps_dir=tmp_path)
    assert tid in [t["tile_id"] for t in reloaded.pending_tiles()]


def test_resume_skips_completed_tiles(tmp_path):
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    done = m.tiles[0]["tile_id"]
    m.mark_tile_outcome(done, classify_tile([cell("a")], agents_all("completed"), {"llm_calls": 2}))
    pending = [t["tile_id"] for t in m.pending_tiles()]
    assert done not in pending
    assert len(pending) == len(m.tiles) - 1


def test_is_complete_requires_every_tile(tmp_path):
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    ok = classify_tile([cell("a")], agents_all("completed"), {"llm_calls": 2})
    for t in m.tiles[:-1]:
        m.mark_tile_outcome(t["tile_id"], ok)
    assert not m.is_complete
    m.mark_tile_outcome(m.tiles[-1]["tile_id"], ok)
    assert m.is_complete
    m.finish()
    assert m.status == SWEEP_COMPLETE


def test_a_failed_tile_makes_the_sweep_partial_not_complete(tmp_path):
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    ok = classify_tile([cell("a")], agents_all("completed"), {"llm_calls": 2})
    bad = classify_tile([cell("a")], agents_all("failed"), {"llm_calls": 0})
    for t in m.tiles[:-1]:
        m.mark_tile_outcome(t["tile_id"], ok)
    m.mark_tile_outcome(m.tiles[-1]["tile_id"], bad)
    m.finish()
    assert m.status == SWEEP_PARTIAL
    assert not m.is_complete


def test_manifest_never_writes_a_secret(tmp_path):
    """The dev path takes the Anthropic key in the request body, so anything
    persisting a config dict is one spread away from writing a key to disk."""
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    m.set_inputs(anthropic_api_key="sk-should-never-land")
    m.write()
    # assert_no_secrets raises inside write(), which swallows and warns — so the
    # file must simply not contain the key.
    assert "sk-should-never-land" not in m.path.read_text()


def test_list_and_delete(tmp_path):
    a = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    b = create_sweep(REGION, "gold", 1000, AGENTS, sweeps_dir=tmp_path)
    ids = {s["sweep_id"] for s in list_sweeps(tmp_path)}
    assert {a.sweep_id, b.sweep_id} <= ids
    assert delete_sweep(a.sweep_id, tmp_path)
    assert a.sweep_id not in {s["sweep_id"] for s in list_sweeps(tmp_path)}


def test_list_survives_a_corrupt_file(tmp_path):
    """A crash mid-write leaves a .tmp; a hand-edit can leave broken JSON.
    Neither may take out the history list."""
    create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    (tmp_path / "garbage.json").write_text("{not json")
    assert len(list_sweeps(tmp_path)) >= 1


# ===========================================================================
# region-wide normalization (§39)
# ===========================================================================


def test_finalize_refuses_a_partial_sweep(tmp_path):
    from app.sweeps.runner import finalize_sweep

    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    store_cells(m.sweep_id, {"wa5070-2000m-000100-000100": cell("wa5070-2000m-000100-000100")}, tmp_path)
    with pytest.raises(ValueError, match="outstanding"):
        finalize_sweep(m)


def test_finalize_normalizes_over_the_whole_region(tmp_path):
    """The point of the workstream: one ranking across every tile, so a cell in
    a barren tile is not promoted to 'high' by having weak neighbours."""
    from app.sweeps.runner import finalize_sweep

    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    ok = classify_tile([cell("a")], agents_all("completed"), {"llm_calls": 2})
    for t in m.tiles:
        m.mark_tile_outcome(t["tile_id"], ok)

    ids = [t["core_cell_ids"][0] for t in m.tiles]
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.99][: len(ids)]
    store_cells(m.sweep_id, {i: cell(i, score=s) for i, s in zip(ids, scores)}, tmp_path)

    merged = finalize_sweep(m)
    assert len(merged) == len(ids)
    assert all(c["normalization_scope"] == "region" for c in merged)
    best = max(merged, key=lambda c: c["score"])
    assert best["tier"] == "high"
    assert best["percentile"] > 0.8


# ===========================================================================
# estimates (§40.3, §42)
# ===========================================================================


def test_batches_round_up_so_a_one_cell_tile_still_costs_a_call():
    assert batches_for(0) == 0
    assert batches_for(1) == 1
    assert batches_for(50) == 1
    assert batches_for(51) == 2


def test_estimate_uses_the_tile_distribution_not_the_total():
    """An area-based estimate understates a ragged region badly, because a tile
    pays a batch per agent whether it holds 2 cells or 50."""
    tiles = tiles_for_region(REGION, 2000)
    e = estimate_sweep(tiles, AGENTS)
    assert e.tiles == len(tiles)
    assert e.llm_calls > e.ideal_llm_calls, "ragged tiling must cost more than packed"
    assert e.raggedness_overhead > 1.0


def test_estimate_says_whether_it_is_measured_or_guessed():
    tiles = tiles_for_region(REGION, 2000)
    assert estimate_sweep(tiles, AGENTS).basis == "default"
    measured = estimate_sweep(tiles, AGENTS, measured={"seconds_per_batch": 9.0})
    assert measured.basis == "measured"
    assert measured.est_seconds < estimate_sweep(tiles, AGENTS).est_seconds


def test_a_warm_cache_lowers_the_estimate():
    tiles = tiles_for_region(REGION, 2000)
    cold = estimate_sweep(tiles, AGENTS, cache_hit_fraction=0.0)
    warm = estimate_sweep(tiles, AGENTS, cache_hit_fraction=1.0)
    assert warm.llm_calls == 0
    assert warm.est_cost_usd == 0.0
    assert cold.llm_calls > 0


def test_estimate_scales_with_agent_count():
    tiles = tiles_for_region(REGION, 2000)
    one = estimate_sweep(tiles, ["lithology"])
    six = estimate_sweep(tiles, AGENTS + ["a", "b", "c", "d"])
    assert six.llm_calls == one.llm_calls * 6


# ===========================================================================
# diff (§41.2)
# ===========================================================================


def _sweep_with(tmp_path, scores):
    m = create_sweep(REGION, "gold", 2000, AGENTS, sweeps_dir=tmp_path)
    store_cells(m.sweep_id, {k: cell(k, score=v) for k, v in scores.items()}, tmp_path)
    return m


def test_diff_reports_movement_per_cell(tmp_path):
    a = _sweep_with(tmp_path, {"x": 0.20, "y": 0.50, "z": 0.80})
    b = _sweep_with(tmp_path, {"x": 0.30, "y": 0.50, "z": 0.60})
    d = diff_sweeps(a, b)
    assert d["summary"]["n_common"] == 3
    assert d["summary"]["moved_up"] == 1
    assert d["summary"]["moved_down"] == 1
    assert d["summary"]["unchanged"] == 1
    # Ordered by how far a cell moved, so the biggest change is first.
    assert d["cells"][0]["cell_id"] == "z"


def test_diff_refuses_to_call_anything_significant_without_a_noise_floor(tmp_path):
    a = _sweep_with(tmp_path, {"x": 0.20})
    b = _sweep_with(tmp_path, {"x": 0.25})
    d = diff_sweeps(a, b)
    assert d["summary"]["significant"] is None
    assert "noise floor" in d["summary"]["interpretation_note"]
    assert d["cells"][0]["significant"] is None


def test_diff_with_a_noise_floor_separates_signal_from_jitter(tmp_path):
    a = _sweep_with(tmp_path, {"big": 0.20, "small": 0.50})
    b = _sweep_with(tmp_path, {"big": 0.60, "small": 0.51})
    d = diff_sweeps(a, b, noise_floor=0.05)
    by_id = {c["cell_id"]: c for c in d["cells"]}
    assert by_id["big"]["significant"] is True
    assert by_id["small"]["significant"] is False
    assert d["summary"]["significant"] == 1


def test_diff_names_a_uniform_shift_as_such(tmp_path):
    """A change that lifts every cell equally is a recalibration, not a
    discrimination — the exact failure mode §41.2 wants visible."""
    a = _sweep_with(tmp_path, {"a": 0.10, "b": 0.20, "c": 0.30})
    b = _sweep_with(tmp_path, {"a": 0.20, "b": 0.30, "c": 0.40})
    d = diff_sweeps(a, b, noise_floor=0.01)
    assert "Uniform shift" in d["summary"]["interpretation_note"]


def test_diff_tracks_cells_present_in_only_one_sweep(tmp_path):
    a = _sweep_with(tmp_path, {"shared": 0.5, "gone": 0.4})
    b = _sweep_with(tmp_path, {"shared": 0.5, "new": 0.6})
    d = diff_sweeps(a, b)
    assert d["only_in_a"] == ["gone"]
    assert d["only_in_b"] == ["new"]


# ===========================================================================
# export (§42, and shared with §48.4)
# ===========================================================================


def test_utm_zone_is_computed_not_assumed():
    """§48.4 says "UTM 10N", which is wrong for a third of Washington: zone 10N
    ends at 120°W and Republic, Metaline, Toroda and Colville are all east of
    it. Republic is the most-cited district in the gold knowledge base."""
    assert utm_zone_for(-121.55) == 10  # NF Snoqualmie corridor
    assert utm_zone_for(-118.74) == 11  # Republic
    _, _, zone_west = to_utm(-121.55, 47.65)
    _, _, zone_east = to_utm(-118.74, 48.65)
    assert zone_west == "10N" and zone_east == "11N"


def test_utm_easting_stays_in_range_in_both_zones():
    for lon, lat in ((-121.55, 47.65), (-118.74, 48.65), (-124.1, 47.75)):
        e, n, _ = to_utm(lon, lat)
        assert 100_000 < e < 900_000, f"{lon} produced a nonsense easting {e}"
        assert 5_000_000 < n < 5_600_000


def test_dms_formatting():
    assert to_dms(47.65, True) == "47°39'00.00\"N"
    assert to_dms(-121.55, False) == "121°33'00.00\"W"
    assert to_dms(-47.65, True).endswith("S")


def test_dms_carries_instead_of_emitting_sixty_seconds():
    """0.65 x 60 is 38.99999999999999 in binary floating point. Truncating the
    minutes and then rounding the seconds yields 47°38'60.00\", which is not a
    coordinate. Every value here is one that trips the naive order."""
    for v in (47.65, 47.35, 48.05, 121.55, 0.65, 47.9999999):
        out = to_dms(v, True)
        minutes = out.split("°")[1].split("'")[0]
        seconds = out.split("'")[1].rstrip('"NSEW')
        assert int(minutes) < 60, f"{v} produced {out}"
        assert float(seconds) < 60.0, f"{v} produced {out}"


def test_csv_has_a_fixed_column_order_even_with_ragged_rows():
    """A row missing an optional key must not shift every later column —
    nobody checks a CSV header against its 400th line."""
    rows = [
        cell("wa5070-1000m-000349-000380", score=0.9, tier="high", evidence=["a", "b"]),
        {"cell_id": "wa5070-1000m-000350-000380", "score": 0.4},
    ]
    text = scored_cells_to_csv(rows)
    lines = text.strip().splitlines()
    header = lines[0].split(",")
    assert header[0] == "rank" and "utm_zone" in header
    assert all(len(line.split(",")) >= len(header) - 2 for line in lines[1:])


def test_csv_carries_coordinates_derived_from_the_cell_id():
    """A cell id regenerates its square exactly, so a CSV row is locatable even
    when the caller passed no geometry."""
    text = scored_cells_to_csv([{"cell_id": "wa5070-1000m-000349-000380", "score": 0.5}])
    row = text.strip().splitlines()[1]
    assert "10N" in row or "11N" in row
    assert "°" in row


def test_csv_flattens_evidence_onto_one_line():
    """Newlines in a quoted field are legal CSV and break grep and half the
    spreadsheet importers in the world."""
    text = scored_cells_to_csv([cell("a", evidence=["first", "second"])])
    assert "first | second" in text
    assert len(text.strip().splitlines()) == 2
