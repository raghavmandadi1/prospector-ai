"""
Toponym matcher tests (Workstream C, §25 acceptance criteria).

The matcher runs against the real WA GNIS extract when it is present, because
the failure modes that matter here — false friends and mouth-located streams —
only show up against real names.

Run:  .venv/bin/python -m pytest backend/tests/test_toponyms.py -q
"""
import pytest

from app.toponyms.matcher import (
    GNIS_PATH,
    Lexicon,
    ToponymName,
    load_gnis,
    load_lexicon,
    match_names,
    score_cap_for,
    toponyms_for_cells,
)

pytestmark = pytest.mark.skipif(
    not GNIS_PATH.exists(),
    reason="GNIS extract absent — run scripts/build_gnis_extract.py",
)


@pytest.fixture(scope="module")
def lex() -> Lexicon:
    lexicon = load_lexicon()
    assert lexicon is not None, "gold_wa.yaml must exist"
    return lexicon


@pytest.fixture(scope="module")
def names():
    return load_gnis()


def square_aoi(lon, lat, half=0.045):
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - half, lat - half],
                    [lon + half, lat - half],
                    [lon + half, lat + half],
                    [lon - half, lat + half],
                    [lon - half, lat - half],
                ]
            ],
        },
    }


# --- lexicon behaviour ----------------------------------------------------


def test_lexicon_loads_with_a_version(lex):
    """The version is hashed into run provenance — it must exist."""
    assert lex.version
    assert set(lex.tiers) >= {1, 2, 3, 4, 5}


def test_anti_signal_beats_positive_terms(lex):
    """A name matching both lists must be suppressed, not scored."""
    anti = lex.anti_signal_tier
    # "Gold Bar" contains "Gold" (tier 2) but is a railroad townsite
    assert lex.classify("Gold Bar", "Populated Place")[0] == anti
    assert lex.classify("Goldmyer Hot Springs", "Spring")[0] == anti
    assert lex.classify("Mill Creek", "Stream")[0] == anti


def test_measured_false_friend_families_are_suppressed(lex):
    """Ship names, railroad tunnels and scenic viewpoints.

    Each accounted for a large share of the strongest tier before they were
    added; see the notes in gold_wa.yaml.
    """
    anti = lex.anti_signal_tier
    for name, cls in [
        ("Discovery Bay", "Bay"),          # HMS Discovery, 1792
        ("Port Discovery", "Populated Place"),
        ("Tunnel Creek", "Stream"),        # railroad tunnel country
        ("Prospect Point", "Cape"),        # a prospect is also a view
    ]:
        assert lex.classify(name, cls)[0] == anti, name


def test_general_terms_still_score_outside_the_named_false_friends(lex):
    """Suppression is by full WA name, not by killing the word.

    A future "Tunnel Gulch" must still score even though "Tunnel Creek" does not.
    """
    assert lex.classify("Tunnel Gulch", "Valley")[0] == 1
    assert lex.classify("Discovery Claim", "Mine")[0] == 1


def test_word_boundaries_are_respected(lex):
    """"Gold" must not match inside "Marigold"."""
    assert lex.classify("Marigold Meadow", "Flat") is None
    assert lex.classify("Gold Basin", "Basin")[0] == 2


def test_tier_4_only_applies_to_landforms(lex):
    """"Red Mountain" records a possible gossan; "Red Barn Road" does not."""
    assert lex.classify("Red Mountain", "Summit")[0] == 4
    assert lex.classify("Red Lake", "Lake") is None


def test_excluded_classes_are_ignored(lex):
    assert lex.classify("Golden Valley", "Census") is None


# --- stream attribution (§25) ---------------------------------------------


def test_streams_carry_a_source_coordinate(names):
    """GNIS locates a stream at its MOUTH. Without the headwaters coordinate a
    stream name is attributed kilometres from whatever it was named for."""
    streams = [n for n in names if n.feature_class == "Stream"]
    assert streams
    with_source = [n for n in streams if n.source_lat is not None]
    assert len(with_source) / len(streams) > 0.95


def test_a_long_stream_is_sampled_along_its_length(names):
    long_streams = [
        n
        for n in names
        if n.feature_class == "Stream"
        and n.source_lat is not None
        and abs(n.source_lat - n.lat) + abs(n.source_lon - n.lon) > 0.1
    ]
    assert long_streams
    n = long_streams[0]
    seg = n.segment()
    assert len(seg) > 5, "a multi-km creek must produce multiple samples"
    assert seg[0] == (n.lon, n.lat)
    assert seg[-1] == pytest.approx((n.source_lon, n.source_lat), abs=1e-9)


def test_point_features_produce_exactly_one_sample():
    n = ToponymName("1", "Bullion Basin", "Basin", 47.4, -121.4)
    assert n.segment() == [(-121.4, 47.4)]


def test_stream_is_attributed_beyond_the_cell_containing_its_mouth(names, lex):
    """The §25 criterion, stated directly."""
    from app.toponyms.matcher import _km_between

    target = None
    for n in names:
        if n.feature_class != "Stream" or n.source_lat is None:
            continue
        if lex.classify(n.name, n.feature_class) is None:
            continue
        if _km_between(n.lon, n.lat, n.source_lon, n.source_lat) > 6:
            target = n
            break
    assert target, "expected at least one long, lexicon-matching creek in WA"

    # A cell near the headwaters, far from the mouth, must still see the name.
    cell = {
        "cell_id": "test",
        "bbox": [
            target.source_lon - 0.005,
            target.source_lat - 0.005,
            target.source_lon + 0.005,
            target.source_lat + 0.005,
        ],
    }
    result = toponyms_for_cells([cell], [target], lex)
    assert "test" in result
    assert result["test"]["hits"][0].name == target.name


# --- per-cell aggregation -------------------------------------------------


def test_monte_cristo_surfaces_its_district_toponyms(names, lex):
    from app.scoring.grid import generate_grid

    cells = [c.model_dump() for c in generate_grid(square_aoi(-121.44, 48.03), 2000)]
    result = toponyms_for_cells(cells, names, lex)
    found = {h.name for d in result.values() for h in d["hits"]}
    assert "Monte Cristo" in found


def test_density_is_reported_so_access_bias_is_visible(names, lex):
    from app.scoring.grid import generate_grid

    cells = [c.model_dump() for c in generate_grid(square_aoi(-121.44, 48.03), 2000)]
    result = toponyms_for_cells(cells, names, lex)
    assert result
    for d in result.values():
        assert d["named_features_nearby"] >= len(d["hits"])
        assert 0.0 <= d["hit_density"] <= 1.0
        assert d["lexicon_version"] == lex.version


def test_suppressed_names_are_reported_not_silently_dropped(lex):
    """The anti-signal log is how the list gets evaluated and grown."""
    goldmyer = ToponymName("1", "Goldmyer Hot Springs", "Spring", 47.54, -121.39)
    cell = {"cell_id": "c", "bbox": [-121.40, 47.53, -121.38, 47.55]}
    result = toponyms_for_cells([cell], [goldmyer], lex)
    assert result["c"]["hits"] == []
    assert any("Goldmyer" in s for s in result["c"]["suppressed"])


# --- corroboration and caps (§21.1, §22) ----------------------------------


def test_corroboration_marks_names_near_a_recorded_occurrence(lex):
    name = ToponymName("1", "Bonanza Creek", "Stream", 48.00, -121.40)
    cell = {"cell_id": "c", "bbox": [-121.41, 47.99, -121.39, 48.01]}

    near = toponyms_for_cells(
        [cell], [name], lex, occurrences=[{"lon": -121.401, "lat": 48.001, "name": "X Mine"}]
    )
    assert near["c"]["hits"][0].corroboration == "corroborated"

    far = toponyms_for_cells(
        [cell], [name], lex, occurrences=[{"lon": -121.6, "lat": 48.3, "name": "Y Mine"}]
    )
    assert far["c"]["hits"][0].corroboration == "uncorroborated"


def test_uncorroborated_toponym_can_never_reach_the_high_tier(lex):
    """§22: a suggestive name alone may not promote a cell."""
    name = ToponymName("1", "Bonanza Creek", "Stream", 48.00, -121.40)
    cell = {"cell_id": "c", "bbox": [-121.41, 47.99, -121.39, 48.01]}
    hits = toponyms_for_cells(
        [cell], [name], lex, occurrences=[{"lon": -121.9, "lat": 48.5, "name": "far"}]
    )["c"]["hits"]
    cap = score_cap_for(hits, lex)
    assert 0 < cap <= 0.45


def test_corroborated_toponym_adds_no_score(lex):
    """The occurrence already scored the cell; the name is for legibility."""
    name = ToponymName("1", "Bonanza Creek", "Stream", 48.00, -121.40)
    cell = {"cell_id": "c", "bbox": [-121.41, 47.99, -121.39, 48.01]}
    hits = toponyms_for_cells(
        [cell], [name], lex, occurrences=[{"lon": -121.4005, "lat": 48.0005, "name": "X"}]
    )["c"]["hits"]
    assert score_cap_for(hits, lex) == 0.0


def test_weak_tiers_are_capped_lower_than_strong_ones(lex):
    strong = ToponymName("1", "Bonanza Creek", "Stream", 48.0, -121.4)
    weak = ToponymName("2", "Red Mountain", "Summit", 48.0, -121.4)
    cell = {"cell_id": "c", "bbox": [-121.41, 47.99, -121.39, 48.01]}
    far = [{"lon": -122.5, "lat": 48.9, "name": "far"}]

    s = score_cap_for(toponyms_for_cells([cell], [strong], lex, far)["c"]["hits"], lex)
    w = score_cap_for(toponyms_for_cells([cell], [weak], lex, far)["c"]["hits"], lex)
    assert w < s


def test_evidence_string_names_the_name_and_its_status(lex):
    name = ToponymName("1", "Bonanza Creek", "Stream", 48.0, -121.4)
    cell = {"cell_id": "c", "bbox": [-121.41, 47.99, -121.39, 48.01]}
    hit = toponyms_for_cells(
        [cell], [name], lex, occurrences=[{"lon": -122.5, "lat": 48.9, "name": "far"}]
    )["c"]["hits"][0]
    s = hit.evidence_string()
    assert "Bonanza Creek" in s
    assert "Tier 2" in s
    assert "uncorroborated" in s.lower()


def test_matching_is_deterministic(names, lex):
    """The benchmark requires run-to-run reproducibility."""
    a = [(n.feature_id, t, term) for n, t, term in match_names(names, lex)]
    b = [(n.feature_id, t, term) for n, t, term in match_names(names, lex)]
    assert a == b
