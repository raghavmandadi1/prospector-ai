"""
Tests for the local spatial context — the agents' evidence base.

What these protect, in rough order of how bad the failure would be:

1. **Distances are metres, not degrees.** A degree of longitude is 78 km at
   Washington's latitude and a degree of latitude is 111 km, so a degree-based
   comparison ranks north–south neighbours ~1.4x further away than east–west ones
   at the same true distance. That produces a plausible map and no error message.
2. **Per-cell facts reach the cache key.** `cell_facts` is a dict, and the cache
   key builder used to keep only list and str values — so a cell's own geology and
   occurrence record would have been invisible to it, and every cell would have
   gone on serving a score computed before it had any evidence.
3. **A `truth` field pin never reaches a prompt.** It is benchmark ground truth;
   a model told "someone marked this spot" ranks that spot highly by construction.
4. **Missing artifacts degrade, they do not raise.** `data/derived/` is gitignored
   and absent on a fresh clone, which is the normal state.
5. **`novelty is None` means unknown, not novel.** With no occurrence extract
   built, calling every cell a lead would turn a missing file into a prospecting
   signal.
"""
import json
import math

import pytest

from app.spatial.geometry import LocalMetric, km_between, pad_bbox
from app.spatial.geology import in_favourable_trend
from app.spatial import local_store, occurrences as occ_mod, wofe_grid

# Republic, Ferry County — the most-cited district in the gold knowledge base.
REPUBLIC = (-118.74, 48.65)


# --- geometry ---------------------------------------------------------------


def test_local_metric_is_metric_not_degrees():
    """Equal true distances must measure equal, whatever their bearing.

    The failure this catches: comparing raw degrees makes 0.01 deg of latitude
    (~1106 m) look like the same distance as 0.01 deg of longitude (~735 m at
    this latitude), a 50% error that always favours east–west neighbours.
    """
    m = LocalMetric.for_point(*REPUBLIC)

    east_x, east_y = m.xy(REPUBLIC[0] + 0.01, REPUBLIC[1])
    north_x, north_y = m.xy(REPUBLIC[0], REPUBLIC[1] + 0.01)

    assert east_y == pytest.approx(0.0)
    assert north_x == pytest.approx(0.0)
    # Same degree offset, materially different metre distances — which is the
    # whole point of projecting before measuring.
    assert east_x == pytest.approx(735, abs=10)
    assert north_y == pytest.approx(1106, abs=10)
    assert not math.isclose(east_x, north_y, rel_tol=0.1)


def test_local_metric_agrees_with_the_matcher_helper():
    """`geometry.km_between` and `matcher._km_between` must not diverge.

    Two modules disagreeing about how far apart two points are would be a
    miserable class of bug: toponym corroboration and occurrence distance would
    quietly use different scales.
    """
    from app.toponyms.matcher import _km_between

    a, b = REPUBLIC, (-118.70, 48.68)
    assert km_between(*a, *b) == pytest.approx(_km_between(*a, *b), rel=1e-9)


def test_local_metric_project_matches_point_arithmetic():
    """Projecting a geometry and projecting its coordinates must agree."""
    from shapely.geometry import LineString, Point

    m = LocalMetric.for_point(*REPUBLIC)
    line = m.project(LineString([REPUBLIC, (-118.70, 48.68)]))
    point = m.project(Point(*REPUBLIC))

    assert line.distance(point) == pytest.approx(0.0, abs=1e-6)
    # And the far end sits where xy() says it does.
    assert list(line.coords)[-1] == pytest.approx(m.xy(-118.70, 48.68))


def test_pad_bbox_expands_by_kilometres_in_both_axes():
    bbox = (-118.80, 48.60, -118.68, 48.70)
    padded = pad_bbox(bbox, 5.0)

    assert padded[0] < bbox[0] and padded[2] > bbox[2]
    assert padded[1] < bbox[1] and padded[3] > bbox[3]
    # 5 km of latitude is a smaller angle than 5 km of longitude up here, so the
    # padding must NOT be symmetric in degrees.
    dlon = bbox[0] - padded[0]
    dlat = bbox[1] - padded[1]
    assert dlon > dlat
    assert km_between(padded[0], bbox[1], bbox[0], bbox[1]) == pytest.approx(5.0, rel=0.02)


# --- OF01-501 trend folding -------------------------------------------------


@pytest.mark.parametrize(
    "azimuth,expected",
    [
        (0.0, True),      # due north — centre of the favourable band
        (30.0, True),     # NNE edge, inclusive
        (30.1, False),    # just outside
        (165.0, True),    # 345 deg folded — the NNW half of the band
        (164.9, False),
        (179.9, True),
        (90.0, False),    # due east — orthogonal to the favourable trend
        (350.0, True),    # unfolded input must fold before testing
        (None, False),    # unknown azimuth is not favourable
    ],
)
def test_favourable_trend_band_is_two_intervals(azimuth, expected):
    """345-030 deg folded into [0,180) is `az <= 30 or az >= 165`.

    Testing only `az <= 30` would silently discard every NNW-trending fault,
    which is most of the Republic graben — the structures the OF01-501 study
    identified in the first place.
    """
    assert in_favourable_trend(azimuth) is expected


# --- novelty ----------------------------------------------------------------


@pytest.mark.parametrize(
    "km,expected",
    [
        (0.0, "confirms"),
        (0.5, "confirms"),
        (0.51, "extends"),
        (2.0, "extends"),
        (2.01, "lead"),
        (9.0, "lead"),
    ],
)
def test_novelty_thresholds(km, expected):
    assert local_store.novelty_for(km) == expected


def test_novelty_of_unknown_is_none_not_lead():
    """No occurrence data must not read as "nothing recorded nearby".

    This is the one that matters. `lead` is a prospecting signal; returning it
    when we simply have no records would convert a missing file into a finding.
    """
    assert local_store.novelty_for(None) is None


# --- occurrence aggregation -------------------------------------------------


def _cell_polygon(metric, lon, lat, half_deg=0.005):
    from shapely.geometry import box

    return metric.project(
        box(lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg)
    )


def _record(name, lon, lat, **kw):
    rec = {
        "name": name,
        "lon": lon,
        "lat": lat,
        "assays": False,
        "production": False,
        "accuracy_class": "topo",
        "commodity_primary": "Gold (Au)",
    }
    rec.update(kw)
    return rec


def test_occurrences_for_cell_counts_by_band_and_finds_nearest():
    metric = LocalMetric.for_point(*REPUBLIC)
    cell = _cell_polygon(metric, *REPUBLIC)

    records = [
        _record("Inside", REPUBLIC[0], REPUBLIC[1]),
        _record("Near", REPUBLIC[0] + 0.012, REPUBLIC[1]),      # ~0.9 km out
        _record("Far", REPUBLIC[0] + 0.30, REPUBLIC[1]),        # ~22 km — excluded
    ]
    layer = occ_mod.PointLayer.build(records, metric)
    out = occ_mod.occurrences_for_cell(layer, cell, radius_km=5.0, max_records=6)

    assert out["n_in_cell"] == 1
    assert out["n_5km"] == 2, "the 22 km record must be outside the radius"
    assert out["nearest_km"] == 0.0, "a record inside the cell is zero km away"
    assert out["nearest"]["name"] == "Inside"


def test_distance_is_measured_from_the_polygon_not_its_centre():
    """A record just inside a corner is 0 km away, not half a cell away."""
    metric = LocalMetric.for_point(*REPUBLIC)
    cell = _cell_polygon(metric, *REPUBLIC, half_deg=0.01)
    corner = _record("Corner", REPUBLIC[0] + 0.0099, REPUBLIC[1] + 0.0099)

    layer = occ_mod.PointLayer.build([corner], metric)
    out = occ_mod.occurrences_for_cell(layer, cell, radius_km=5.0, max_records=6)

    assert out["nearest_km"] == 0.0
    assert out["n_in_cell"] == 1


def test_best_record_prefers_evidence_over_proximity():
    """A producing mine 3 km out beats an unassayed showing 400 m away.

    Distance is not the same thing as information. The `records` list is what
    reaches the prompt and it is capped, so this ordering decides which evidence
    survives truncation.
    """
    metric = LocalMetric.for_point(*REPUBLIC)
    cell = _cell_polygon(metric, *REPUBLIC)
    records = [
        _record("Showing", REPUBLIC[0] + 0.006, REPUBLIC[1]),
        _record(
            "Producer",
            REPUBLIC[0] + 0.045,
            REPUBLIC[1],
            production=True,
            assays=True,
            accuracy_class="survey",
        ),
    ]
    layer = occ_mod.PointLayer.build(records, metric)
    out = occ_mod.occurrences_for_cell(layer, cell, radius_km=5.0, max_records=6)

    assert out["nearest"]["name"] == "Showing"
    assert out["best"]["name"] == "Producer"
    assert out["records"][0]["name"] == "Producer"
    assert out["with_production_5km"] == 1
    assert out["with_assays_5km"] == 1


def test_untrustworthy_position_classes_are_named_and_ranked_last():
    """A district centroid must sort below every real position.

    24 of 1,467 WA DNR gold/silver records are mining district centroids and 917
    are "coordinate accuracy highly variable". Letting one of those outrank a
    survey-grade record would put fictitious precision at the top of a prompt.
    """
    assert occ_mod.ACCURACY_RANK["survey"] < occ_mod.ACCURACY_RANK["topo"]
    assert occ_mod.ACCURACY_RANK["variable"] < occ_mod.ACCURACY_RANK["district_centroid"]
    assert occ_mod.ACCURACY_RANK["district_centroid"] < occ_mod.ACCURACY_RANK["unknown"]
    assert "district_centroid" in occ_mod.UNTRUSTWORTHY_POSITION


def test_occurrences_for_cell_reports_absence_rather_than_omitting_it():
    """"Nothing within 5 km" is an answer and must be expressible."""
    metric = LocalMetric.for_point(*REPUBLIC)
    cell = _cell_polygon(metric, *REPUBLIC)
    layer = occ_mod.PointLayer.build(
        [_record("Far", REPUBLIC[0] + 0.5, REPUBLIC[1])], metric
    )
    out = occ_mod.occurrences_for_cell(layer, cell, radius_km=5.0, max_records=6)

    assert out["nearest_km"] is None
    assert out["n_1km"] == 0
    assert "nearest" not in out


def test_empty_layer_yields_empty_facts_not_a_crash():
    metric = LocalMetric.for_point(*REPUBLIC)
    cell = _cell_polygon(metric, *REPUBLIC)
    layer = occ_mod.PointLayer.build([], metric)
    assert occ_mod.occurrences_for_cell(layer, cell, 5.0, 6) == {}
    assert occ_mod.iaml_for_cell(layer, cell, 5.0, 3) == []


# --- OF-00-495 quadtree decomposition ---------------------------------------


def test_wofe_lookup_decomposes_a_coarse_cell_into_250m_children():
    """A 1000 m cell is exactly sixteen 250 m cells, and they nest.

    The derived table is keyed at 250 m, a rung of RESOLUTION_LADDER. If the
    decomposition were off by one the lookup would attach a neighbouring cell's
    geology, which is invisible in the output and wrong everywhere.
    """
    from app.scoring.grid import (
        cell_id_for_point,
        make_cell_id,
        parent_cell_id,
        parse_cell_id,
    )

    coarse = cell_id_for_point(*REPUBLIC, 1000)
    res, col, row = parse_cell_id(coarse)
    k = res // wofe_grid.WOFE_CELL_RESOLUTION_M
    assert k == 4

    children = [
        make_cell_id(col * k + dc, row * k + dr, wofe_grid.WOFE_CELL_RESOLUTION_M)
        for dc in range(k)
        for dr in range(k)
    ]
    assert len(children) == 16
    assert len(set(children)) == 16
    assert all(parent_cell_id(c, 1000) == coarse for c in children)


def test_published_contrasts_match_of01_501():
    """The six favourable units and their contrasts, exactly as published.

    Boleneus et al. 2001, 50 epithermal training sites. These are measurements,
    not tunables — a typo here silently rescales every NE Washington score.
    """
    expected = {
        "Eck": 4.55,
        "Evkct": 3.62,
        "Evst": 3.42,
        "Evsf": 3.21,
        "Evkf": 2.56,
        "Eco": 1.96,
    }
    assert {k: v["contrast"] for k, v in wofe_grid.WOFE_CONTRASTS.items()} == expected
    # 30 of the 50 training sites sit on Sanpoil flows — the dominant host — while
    # Eck has the highest contrast because it concentrates 4 sites into 26 km².
    # "Most important" and "most predictive per unit area" are different claims.
    assert wofe_grid.WOFE_CONTRASTS["Evsf"]["training_sites"] == 30
    assert max(wofe_grid.WOFE_CONTRASTS, key=lambda u: wofe_grid.WOFE_CONTRASTS[u]["contrast"]) == "Eck"


def test_of00495_structure_codes_match_appendix_b():
    """Fault and fold codes as printed in Appendices B-1/B-2 of the report.

    The `.e00` value-attribute tables carry only VALUE and COUNT with empty label
    columns, so these codes look uninterpretable if the raster is all you read.
    They are not, and the distinction decides scores: the OF01-501 predictor is
    specifically a *normal* fault. Getting 7 and 43 the wrong way round would tell
    the structure agent that Mesozoic contraction is the Eocene ore control.
    """
    assert wofe_grid.describe_fault(7) == "thrust fault"
    assert wofe_grid.describe_fault(31) == "low-angle normal fault"
    assert wofe_grid.describe_fault(43) == "normal fault"
    assert wofe_grid.describe_fault(1) == "fault, unknown offset"
    assert wofe_grid.describe_fold(1) == "anticline"
    assert wofe_grid.describe_fold(13) == "syncline"
    assert wofe_grid.describe_fold(31) == "monocline, anticlinal bend"

    # An unknown code must be None, not a guess — a plausible label on a code we
    # cannot resolve is worse than no label.
    assert wofe_grid.describe_fault(999) is None
    assert wofe_grid.describe_fault(None) is None

    # Only true normal faults are the predictor class. Low-angle normal (31/33) is
    # a core-complex detachment: same extension, different plumbing.
    assert wofe_grid.PREDICTOR_FAULT_CODES == {43, 44, 45}
    assert 31 not in wofe_grid.PREDICTOR_FAULT_CODES
    assert 7 not in wofe_grid.PREDICTOR_FAULT_CODES

    # Every code in the VATs the build actually produced must be describable.
    # Measured 2026-08-12 from newafaul.e00 / newafold.e00.
    for code in (0, 1, 2, 3, 4, 7, 8, 9, 10, 31, 33, 43, 44, 45):
        assert wofe_grid.describe_fault(code), f"fault code {code} undescribed"
    for code in (1, 2, 3, 7, 8, 9, 13, 15, 19, 20, 21, 31, 32, 33):
        assert wofe_grid.describe_fold(code), f"fold code {code} undescribed"


def test_structure_agent_names_fault_types_and_flags_the_predictor_class():
    """The prompt must say "normal fault", not "code 43", and admit no azimuth."""
    from app.agents.structure_agent import _render

    line = _render({"wofe": {"fault_codes": [7, 43], "fault_types": ["thrust fault", "normal fault"], "has_predictor_fault": True}})
    assert "thrust fault" in line and "normal fault" in line
    assert "NORMAL-fault predictor class" in line
    # Presence rasters carry no orientation, and the OF01-501 trend rule needs it.
    # Silence here would let the model assume the favourable case.
    assert "no azimuth available" in line

    thrust_only = _render({"wofe": {"fault_codes": [7], "fault_types": ["thrust fault"], "has_predictor_fault": False}})
    assert "predictor class" not in thrust_only


def test_contrast_for_unlisted_unit_is_none_not_zero():
    """A non-predictive unit is a finding, not a missing value.

    The WofE study tested 150+ units and found zero training sites on all but
    six; 92% of NE Washington is non-permissive. Returning 0.0 would let a caller
    average that into a score as though it were a weak positive.
    """
    assert wofe_grid.contrast_for("Evsf") == 3.21
    assert wofe_grid.contrast_for("Kigd") is None
    assert wofe_grid.contrast_for(None) is None
    assert wofe_grid.contrast_for("") is None


def test_absent_stores_report_unavailable_and_return_empty(tmp_path):
    """A missing derived artifact is the fresh-clone default, not an error."""
    from app.spatial.geology import GeologyStore

    missing = tmp_path / "nope.sqlite"
    geo = GeologyStore(path=missing)
    assert geo.available is False
    assert geo.unit_descriptions() == {}
    window = geo.window((-119.0, 48.0, -118.0, 49.0), LocalMetric.for_point(*REPUBLIC))
    assert window.has_units is False and window.has_structures is False

    store = wofe_grid.WofeGridStore(path=missing)
    assert store.available is False
    assert store.facts_for_cells(["wa5070-250m-000001-000001"]) == {}


# --- prompt block -----------------------------------------------------------


def test_cell_facts_block_labels_cells_and_marks_the_empty_ones():
    """Cells with nothing must say "no data", not be omitted.

    An absent line reads as an oversight, and a model filling a perceived gap
    with a plausible guess is the behaviour this whole change set exists to stop.
    """
    from app.agents.base_agent import cell_facts_block

    cells = [{"cell_id": "a"}, {"cell_id": "b"}, {"cell_id": "c"}]
    facts = {"a": {"x": 1}, "c": {"x": 3}}
    block = cell_facts_block(
        cells, facts, lambda f: f"value {f['x']}", header="## Header"
    )

    assert "## Header" in block
    assert "- c1: value 1" in block
    assert "- c2: no data" in block
    assert "- c3: value 3" in block
    # Labels are batch-local, never canonical ids — a 26-character id retyped
    # fifty times costs output tokens and invites digit errors.
    assert "cell_id" not in block and "wa5070" not in block


def test_cell_facts_block_is_empty_when_no_cell_has_data():
    """An agent without its data source keeps its original prompt shape."""
    from app.agents.base_agent import cell_facts_block

    cells = [{"cell_id": "a"}, {"cell_id": "b"}]
    assert cell_facts_block(cells, {}, lambda f: "x", header="## H") == ""
    # Facts present but this domain has nothing to say about any of them.
    assert cell_facts_block(cells, {"a": {"other": 1}}, lambda f: None, "## H") == ""


# --- cache key correctness --------------------------------------------------


def test_cache_key_includes_per_cell_facts():
    """Two cells with different evidence must not share a cache key.

    The bug this prevents: `cell_facts` is a dict, and the previous
    `_cell_context` kept only list and str values — so per-cell geology and
    occurrence records were invisible to the key. Every cell would have kept
    serving the score it got before it had any evidence at all, which produces
    plausible numbers and no error.
    """
    from app.agents.lithology_agent import LithologyAgent

    agent = LithologyAgent.__new__(LithologyAgent)
    ctx = {
        "cell_facts": {
            "cell_a": {"geology": [{"unit": "Evsf", "frac": 1.0}]},
            "cell_b": {"geology": [{"unit": "Kigd", "frac": 1.0}]},
        },
        "known_deposits": [],
        "grid_cells": [],
    }

    a = agent._cell_context("cell_a", ctx)
    b = agent._cell_context("cell_b", ctx)
    assert a != b
    assert a["cell"]["geology"][0]["unit"] == "Evsf"

    keys = agent._cache_keys(
        [{"cell_id": "cell_a"}, {"cell_id": "cell_b"}], "gold", None, ctx
    )
    assert keys["cell_a"] != keys["cell_b"]


def test_cache_key_changes_when_a_cells_evidence_changes():
    """Building the occurrence extract must invalidate previously-cached cells."""
    from app.agents.historical_agent import HistoricalAgent

    agent = HistoricalAgent.__new__(HistoricalAgent)
    cells = [{"cell_id": "cell_a"}]
    before = {"cell_facts": {}, "known_deposits": [], "grid_cells": []}
    after = {
        "cell_facts": {"cell_a": {"occurrences": {"nearest_km": 0.4}}},
        "known_deposits": [],
        "grid_cells": [],
    }

    k_before = agent._cache_keys(cells, "gold", None, before)["cell_a"]
    k_after = agent._cache_keys(cells, "gold", None, after)["cell_a"]
    assert k_before != k_after


# --- role enforcement -------------------------------------------------------


def test_truth_pins_never_reach_the_prompt_path(tmp_path, monkeypatch):
    """A `role: "truth"` pin is benchmark ground truth and must stay invisible.

    If the model is told "someone marked this spot" and the benchmark then asks
    "did the model rank that spot highly", the answer is yes by construction and
    measures nothing ("steps for raghav 2.0" §30).
    """
    from app.spatial import user_sites

    def pin(name, role):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [REPUBLIC[0], REPUBLIC[1]]},
            "properties": {
                "pin_id": name,
                "name": name,
                "role": role,
                "provenance": "field_visit",
                "visited": True,
                "observed": "adit",
                "position_confidence": "gps",
            },
        }

    (tmp_path / "pins.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    pin("truth-pin", "truth"),
                    pin("evidence-pin", "evidence"),
                    pin("display-pin", "display"),
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(user_sites, "USER_SITES_DIR", tmp_path)

    # The default must be the safe answer: a caller that forgets to filter gets
    # evidence pins only.
    names = {
        (p.get("properties") or p).get("name") for p in user_sites.load_user_sites()
    }
    assert names == {"evidence-pin"}

    pins, counts = local_store._user_pins(
        pad_bbox((REPUBLIC[0], REPUBLIC[1], REPUBLIC[0], REPUBLIC[1]), 5.0)
    )
    assert [p["name"] for p in pins] == ["evidence-pin"]
    assert counts.get("truth") == 1, "the census still sees it; the prompt does not"


def test_local_context_disabled_returns_empty_but_well_formed(monkeypatch):
    """The escape hatch for measuring what the data actually adds."""
    from app.config import settings

    monkeypatch.setattr(settings, "local_context_enabled", False)
    ctx = local_store.build_local_context({}, [{"cell_id": "x", "bbox": [-119, 48, -118, 49]}])

    assert ctx["context_sources"] == []
    assert ctx["cell_facts"] == {}
    for key in ("geology_units", "fault_traces", "known_deposits", "historic_mines"):
        assert ctx[key] == []


def test_build_local_context_survives_a_cell_with_no_geometry():
    """Malformed input degrades; it does not take down a run."""
    ctx = local_store.build_local_context(
        {}, [{"cell_id": "no-geom", "bbox": [-119.0, 48.0, -118.9, 48.1]}], "gold"
    )
    assert "cell_facts" in ctx and "coverage" in ctx
