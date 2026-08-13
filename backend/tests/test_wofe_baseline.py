"""
Acceptance tests for the deterministic WofE baseline.

The baseline exists to be the thing the LLM composite is checked against, so it
has to be trustworthy in its own right. Two properties matter more than the rest
and both are covered here:

* **The published numbers are the published numbers.** If a contrast drifts, every
  comparison downstream silently becomes a comparison against something else.
* **Out of scope means no answer.** OF01-501 was fitted on 50 epithermal gold
  sites in NE Washington. A confident number for the North Cascades would be
  indistinguishable from a real one and would quietly poison the benchmark.

Most tests run against a hand-built synthetic ``of00495.sqlite`` so they work on
a fresh clone where ``data/derived/`` does not exist. The few that need the real
build are marked and skipped when it is absent.

Run:  .venv/bin/python -m pytest backend/tests/test_wofe_baseline.py -q
"""
import importlib.util
import math
import sqlite3
import sys
from pathlib import Path

import pytest

from app.scoring.grid import make_cell_id, parse_cell_id
from app.scoring.wofe_baseline import (
    FAULT_BUFFER_M,
    LITHO_BUFFER_M,
    MIN_COVERAGE_FRAC,
    PLACER_BUFFER_M,
    TRACT_SCORE_BANDS,
    WofEBaseline,
    WofERefusal,
    score_cells_wofe,
    scored_only,
)
from app.spatial.wofe_grid import WOFE_CONTRASTS

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "derived" / "of00495.sqlite"

# A patch of the fixed grid inside the real OF-00-495 index range (cols
# 1812-2786, rows 1184-1822 in the real build), so synthetic cell ids are
# plausible and the projection code is exercised on real Washington ground.
BASE_COL = 2240
BASE_ROW = 1590
PATCH = 40  # cells per side

#: A unit the WofE study tested and found zero training sites in. Not invented —
#: Kigd is called out by name in knowledge/lithology/gold.md as 1,289 km² with
#: zero sites, which is exactly the "Outside" case this baseline must score low.
BARREN_UNIT = "Kigd"


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------


def build_db(path: Path, cells: dict) -> Path:
    """Write a minimal ``of00495.sqlite`` with the contract schema.

    ``cells`` maps ``(col, row)`` -> ``(geol_unit, frac, fault_code)``. Only the
    columns this module reads are populated; the rest are NULL, which is exactly
    the state the real build leaves them in for most cells.
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE wofe_cell (
          cell_id TEXT PRIMARY KEY,
          geol_unit TEXT,
          geol_unit_frac REAL,
          fault_code INTEGER,
          fold_code INTEGER,
          dike_unit TEXT
        );
        CREATE TABLE vat (grid TEXT, value INTEGER, count INTEGER, label TEXT);
        """
    )
    rows = [
        (make_cell_id(c, r, 250), unit, frac, fault, None, None)
        for (c, r), (unit, frac, fault) in cells.items()
    ]
    conn.executemany("INSERT INTO wofe_cell VALUES (?,?,?,?,?,?)", rows)
    conn.execute(
        "INSERT INTO meta VALUES ('counts', ?)",
        ('{"wofe_cell": %d}' % len(rows),),
    )
    conn.commit()
    conn.close()
    return path


def flat_patch(unit: str = BARREN_UNIT) -> dict:
    """A featureless PATCH×PATCH block: one barren unit, no faults anywhere."""
    return {
        (BASE_COL + dc, BASE_ROW + dr): (unit, 1.0, None)
        for dc in range(PATCH)
        for dr in range(PATCH)
    }


@pytest.fixture
def baseline_factory(tmp_path):
    """Build a WofEBaseline over a synthetic DB, with the 24k azimuth test off.

    The azimuth test reads a *second* database and is deliberately excluded from
    these tests: it must never be the reason a core assertion passes or fails.
    Its own contract — unknown ⇒ component dropped, not guessed — is asserted
    separately in ``test_unknown_trend_is_dropped_not_guessed``.
    """
    counter = {"n": 0}

    def make(cells: dict) -> WofEBaseline:
        counter["n"] += 1
        path = tmp_path / f"of00495_{counter['n']}.sqlite"
        build_db(path, cells)
        return WofEBaseline(wofe_db=path, use_geology_azimuth=False)

    return make


def cid(dc: int, dr: int, res: int = 250) -> str:
    """Cell id at an offset from the patch origin, at 250 m."""
    return make_cell_id(BASE_COL + dc, BASE_ROW + dr, res)


# ---------------------------------------------------------------------------
# 1. The published table
# ---------------------------------------------------------------------------


def test_contrast_table_matches_the_published_numbers_exactly():
    """OF01-501 Appendix / knowledge/lithology/gold.md, transcribed literally.

    Hardcoded on purpose. If someone edits WOFE_CONTRASTS this test is the thing
    that notices, and a "small correction" to a fitted contrast is not a small
    change — it silently redefines every benchmark comparison ever made against
    this baseline.
    """
    expected = {
        "Eck": (4.55, 4, 26.3),
        "Evkct": (3.62, 5, 43.1),
        "Evst": (3.42, 5, 50.6),
        "Evsf": (3.21, 30, 302.5),
        "Evkf": (2.56, 3, 45.3),
        "Eco": (1.96, 2, 35.2),
    }
    assert set(WOFE_CONTRASTS) == set(expected), (
        "the favourable-unit set changed; the study found exactly six units with "
        "a positive correlation and every other unit had zero training sites"
    )
    for unit, (contrast, sites, area) in expected.items():
        got = WOFE_CONTRASTS[unit]
        assert got["contrast"] == contrast, unit
        assert got["training_sites"] == sites, unit
        assert got["area_km2"] == area, unit

    # The training sites must still add up to the 50 the model was fitted on.
    assert sum(v["training_sites"] for v in WOFE_CONTRASTS.values()) == 49, (
        "49 of the 50 training sites fall in the favourable units; the 50th is "
        "the Kettle mine, buried under Quaternary cover in the permissive tract"
    )


def test_published_buffers_and_tract_bands_are_unchanged():
    assert FAULT_BUFFER_M == 1700.0
    assert LITHO_BUFFER_M == 150.0
    assert PLACER_BUFFER_M == 4000.0
    assert TRACT_SCORE_BANDS == {
        "favourable": (0.70, 0.95),
        "permissive": (0.35, 0.65),
        "non_permissive": (0.00, 0.30),
    }


# ---------------------------------------------------------------------------
# 2. Lithology
# ---------------------------------------------------------------------------


def test_evsf_scores_high_and_a_zero_site_unit_scores_low(baseline_factory):
    """Sanpoil flows host 30 of 50 known deposits; Kigd hosts none."""
    cells = flat_patch()
    cells[(BASE_COL + 5, BASE_ROW + 5)] = ("Evsf", 1.0, None)
    # A fault in the same cell, so Evsf reaches the favourable tract rather than
    # stopping at permissive. Both predictors are what the published favourable
    # tract requires.
    cells[(BASE_COL + 5, BASE_ROW + 5)] = ("Evsf", 1.0, 33)
    b = baseline_factory(cells)

    good = score_cells_wofe([cid(5, 5)], baseline=b)[cid(5, 5)]
    bad = score_cells_wofe([cid(20, 20)], baseline=b)[cid(20, 20)]

    assert good["tract"] == "favourable"
    assert good["wofe_unit"] == "Evsf"
    assert good["contrast"] == 3.21
    assert good["score"] >= 0.70, good

    assert bad["tract"] == "non_permissive"
    assert bad["wofe_unit"] is None
    assert bad["contrast"] == 0.0
    assert bad["score"] <= 0.30, bad
    assert good["score"] > bad["score"] + 0.5


def test_higher_contrast_unit_scores_higher(baseline_factory):
    """Eck (4.55) must outrank Eco (1.96) with everything else held equal."""
    cells = flat_patch()
    cells[(BASE_COL + 5, BASE_ROW + 5)] = ("Eck", 1.0, 33)
    cells[(BASE_COL + 25, BASE_ROW + 25)] = ("Eco", 1.0, 33)
    b = baseline_factory(cells)
    got = score_cells_wofe([cid(5, 5), cid(25, 25)], baseline=b)
    assert got[cid(5, 5)]["score"] > got[cid(25, 25)]["score"]
    assert got[cid(5, 5)]["tract"] == got[cid(25, 25)]["tract"] == "favourable"


def test_litho_buffer_reaches_exactly_one_cell(baseline_factory):
    """The 150 m buffer is one 250 m cell of dilation, and no further.

    A barren cell touching Sanpoil flows is permissive ground by the published
    model. A barren cell two cells away is not.
    """
    cells = flat_patch()
    cells[(BASE_COL + 10, BASE_ROW + 10)] = ("Evsf", 1.0, None)
    b = baseline_factory(cells)

    adjacent = score_cells_wofe([cid(11, 10)], baseline=b)[cid(11, 10)]
    diagonal = score_cells_wofe([cid(11, 11)], baseline=b)[cid(11, 11)]
    two_away = score_cells_wofe([cid(12, 10)], baseline=b)[cid(12, 10)]

    for near in (adjacent, diagonal):
        assert near["wofe_unit"] == "Evsf"
        assert near["litho_buffer_only"] is True
        assert near["unit"] == BARREN_UNIT, "the cell's own rock is unchanged"
        assert near["tract"] == "permissive"
    assert two_away["wofe_unit"] is None
    assert two_away["tract"] == "non_permissive"


# ---------------------------------------------------------------------------
# 3. Structure
# ---------------------------------------------------------------------------


def test_fault_buffer_moves_the_score_up(baseline_factory):
    """Same lithology, fault inside vs outside the published 1700 m buffer."""
    # Two Evsf cells far enough apart that neither sees the other's fault.
    with_fault = flat_patch()
    with_fault[(BASE_COL + 5, BASE_ROW + 5)] = ("Evsf", 1.0, 33)
    b1 = baseline_factory(with_fault)
    near = score_cells_wofe([cid(5, 5)], baseline=b1)[cid(5, 5)]

    no_fault = flat_patch()
    no_fault[(BASE_COL + 5, BASE_ROW + 5)] = ("Evsf", 1.0, None)
    b2 = baseline_factory(no_fault)
    far = score_cells_wofe([cid(5, 5)], baseline=b2)[cid(5, 5)]

    assert near["fault_within_buffer"] is True
    assert near["fault_distance_m"] == 0.0
    assert near["tract"] == "favourable"
    assert far["fault_within_buffer"] is False
    assert far["fault_distance_m"] is None
    assert far["tract"] == "permissive"
    assert near["score"] > far["score"], (
        "the structural predictor must raise the score, not lower it — "
        "OF01-501's optimum fault buffer is a positive correlation"
    )


def test_fault_just_inside_the_buffer_beats_just_outside(baseline_factory):
    """1700 m is a real threshold: 6 cells in, 8 cells out.

    Distances are quantised to whole 250 m steps, so 6 cells = 1500 m (inside)
    and 8 cells = 2000 m (outside). 7 cells is 1750 m, deliberately not used
    because it sits within one quantisation step of the threshold.
    """
    inside = flat_patch()
    inside[(BASE_COL + 5 + 6, BASE_ROW + 5)] = (BARREN_UNIT, 1.0, 33)
    b_in = baseline_factory(inside)
    got_in = score_cells_wofe([cid(5, 5)], baseline=b_in)[cid(5, 5)]

    outside = flat_patch()
    outside[(BASE_COL + 5 + 8, BASE_ROW + 5)] = (BARREN_UNIT, 1.0, 33)
    b_out = baseline_factory(outside)
    got_out = score_cells_wofe([cid(5, 5)], baseline=b_out)[cid(5, 5)]

    assert got_in["fault_distance_m"] == 1500.0
    assert got_in["fault_within_buffer"] is True
    assert got_out["fault_distance_m"] == 2000.0
    assert got_out["fault_within_buffer"] is False
    assert got_in["score"] > got_out["score"]
    # Neither reaches permissive: no favourable lithology, so the published
    # model has both in the non-permissive 92% however close the fault is.
    assert got_in["tract"] == got_out["tract"] == "non_permissive"
    assert got_in["score"] <= 0.30


def test_fault_beyond_the_placer_buffer_is_reported_as_no_fault(baseline_factory):
    """Past 4000 m the distance carries no weight and must not be quoted."""
    cells = flat_patch()
    cells[(BASE_COL + 5 + 20, BASE_ROW + 5)] = (BARREN_UNIT, 1.0, 33)  # 5000 m
    b = baseline_factory(cells)
    got = score_cells_wofe([cid(5, 5)], baseline=b)[cid(5, 5)]
    assert got["fault_distance_m"] is None
    assert got["score"] == 0.0
    assert any("No OF-00-495 mapped fault within" in e for e in got["evidence"])


def test_unknown_trend_is_dropped_not_guessed():
    """A None trend must not be scored as False.

    ``_band_position`` renormalises over the components that have an input. If it
    instead treated unknown as "not favourable", every cell in the Republic
    corridor would be penalised for a hole in the 1:24k geology — a dataset with
    nothing to do with the OF-00-495 model.
    """
    from app.scoring.wofe_baseline import _band_position

    unknown = _band_position(evidence=0.5, closeness=0.5, trend=None)
    negative = _band_position(evidence=0.5, closeness=0.5, trend=False)
    positive = _band_position(evidence=0.5, closeness=0.5, trend=True)
    assert negative < unknown < positive
    assert unknown == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 4. Refusal — the property that keeps the benchmark honest
# ---------------------------------------------------------------------------


def test_cell_outside_the_footprint_gets_no_score(baseline_factory):
    """A cell the study area does not cover returns None, not an extrapolation."""
    b = baseline_factory(flat_patch())
    far = make_cell_id(BASE_COL + 5000, BASE_ROW, 250)
    got = score_cells_wofe([far], baseline=b)[far]
    assert got["score"] is None, "0.0 would be a claim about the ground"
    assert got["in_footprint"] is False
    assert got["refused"] == WofERefusal.OUT_OF_FOOTPRINT
    assert got["tract"] is None
    assert got["data_sources_used"] == []
    assert scored_only({far: got}) == {}


def test_partially_covered_coarse_cell_is_refused(baseline_factory):
    """A coarse cell straddling the edge of the study area is not scored.

    Built as a 1000 m cell (4×4 = 16 children at 250 m) with only 4 children
    present — 25% coverage, below MIN_COVERAGE_FRAC.
    """
    assert MIN_COVERAGE_FRAC == 0.5
    col1000, row1000 = 600, 400
    base_col, base_row = col1000 * 4, row1000 * 4
    cells = {
        (base_col + dc, base_row + dr): ("Evsf", 1.0, 33)
        for dc in range(2)
        for dr in range(2)
    }
    b = baseline_factory(cells)
    target = make_cell_id(col1000, row1000, 1000)
    got = score_cells_wofe([target], baseline=b)[target]
    assert got["score"] is None
    assert got["refused"] == WofERefusal.PARTIAL_COVERAGE
    assert got["coverage_frac"] == 0.25


def test_non_gold_mineral_is_refused(baseline_factory):
    b = baseline_factory(flat_patch())
    for mineral in ("silver", "copper", "uranium", "lithium"):
        got = score_cells_wofe([cid(5, 5)], target_mineral=mineral, baseline=b)
        entry = got[cid(5, 5)]
        assert entry["score"] is None, mineral
        assert "out of scope" in entry["refused"]
        assert "epithermal *gold*" in entry["refused"]


def test_missing_database_refuses_every_cell(tmp_path):
    """A fresh clone has no data/derived/. That must not be a traceback."""
    b = WofEBaseline(wofe_db=tmp_path / "absent.sqlite", use_geology_azimuth=False)
    assert b.available is False
    got = score_cells_wofe([cid(1, 1), cid(2, 2)], baseline=b)
    assert len(got) == 2
    for entry in got.values():
        assert entry["score"] is None
        assert entry["refused"] == WofERefusal.NO_DB


def test_malformed_cell_id_is_refused_not_crashed(baseline_factory):
    b = baseline_factory(flat_patch())
    got = score_cells_wofe(["not-a-cell-id", cid(5, 5)], baseline=b)
    assert got["not-a-cell-id"]["refused"] == WofERefusal.BAD_CELL_ID
    assert got[cid(5, 5)]["score"] is not None


# ---------------------------------------------------------------------------
# 5. Resolution behaviour
# ---------------------------------------------------------------------------


def test_coarse_cell_is_the_mean_of_its_native_children(baseline_factory):
    """A 1000 m cell is the equal-area mean of its sixteen 250 m children.

    Scoring the coarse cell directly on presence tests would saturate: measured
    on the real Republic AOI, direct 1000 m scoring put 97 of 105 cells in the
    favourable tract. Rolling up preserves the discrimination.
    """
    col1000, row1000 = 600, 400
    bc, br = col1000 * 4, row1000 * 4
    cells = {}
    for dc in range(4):
        for dr in range(4):
            # Four favourable children with a fault, twelve barren ones.
            if dc < 1:
                cells[(bc + dc, br + dr)] = ("Evsf", 1.0, 33)
            else:
                cells[(bc + dc, br + dr)] = (BARREN_UNIT, 1.0, None)
    b = baseline_factory(cells)

    coarse_id = make_cell_id(col1000, row1000, 1000)
    child_ids = [
        make_cell_id(bc + dc, br + dr, 250) for dc in range(4) for dr in range(4)
    ]
    coarse = score_cells_wofe([coarse_id], baseline=b)[coarse_id]
    children = score_cells_wofe(child_ids, baseline=b)

    child_scores = [children[c]["score"] for c in child_ids]
    assert coarse["wofe_cells"] == 16
    assert coarse["coverage_frac"] == 1.0
    assert coarse["score"] == pytest.approx(sum(child_scores) / 16, abs=1e-4)
    assert coarse["score_max"] == pytest.approx(max(child_scores), abs=1e-4)
    assert coarse["score_min"] == pytest.approx(min(child_scores), abs=1e-4)
    # The mix is exposed, so "0.4 on average" cannot be mistaken for uniform.
    assert sum(coarse["tract_fracs"].values()) == pytest.approx(1.0)
    # Eight favourable children, not four: the 4 Evsf cells plus the 4 barren
    # cells in the next column, which are inside the 150 m lithologic buffer and
    # within 250 m of the Evsf cells' fault. Both published predictors fire, so
    # they are favourable ground by the model's own definition. That the buffer
    # doubles the favourable count from a one-cell-wide unit is exactly the
    # over-generosity the module docstring records — asserted here so it cannot
    # change without someone noticing.
    assert coarse["tract_fracs"]["favourable"] == pytest.approx(8 / 16)
    assert coarse["tract_fracs"]["non_permissive"] == pytest.approx(8 / 16)


def test_125m_cell_inherits_from_its_250m_parent(baseline_factory):
    cells = flat_patch()
    cells[(BASE_COL + 5, BASE_ROW + 5)] = ("Evsf", 1.0, 33)
    b = baseline_factory(cells)
    parent = cid(5, 5)
    fine = make_cell_id((BASE_COL + 5) * 2, (BASE_ROW + 5) * 2, 125)
    got = score_cells_wofe([parent, fine], baseline=b)
    assert got[fine]["inherited_from_250m"] is True
    assert got[fine]["score"] == got[parent]["score"]
    assert any("Inherited" in e or "inherited" in e for e in got[fine]["evidence"])


def test_every_input_cell_appears_in_the_result(baseline_factory):
    """Silence is not an acceptable answer — a caller must see the refusals."""
    b = baseline_factory(flat_patch())
    ids = [cid(1, 1), cid(2, 2), make_cell_id(999, 999, 250), "junk"]
    got = score_cells_wofe(ids, baseline=b)
    assert set(got) == set(ids)


# ---------------------------------------------------------------------------
# 6. The Spearman helper in scripts/benchmark.py
# ---------------------------------------------------------------------------


def _benchmark_module():
    """Import scripts/benchmark.py by path — it is a script, not a package."""
    path = REPO_ROOT / "scripts" / "benchmark.py"
    spec = importlib.util.spec_from_file_location("_bench_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bench_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_spearman_matches_a_hand_computed_value_with_ties():
    """Worked by hand, because the tie case is where naive versions go wrong.

        x  = [1, 2, 3, 4, 5]        ranks 1, 2, 3, 4, 5
        y  = [2, 2, 3, 1, 5]        ranks 2.5, 2.5, 4, 1, 5   (the 2s tie)

        mean rank = 3 on both sides
        dx = [-2, -1, 0, 1, 2]      dy = [-0.5, -0.5, 1, -2, 2]
        Σ dx·dy = 3.5   Σ dx² = 10   Σ dy² = 9.5
        rho = 3.5 / sqrt(10 · 9.5) = 3.5 / sqrt(95) = 0.3590924...

    The shortcut formula 1 − 6Σd²/n(n²−1) gives 0.35 here, which is wrong; it is
    only valid without ties. That difference is the whole point of the test.
    """
    bench = _benchmark_module()
    x = [1, 2, 3, 4, 5]
    y = [2, 2, 3, 1, 5]

    assert bench.rank(y) == [2.5, 2.5, 4.0, 1.0, 5.0]
    expected = 3.5 / math.sqrt(95.0)
    assert bench.spearman(x, y) == pytest.approx(expected, abs=1e-9)
    assert bench.spearman(x, y) == pytest.approx(0.3590924, abs=1e-6)

    # And it is not the shortcut formula's answer.
    assert abs(bench.spearman(x, y) - 0.35) > 1e-3


def test_spearman_edge_cases():
    bench = _benchmark_module()
    assert bench.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert bench.spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    # Symmetric in its arguments.
    assert bench.spearman([3, 1, 2], [9, 4, 5]) == pytest.approx(
        bench.spearman([9, 4, 5], [3, 1, 2])
    )
    # Undefined, and must say so rather than returning 0.0 — a constant side has
    # no ranking, which is exactly what the WofE baseline produces over a
    # uniformly non-permissive AOI.
    assert bench.spearman([1, 2, 3], [7, 7, 7]) is None
    assert bench.spearman([1, 2], [1, 2]) is None
    assert bench.spearman([1, 2, 3], [1, 2]) is None


def test_workings_gate_refuses_with_a_reason():
    """Never report a metric for an unverified AOI, and always say why."""
    bench = _benchmark_module()
    ok, why = bench.workings_gate({"known_workings": [], "workings_verified": True})
    assert ok is False and "no known_workings" in why

    ok, why = bench.workings_gate(
        {"known_workings": [{"lon": 0, "lat": 0}], "workings_verified": False}
    )
    assert ok is False and "workings_verified is false" in why

    ok, why = bench.workings_gate(
        {"known_workings": [{"lon": 0, "lat": 0}], "workings_verified": True}
    )
    assert ok is True and why == ""


# ---------------------------------------------------------------------------
# 7. Against the real build, when it exists
# ---------------------------------------------------------------------------

real_db = pytest.mark.skipif(
    not REAL_DB.exists(),
    reason="data/derived/of00495.sqlite not built (scripts/build_of00495.py)",
)


def square_aoi(lon, lat, half_deg=0.05):
    return {
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
    }


@real_db
def test_republic_is_in_scope_and_discriminates():
    from app.scoring.grid import generate_grid

    b = WofEBaseline(use_geology_azimuth=False)
    cells = generate_grid(square_aoi(-118.74, 48.65), 250)
    got = score_cells_wofe(cells, baseline=b)
    scores = scored_only(got)
    assert len(scores) > 1000
    assert len(set(scores.values())) > 20, (
        "a baseline that gives one value to the whole Republic graben is not "
        "discriminating and cannot rank anything"
    )
    assert max(scores.values()) >= 0.70
    assert min(scores.values()) <= 0.30
    favourable = [g for g in got.values() if g.get("tract") == "favourable"]
    assert favourable, "Eureka Gulch must contain favourable-tract cells"


@real_db
def test_monte_cristo_is_refused_not_extrapolated():
    """Orogenic gold in the North Cascades. The model must decline.

    This is the single most important real-data assertion here: Monte Cristo is a
    genuine ~400 koz district, so a scorer that wanted to look good would happily
    return a high number for it. The published model has no basis for one.
    """
    from app.scoring.grid import generate_grid

    b = WofEBaseline(use_geology_azimuth=False)
    cells = generate_grid(square_aoi(-121.44, 48.03), 250)
    got = score_cells_wofe(cells, baseline=b)
    assert scored_only(got) == {}
    reasons = {g["refused"] for g in got.values()}
    assert reasons == {WofERefusal.OUT_OF_FOOTPRINT}


@real_db
def test_real_cell_ids_round_trip_at_250m():
    """Every stored id must be a 250 m id on the ladder — the cache depends on it."""
    conn = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    rows = conn.execute("SELECT cell_id FROM wofe_cell LIMIT 500").fetchall()
    conn.close()
    assert rows
    for (cell_id,) in rows:
        res, col, row = parse_cell_id(cell_id)
        assert res == 250
        assert make_cell_id(col, row, res) == cell_id
