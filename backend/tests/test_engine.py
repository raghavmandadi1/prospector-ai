"""
Unit tests for the scoring maths — `app.scoring.engine`.

CLAUDE.md Known Gap #3 named this the highest-leverage unclaimed test work in
the repo: `_weighted_mean` and `normalize_relative` decide every number the map
shades and every number the benchmark diffs, and until now neither had a direct
test. The all-zero-scores bug in `.claude/mistakes-log.md` lived for weeks
because "it ran without an exception" was the bar, and its output shape was
indistinguishable from a legitimate all-low result. These tests exist to make
that class of bug loud: they pin the exact arithmetic, including the two
*different* denominators inside `_weighted_mean`, the midpoint tie convention in
`normalize_relative`, and the "uniform AOI invents no hotspots" behaviour.

Everything here is hand-built ScoredCell / AgentResult objects and a fake grid
cell. No LLM, no network, no real grid — the maths does not need one, and a test
that needs a 1 km grid over Monte Cristo to check a division is a test nobody
will run.

Where a test asserts behaviour I believe is *wrong*, it says so in a comment
marked BUG / QUIRK and asserts what the code does today. Changing engine.py to
match the comment should make that test fail — that is the point, and it is how
the two hand-run smoke scripts in this directory already work.

Run:  .venv/bin/python -m pytest backend/tests/test_engine.py -q
"""
import math

import pytest
from pydantic import ValidationError

from app.models.agent_result import AgentResult, ScoredCell
from app.scoring.engine import (
    PCT_HIGH,
    PCT_LOW,
    PCT_MEDIUM,
    _tier_from_percentile,
    _weighted_mean,
    normalize_relative,
    synthesize,
)

# A syntactically valid GeoJSON polygon. Nothing in the engine looks inside it;
# it exists because ScoredCell.geometry is required.
SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-121.5, 48.0], [-121.4, 48.0], [-121.4, 48.1], [-121.5, 48.1], [-121.5, 48.0]]],
}
CLIPPED = {
    "type": "Polygon",
    "coordinates": [[[-121.5, 48.0], [-121.45, 48.0], [-121.45, 48.05], [-121.5, 48.05], [-121.5, 48.0]]],
}

# Real-shaped cell ids, so a failure message looks like production data.
CELL_A = "wa5070-1000m-000349-000380"
CELL_B = "wa5070-1000m-000350-000380"
CELL_C = "wa5070-1000m-000351-000380"


def sc(score, confidence=1.0, cell_id=CELL_A, evidence=None, sources=None) -> ScoredCell:
    """One agent's opinion about one cell."""
    return ScoredCell(
        cell_id=cell_id,
        geometry=SQUARE,
        score=score,
        confidence=confidence,
        evidence=evidence or [],
        data_sources_used=sources or [],
    )


def result(agent_id, cells, status="completed") -> AgentResult:
    return AgentResult(agent_id=agent_id, status=status, scored_cells=cells)


_MISSING = object()


class FakeGridCell:
    """Minimal stand-in for `scoring.grid.GridCell`.

    `synthesize()` reads exactly three things off a grid cell: `.cell_id`,
    `.geometry`, and `.display_geometry` — the last through
    `getattr(cell, "display_geometry", None)`, so both "attribute absent" and
    "attribute is None" are live code paths and both are tested below. Passing
    nothing for `display_geometry` leaves the attribute genuinely unset rather
    than set to None.
    """

    def __init__(self, cell_id, geometry=SQUARE, display_geometry=_MISSING):
        self.cell_id = cell_id
        self.geometry = geometry
        if display_geometry is not _MISSING:
            self.display_geometry = display_geometry


# ===========================================================================
# _weighted_mean
# ===========================================================================


def test_no_agent_scores_returns_zero_zero():
    """The "no agent touched this cell" case must not divide by anything."""
    assert _weighted_mean({}, {"lithology": 0.25}) == (0.0, 0.0)


def test_missing_weight_defaults_that_agent_to_one():
    """`weights.get(agent_id, 1.0)` — and an empty dict is the LIVE path.

    orchestrator.py:245 falls back to `DEFAULT_WEIGHTS.get(target_mineral, {})`,
    so every mineral without a preset arrives here as `{}` and gets equal
    weighting. (`EQUAL_WEIGHTS` in weights.py is never referenced by anything —
    same outcome, different code path. Do not "fix" this by wiring it in
    without also changing the call site.)
    """
    scores = {"structure": sc(0.8), "historical": sc(0.2)}

    # No weights at all → plain mean of the two scores.
    assert _weighted_mean(scores, {}) == (0.5, 1.0)
    # A partial dict must behave identically to spelling out the 1.0s.
    assert _weighted_mean(scores, {"structure": 1.0}) == _weighted_mean(
        scores, {"structure": 1.0, "historical": 1.0}
    )
    # And the default really is 1.0, not 0.0: weighting one agent at 3.0 must
    # pull the composite, which it cannot do if the other agent weighs nothing.
    assert _weighted_mean(scores, {"structure": 3.0})[0] == pytest.approx(0.65)


def test_confidence_zero_agent_contributes_nothing_to_the_score():
    """A confidence-0 cell is an LLM *miss*, not a score of zero.

    This is the fix from the all-zero-scores bug (mistakes-log 2026-07-07): the
    fill-in placeholders BaseAgent.run() emits carry confidence=0 so the engine
    ignores them instead of dragging every composite to zero. If this test
    fails, that whole bug is back.
    """
    scored = sc(0.9, confidence=1.0)
    placeholder = sc(0.0, confidence=0.0)  # "Cell not scored by LLM"

    composite, mean_conf = _weighted_mean({"a": scored, "b": placeholder}, {})
    assert composite == 0.9, "the placeholder must not average the score down"
    # ...but it *is* counted in the confidence denominator, which is the point of
    # reporting confidence at all: half the panel abstained.
    assert mean_conf == 0.5


def test_all_confidence_zero_returns_zero_without_dividing_by_zero():
    """Every agent missed this cell: (0.0, 0.0), not ZeroDivisionError."""
    agents = {"a": sc(0.9, confidence=0.0), "b": sc(0.4, confidence=0.0)}
    assert _weighted_mean(agents, {"a": 0.3, "b": 0.2}) == (0.0, 0.0)


def test_score_and_confidence_have_different_denominators():
    """Hand-computed. The two returned numbers are normalised differently.

    score        = Σ(w·conf·score) / Σ(w·conf)     ← confidence-weighted weight
    confidence   = Σ(w·conf)       / Σ(w)          ← RAW weight

    A refactor that "tidies up" by sharing one denominator would silently turn
    mean_confidence into a constant 1.0 and destroy the only signal that says
    how much of the panel actually answered. This case is built so the two
    denominators are different numbers (0.4 vs 0.5) and both results are exact
    in binary floating point.

        structure  w=0.3  score=0.8  conf=1.0   → eff 0.30, contrib 0.24
        historical w=0.2  score=0.4  conf=0.5   → eff 0.10, contrib 0.04
        Σ(w·conf) = 0.4   Σ(w) = 0.5   Σ(w·conf·score) = 0.28
        score      = 0.28 / 0.4 = 0.70
        confidence = 0.40 / 0.5 = 0.80
    """
    agents = {"structure": sc(0.8, 1.0), "historical": sc(0.4, 0.5)}
    weights = {"structure": 0.3, "historical": 0.2}

    assert _weighted_mean(agents, weights) == (0.7, 0.8)

    # Spelled out, so the failure message names which denominator drifted.
    conf_weighted_total = 0.3 * 1.0 + 0.2 * 0.5
    raw_total = 0.3 + 0.2
    assert conf_weighted_total != raw_total
    assert _weighted_mean(agents, weights)[0] == pytest.approx(0.28 / conf_weighted_total)
    assert _weighted_mean(agents, weights)[1] == pytest.approx(conf_weighted_total / raw_total)


def test_single_agent_at_full_confidence_returns_its_own_score():
    assert _weighted_mean({"lithology": sc(0.73, 1.0)}, {"lithology": 0.25}) == (0.73, 1.0)
    # ...and the weight cannot matter when it is the only agent.
    assert _weighted_mean({"lithology": sc(0.73, 1.0)}, {"lithology": 999.0}) == (0.73, 1.0)


def test_output_is_rounded_to_four_places():
    """The source rounds to 4 dp — it does NOT clamp. Both halves matter.

    Rounding is what makes run records diffable; the absence of clamping is
    fine only because a convex combination of scores already in [0,1] with
    non-negative weights cannot leave [0,1]. See
    test_mixed_sign_weights_escape_the_unit_interval for what happens when that
    precondition is broken.
    """
    composite, mean_conf = _weighted_mean(
        {"a": sc(1.0), "b": sc(0.0), "c": sc(0.0)}, {}
    )
    assert composite == 0.3333  # 1/3, rounded not truncated
    assert mean_conf == 1.0

    composite, mean_conf = _weighted_mean({"a": sc(0.5, 1.0), "b": sc(0.5, 1.0), "c": sc(0.5, 0.0)}, {})
    assert composite == 0.5
    assert mean_conf == 0.6667  # 2/3


@pytest.mark.parametrize(
    "confidences",
    [(1.0, 1.0), (0.0, 1.0), (0.5, 0.5), (0.01, 0.99), (1.0, 0.0), (0.3, 0.7)],
)
def test_mean_confidence_stays_within_zero_and_one(confidences):
    """Confidence is reported to the user as a percentage; >1 would be a lie."""
    c1, c2 = confidences
    agents = {"a": sc(0.6, c1), "b": sc(0.2, c2)}
    for weights in ({}, {"a": 0.3, "b": 0.2}, {"a": 0.01, "b": 5.0}):
        composite, mean_conf = _weighted_mean(agents, weights)
        assert 0.0 <= mean_conf <= 1.0, weights
        assert 0.0 <= composite <= 1.0, weights


def test_zero_total_weight_never_raises_or_returns_nan():
    """Several routes to a zero denominator, none may produce NaN/inf/raise."""
    # All weights explicitly zero (a user dragging every slider to the bottom).
    assert _weighted_mean({"a": sc(0.9), "b": sc(0.4)}, {"a": 0.0, "b": 0.0}) == (0.0, 0.0)
    # Equal-and-opposite weights that cancel to exactly 0.0 in binary float.
    assert _weighted_mean({"a": sc(1.0), "b": sc(0.0)}, {"a": -1.0, "b": 1.0}) == (0.0, 0.0)
    # A zero-weight agent alongside a real one: contributes nothing, no NaN.
    composite, mean_conf = _weighted_mean({"a": sc(0.8), "b": sc(0.1)}, {"a": 1.0, "b": 0.0})
    assert (composite, mean_conf) == (0.8, 1.0)

    for weights in ({"a": 0.0, "b": 0.0}, {"a": -1.0, "b": 1.0}, {"a": -0.5, "b": -0.5}):
        for value in _weighted_mean({"a": sc(1.0), "b": sc(0.0)}, weights):
            assert math.isfinite(value), weights


def test_mixed_sign_weights_escape_the_unit_interval():
    """BUG (pinned, not fixed — engine.py is not mine to edit).

    `weight_total == 0` is an exact float comparison. Weights that *nearly*
    cancel leave a denominator of ~5e-17 instead of 0.0, and the composite
    explodes: the case below returns about -5.4e15. `_weighted_mean` does not
    clamp, so `synthesize()` then hands that to `ScoredCell(score=...)`, whose
    `ge=0.0, le=1.0` constraint aborts the entire run — *after* every Anthropic
    token has been paid for (see the companion synthesize test).

    Reachable because `config` is an unvalidated `Optional[dict]` on
    `DevAnalysisRequest` (analysis_dev.py:43) and its `weights` go straight
    through orchestrator.py:245 into here. Correct behaviour, in my view:
    clamp the composite into [0,1] in `_weighted_mean`, and reject negative
    weights at the API boundary. Both live in files owned by others.
    """
    composite, mean_conf = _weighted_mean(
        {"a": sc(1.0), "b": sc(0.5), "c": sc(0.5)},
        {"a": 0.3, "b": -0.1, "c": -0.2},
    )
    assert math.isfinite(composite)
    assert abs(composite) > 1e6, "documenting the blow-up, not endorsing it"
    assert not (0.0 <= composite <= 1.0)
    assert mean_conf == 1.0


def test_weights_must_be_a_dict_not_none():
    """Records a precondition that has no guard behind it.

    orchestrator.py:245 is `config.get("weights", DEFAULT_WEIGHTS.get(...))`,
    which returns None — not the default — when a client posts an explicit
    `"weights": null`. That reaches `weights.get(...)` here and kills the run
    at synthesis. The one-word fix (`config.get("weights") or DEFAULT_...`) is
    in orchestrator.py, which this test file does not own. If the engine is made
    defensive instead, update this test.
    """
    with pytest.raises(AttributeError):
        _weighted_mean({"a": sc(0.5)}, None)


# ===========================================================================
# _tier_from_percentile — boundary values themselves, not points either side
# ===========================================================================


def test_tier_boundaries_are_inclusive():
    assert (PCT_HIGH, PCT_MEDIUM, PCT_LOW) == (0.90, 0.65, 0.35)

    assert _tier_from_percentile(0.90) == "high"
    assert _tier_from_percentile(0.65) == "medium"
    assert _tier_from_percentile(0.35) == "low"

    # Just below each boundary drops exactly one tier.
    assert _tier_from_percentile(0.8999) == "medium"
    assert _tier_from_percentile(0.6499) == "low"
    assert _tier_from_percentile(0.3499) == "negligible"

    # Ends of the range.
    assert _tier_from_percentile(1.0) == "high"
    assert _tier_from_percentile(0.0) == "negligible"


# ===========================================================================
# normalize_relative
# ===========================================================================


def cells(*scores):
    """One ScoredCell per score, with distinct ids."""
    return [
        ScoredCell(cell_id=f"wa5070-1000m-{i:06d}-000380", geometry=SQUARE, score=s, confidence=1.0)
        for i, s in enumerate(scores)
    ]


def test_empty_cell_list_is_returned_untouched():
    empty: list = []
    assert normalize_relative(empty) is empty


def test_annotates_in_place_and_returns_the_same_list():
    original = cells(0.1, 0.5, 0.9)
    out = normalize_relative(original)
    assert out is original
    assert all(c.relative_score is not None for c in out)
    assert all(c.tier is not None for c in out)


def test_relative_score_is_a_min_max_stretch():
    """Min cell → 0.0, max cell → 1.0, regardless of the absolute band.

    This is the whole point of AOI-relative shading: an AOI where every
    absolute composite is 0.11–0.14 must still show its best ground, not a flat
    grey rectangle.
    """
    low, mid, high = normalize_relative(cells(0.11, 0.125, 0.14))
    assert low.relative_score == 0.0
    assert high.relative_score == 1.0
    assert mid.relative_score == 0.5
    # Absolute scores are untouched — the UI toggle needs both views.
    assert (low.score, mid.score, high.score) == (0.11, 0.125, 0.14)


def test_percentile_uses_midpoint_tie_handling():
    """A tied block shares the midpoint of the ranks it spans.

        sorted = [0.1, 0.2, 0.2, 0.3],  n = 4
        0.1 → bisect_left 0, right 1 → (0+1)/2 / 4 = 0.125
        0.2 → bisect_left 1, right 3 → (1+3)/2 / 4 = 0.500   (both tied cells)
        0.3 → bisect_left 3, right 4 → (3+4)/2 / 4 = 0.875
    """
    a, b, c, d = normalize_relative(cells(0.1, 0.2, 0.2, 0.3))
    assert [x.percentile for x in (a, b, c, d)] == [0.125, 0.5, 0.5, 0.875]
    assert b.percentile == c.percentile, "tied cells must not be ranked against each other"
    assert [x.tier for x in (a, b, c, d)] == ["negligible", "low", "low", "medium"]


def test_uniform_aoi_invents_no_hotspots():
    """max == min: every cell 0.5 / "low". Explicitly documented behaviour.

    The shape of a past bug (mistakes-log 2026-07-07) was an entire AOI of
    identical composites; a min-max stretch over zero spread would have divided
    by zero, and any "spread them out anyway" fallback would have painted
    random hotspots onto noise. Flat mid shading is the honest answer.
    """
    flat = normalize_relative(cells(0.42, 0.42, 0.42, 0.42, 0.42))
    assert {c.relative_score for c in flat} == {0.5}
    assert {c.tier for c in flat} == {"low"}
    assert {c.percentile for c in flat} == {0.5}
    # Zero is also a uniform AOI — the all-agents-missed case must not crash.
    zeroed = normalize_relative(cells(0.0, 0.0, 0.0))
    assert {c.relative_score for c in zeroed} == {0.5}
    assert {c.tier for c in zeroed} == {"low"}


def test_spread_below_the_epsilon_is_treated_as_uniform():
    """The guard is `spread > 1e-9`, not `spread > 0`.

    QUIRK worth knowing: below the epsilon, `relative_score` and `tier` take
    the uniform branch but `percentile` is still computed from bisect, so the
    two cells below report percentiles 0.25 / 0.75 while both are tier "low".
    Inconsistent, but harmless and strictly safer than stretching numerical
    noise across the full colour ramp. Asserting it so a change is deliberate.
    """
    a, b = normalize_relative(cells(0.5, 0.5 + 1e-10))
    assert (a.relative_score, b.relative_score) == (0.5, 0.5)
    assert (a.tier, b.tier) == ("low", "low")
    assert (a.percentile, b.percentile) == (0.25, 0.75)


def test_single_cell_aoi_does_not_divide_by_zero():
    (only,) = normalize_relative(cells(0.7))
    assert only.relative_score == 0.5  # spread is 0 → uniform branch
    assert only.percentile == 0.5      # (0 + 1) / 2 / 1
    assert only.tier == "low"
    assert only.score == 0.7


def test_tier_boundaries_are_hit_exactly_through_normalize():
    """Ten distinct scores land percentiles exactly on 0.35 and 0.65.

    With n cells all distinct, percentiles are (2i+1)/2n — so n=10 gives
    0.05…0.95 in steps of 0.10 and lands *on* two of the three boundaries. That
    makes this the case to watch if the tie convention or the comparison
    operators ever change.
    """
    ranked = normalize_relative(cells(*[i / 10 for i in range(10)]))
    assert [c.percentile for c in ranked] == [
        0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95
    ]
    assert [c.tier for c in ranked] == [
        "negligible", "negligible", "negligible",  # 0.05 0.15 0.25
        "low", "low", "low",                       # 0.35 (boundary) 0.45 0.55
        "medium", "medium", "medium",              # 0.65 (boundary) 0.75 0.85
        "high",                                    # 0.95
    ]

    # Five distinct scores land the top cell exactly on PCT_HIGH = 0.90.
    five = normalize_relative(cells(0.0, 0.25, 0.5, 0.75, 1.0))
    assert [c.percentile for c in five] == [0.1, 0.3, 0.5, 0.7, 0.9]
    assert five[-1].tier == "high"


def test_top_cell_needs_five_cells_before_it_can_reach_high():
    """QUIRK (pinned): the top cell's percentile is (2n-1)/2n.

    That only reaches 0.90 at n >= 5, so an AOI of four cells has no "high
    priority" cell however strong its best ground is. Harmless in practice —
    the 25 km² AOI minimum means >= 25 cells at 1000 m — but it is the kind of
    off-by-a-convention that would be baffling in a hand-built test fixture,
    so it is written down rather than discovered.
    """
    four = normalize_relative(cells(0.1, 0.4, 0.7, 1.0))
    assert four[-1].percentile == 0.875
    assert "high" not in {c.tier for c in four}

    five = normalize_relative(cells(0.1, 0.3, 0.5, 0.7, 1.0))
    assert [c.tier for c in five].count("high") == 1


def test_a_large_tied_block_can_leave_the_aoi_with_no_high_tier():
    """QUIRK (pinned): nine cells tied at the top all get percentile 0.55.

    Midpoint tie handling is the right convention, but it means "top 10%" is a
    statement about *ranks*, not about scores: an AOI whose best ground is a
    broad plateau shows zero high-priority cells while a single cell peak shows
    one. Anyone reading the legend as "the best 10% of this polygon" should
    know that.
    """
    plateau = normalize_relative(cells(0.1, *[0.9] * 9))
    assert plateau[0].tier == "negligible"
    assert {c.percentile for c in plateau[1:]} == {0.55}
    assert "high" not in {c.tier for c in plateau}
    # The stretch still separates them, so the map is not misleading — only the
    # tier label collapses.
    assert plateau[0].relative_score == 0.0
    assert {c.relative_score for c in plateau[1:]} == {1.0}


def test_absolute_scores_are_never_mutated():
    values = [0.0, 0.137, 0.5, 0.86, 1.0]
    annotated = normalize_relative(cells(*values))
    assert [c.score for c in annotated] == values
    # Idempotent: running it twice must not drift the absolutes or the derived
    # fields (the benchmark re-normalizes stored runs).
    again = normalize_relative(annotated)
    assert [c.score for c in again] == values
    assert [c.relative_score for c in again] == [0.0, 0.137, 0.5, 0.86, 1.0]


# ===========================================================================
# synthesize
# ===========================================================================


def grid(*cell_ids):
    return [FakeGridCell(cid) for cid in cell_ids]


def test_failed_agent_is_excluded_entirely():
    """status != "completed" → the agent's cells and evidence are dropped.

    A failed agent that still voted would be worse than useless: its scores are
    whatever it managed before dying.
    """
    good = result("lithology", [sc(0.2, 1.0, CELL_A, evidence=["andesite"])])
    bad = result(
        "structure",
        [sc(1.0, 1.0, CELL_A, evidence=["should never be shown"], sources=["ghost"])],
        status="failed",
    )

    (cell,) = synthesize([good, bad], grid(CELL_A), {}, {})
    assert cell.score == 0.2, "the failed agent must not move the composite"
    assert cell.evidence == ["[lithology] andesite"]
    assert cell.data_sources_used == []


def test_skipped_agent_is_excluded_too():
    """"skipped" is a documented AgentResult.status; the filter is != completed."""
    skipped = result("proximity", [sc(1.0, 1.0, CELL_A)], status="skipped")
    (cell,) = synthesize([skipped], grid(CELL_A), {}, {})
    assert (cell.score, cell.confidence) == (0.0, 0.0)


def test_confidence_zero_cells_keep_their_place_in_the_grid():
    """Grid coverage is never lost — the cell appears, at 0.0/0.0.

    A missing cell would leave a hole in the choropleth that reads as "no data"
    in exactly the same way a scored-low cell reads as "poor ground". Every
    grid cell in, every grid cell out, in the same order.
    """
    missed = result(
        "geochemistry",
        [sc(0.0, 0.0, cid, evidence=["Cell not scored by LLM"]) for cid in (CELL_A, CELL_B)],
    )
    out = synthesize([missed], grid(CELL_A, CELL_B, CELL_C), {}, {})

    assert [c.cell_id for c in out] == [CELL_A, CELL_B, CELL_C]
    assert [c.score for c in out] == [0.0, 0.0, 0.0]
    assert [c.confidence for c in out] == [0.0, 0.0, 0.0]
    # The placeholder's own evidence still rides along, which is honest: the
    # drilldown says the LLM never scored it rather than showing nothing.
    assert out[0].evidence == ["[geochemistry] Cell not scored by LLM"]


def test_cell_with_no_agent_scores_at_all_still_appears_at_zero():
    scored = result("lithology", [sc(0.8, 1.0, CELL_A)])
    out = synthesize([scored], grid(CELL_A, CELL_B), {}, {})

    assert [c.cell_id for c in out] == [CELL_A, CELL_B]
    assert out[1].score == 0.0
    assert out[1].confidence == 0.0
    assert out[1].evidence == []


def test_scores_for_cells_outside_the_grid_are_ignored():
    """The grid, not the LLM, decides which cells exist.

    An id the model invented (or a stale cached row from a different AOI) must
    not conjure a polygon into the output — the engine has no geometry for it.
    """
    stray = result("structure", [sc(1.0, 1.0, "wa5070-1000m-999999-999999")])
    out = synthesize([stray], grid(CELL_A), {}, {})
    assert [c.cell_id for c in out] == [CELL_A]
    assert out[0].score == 0.0


def test_evidence_is_prefixed_with_the_agent_id():
    """The EvidenceDrawer relies on this prefix to attribute each line."""
    a = result("lithology", [sc(0.6, 1.0, CELL_A, evidence=["Swauk Formation arkose"])])
    b = result("historical", [sc(0.4, 1.0, CELL_A, evidence=["Blewett district, 2 km"])])

    (cell,) = synthesize([a, b], grid(CELL_A), {}, {})
    assert cell.evidence == [
        "[lithology] Swauk Formation arkose",
        "[historical] Blewett district, 2 km",
    ]


def test_evidence_is_capped_at_twenty_entries():
    """Cap is 20 — and it truncates in AGENT order, which loses whole agents.

    Six agents at five evidence strings each is 30; the cap keeps the first 20,
    so agents 5 and 6 contribute nothing to the drilldown at all. With the
    orchestrator's fan-out order that systematically silences the same agents
    every run. A fairer cap would take round-robin (~3 per agent). Not fixing
    it here — engine.py is not mine — but pinning the count so the change is
    visible when someone does.
    """
    agents = [
        result(f"agent{i}", [sc(0.5, 1.0, CELL_A, evidence=[f"a{i}-ev{j}" for j in range(5)])])
        for i in range(6)
    ]
    (cell,) = synthesize(agents, grid(CELL_A), {}, {})

    assert len(cell.evidence) == 20
    assert cell.evidence[0] == "[agent0] a0-ev0"
    assert all("agent4" not in e for e in cell.evidence)
    assert all("agent5" not in e for e in cell.evidence)


def test_display_geometry_is_preferred_over_the_canonical_square():
    """The map must never draw grid poking outside the AOI.

    Cells are *scored* on the full square (that is what gets cached and what the
    LLM reasons about) but *drawn* clipped, so `synthesize` copies
    `display_geometry` onto the output cell when there is one.
    """
    cell = FakeGridCell(CELL_A, geometry=SQUARE, display_geometry=CLIPPED)
    (out,) = synthesize([result("lithology", [sc(0.5, 1.0, CELL_A)])], [cell], {}, {})
    assert out.geometry == CLIPPED


def test_canonical_geometry_is_used_when_there_is_no_display_geometry():
    """Both fallback branches of `getattr(cell, "display_geometry", None)`.

    Interior cells clip to themselves, and any non-GridCell source (the cell
    cache, interpolated cells) may not carry the attribute at all.
    """
    absent = FakeGridCell(CELL_A)                                   # attribute unset
    explicit_none = FakeGridCell(CELL_B, display_geometry=None)     # attribute None
    empty_dict = FakeGridCell(CELL_C, display_geometry={})          # falsy dict

    out = synthesize(
        [result("lithology", [sc(0.5, 1.0, CELL_A)])],
        [absent, explicit_none, empty_dict],
        {},
        {},
    )
    assert [c.geometry for c in out] == [SQUARE, SQUARE, SQUARE]


def test_data_sources_used_is_deduplicated():
    """One source cited by three agents appears once in the cell's list.

    NOTE: the implementation is `list(set(...))`, so the *order* is not stable
    across processes (string hashing is salted per interpreter). Compare sorted.
    """
    shared = "WA_DNR_WGS_Mines_and_Minerals"
    agents = [
        result("lithology", [sc(0.5, 1.0, CELL_A, sources=[shared, "USGS_OF01_501"])]),
        result("historical", [sc(0.5, 1.0, CELL_A, sources=[shared, shared])]),
        result("structure", [sc(0.5, 1.0, CELL_A, sources=["USGS_OF01_501"])]),
    ]

    (cell,) = synthesize(agents, grid(CELL_A), {}, {})
    assert sorted(cell.data_sources_used) == ["USGS_OF01_501", shared]
    assert len(cell.data_sources_used) == len(set(cell.data_sources_used))


def test_weights_come_from_the_argument_not_from_config():
    """`config` is accepted and never read. Pinning that, because a future
    reader will assume the weights in `config` are the ones in force — the
    orchestrator pulls them out of config *before* calling, at :245."""
    agents = [
        result("a", [sc(1.0, 1.0, CELL_A)]),
        result("b", [sc(0.0, 1.0, CELL_A)]),
    ]
    hostile_config = {"weights": {"a": 1.0, "b": 0.0}}  # would give 1.0 if honoured

    (cell,) = synthesize(agents, grid(CELL_A), {"a": 1.0, "b": 1.0}, hostile_config)
    assert cell.score == 0.5


def test_weighting_flows_through_synthesize():
    """End-to-end on the maths above, with the real gold preset ratio.

    structure 0.30 at 0.9, lithology 0.25 at 0.4, both fully confident:
        (0.30·0.9 + 0.25·0.4) / (0.30 + 0.25) = 0.37 / 0.55 = 0.6727
    """
    agents = [
        result("structure", [sc(0.9, 1.0, CELL_A)]),
        result("lithology", [sc(0.4, 1.0, CELL_A)]),
    ]
    (cell,) = synthesize(agents, grid(CELL_A), {"structure": 0.30, "lithology": 0.25}, {})
    assert cell.score == 0.6727
    assert cell.confidence == 1.0


def test_mixed_sign_weights_abort_the_whole_synthesis():
    """BUG (pinned) — the user-facing consequence of the unclamped composite.

    `_weighted_mean` returns ~-5.4e15 for nearly-cancelling weights (see
    test_mixed_sign_weights_escape_the_unit_interval); `ScoredCell` then
    rejects it on `ge=0.0`, so `synthesize` raises and the orchestrator loses
    the entire run *after* paying for every token. Correct behaviour is to
    clamp in `_weighted_mean` and/or validate weights at the API boundary;
    both files belong to others, so this test documents the crash instead.
    """
    agents = [
        result("a", [sc(1.0, 1.0, CELL_A)]),
        result("b", [sc(0.5, 1.0, CELL_A)]),
        result("c", [sc(0.5, 1.0, CELL_A)]),
    ]
    with pytest.raises(ValidationError):
        synthesize(agents, grid(CELL_A), {"a": 0.3, "b": -0.1, "c": -0.2}, {})


def test_synthesize_then_normalize_is_the_orchestrators_actual_sequence():
    """The two functions compose: absolute composites, then AOI-relative fields.

    `synthesize` deliberately does not normalize (the orchestrator interpolates
    to the display grid in between), so this is the only place the whole scoring
    path is exercised as the orchestrator runs it.
    """
    lith = result(
        "lithology",
        [sc(s, 1.0, cid) for cid, s in ((CELL_A, 0.1), (CELL_B, 0.5), (CELL_C, 0.9))],
    )
    hist = result(
        "historical",
        [
            sc(0.0, 0.0, CELL_A, evidence=["Cell not scored by LLM"]),  # a miss
            sc(0.5, 1.0, CELL_B),
            sc(0.5, 1.0, CELL_C),
        ],
    )

    out = normalize_relative(synthesize([lith, hist], grid(CELL_A, CELL_B, CELL_C), {}, {}))

    # CELL_A: the historical miss is ignored, so the composite is lithology's.
    assert [c.score for c in out] == [0.1, 0.5, 0.7]
    assert [c.confidence for c in out] == [0.5, 1.0, 1.0]
    assert [c.relative_score for c in out] == [0.0, 0.6667, 1.0]
    assert [c.percentile for c in out] == [0.1667, 0.5, 0.8333]
    # n=3 → the top cell's percentile is 5/6, below PCT_HIGH; see
    # test_top_cell_needs_five_cells_before_it_can_reach_high.
    assert [c.tier for c in out] == ["negligible", "low", "medium"]
