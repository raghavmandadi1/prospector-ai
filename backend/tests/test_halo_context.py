"""The sweep halo is context, never output — asserted structurally.

"Steps for Raghav 3.0" §38 describes the halo as extra cells that are scored and
then discarded, and requires that they be excluded from the cache write. Neither
works against the real pipeline: the prompt unit is BATCH_SIZE = 50 rather than
the tile, batches form over the cache-filtered list, and `_store_in_cache` has no
per-cell opt-out.

So the halo travels as prompt context under a separate `n1..nM` label namespace
and there is no code path by which it becomes a score. These tests pin the three
properties that make that true, because each of them fails silently if broken —
a halo cell leaking into the output looks exactly like a normal cell.
"""
from app.agents.base_agent import (
    batch_label,
    cell_facts_block,
    neighbour_label,
)

CELLS = [{"cell_id": "core_a"}, {"cell_id": "core_b"}]
HALO = [{"cell_id": "halo_x"}, {"cell_id": "halo_y"}]
FACTS = {
    "core_a": {"unit": "Evsf"},
    "core_b": {"unit": "Kigd"},
    "halo_x": {"unit": "Eck"},
    "halo_y": {"unit": "Eco"},
}
RENDER = lambda f: f"unit {f['unit']}" if f.get("unit") else None  # noqa: E731


# --- label namespaces are disjoint -----------------------------------------


def test_label_namespaces_do_not_collide():
    core = {batch_label(i) for i in range(200)}
    halo = {neighbour_label(i) for i in range(200)}
    assert not (core & halo)
    assert batch_label(0) == "c1" and neighbour_label(0) == "n1"


# --- an absent halo changes nothing ----------------------------------------


def test_no_halo_produces_a_byte_identical_block():
    """A hand-drawn AOI's prompt must not shift when tiling lands.

    If it did, every cache key in data/cache/cells.sqlite would change and the
    entire cache would miss on the next run — the cost the cache exists to avoid.
    """
    before = cell_facts_block(CELLS, FACTS, RENDER, "## H")
    for empty in (None, [], ()):
        assert cell_facts_block(CELLS, FACTS, RENDER, "## H", context_cells=empty) == before
    assert "n1" not in before


# --- a present halo is rendered, labelled and fenced off --------------------


def test_halo_is_rendered_under_the_n_namespace_and_marked_not_to_score():
    block = cell_facts_block(CELLS, FACTS, RENDER, "## H", context_cells=HALO)
    assert "  - c1: unit Evsf" in block
    assert "  - c2: unit Kigd" in block
    assert "  - n1: unit Eck" in block
    assert "  - n2: unit Eco" in block
    assert "DO NOT score" in block
    # The core cells are still listed first and still under c-labels.
    assert block.index("c1") < block.index("n1")


def test_halo_alone_still_counts_as_data():
    """A batch whose own cells have nothing, but whose neighbours do, keeps the
    section — the point of the halo is precisely to inform a cell with no facts
    of its own about what surrounds it."""
    facts = {"halo_x": {"unit": "Eck"}}
    block = cell_facts_block(CELLS, facts, RENDER, "## H", context_cells=HALO)
    assert block
    assert "  - c1: no data" in block
    assert "  - n1: unit Eck" in block


def test_halo_cells_with_nothing_are_omitted_rather_than_listed_as_no_data():
    """Core cells get an explicit "no data" line so a gap is never read as an
    oversight. Halo cells do not: they are context, and a wall of "no data"
    neighbours is noise that costs tokens and says nothing."""
    facts = {"core_a": {"unit": "Evsf"}, "core_b": {"unit": "Kigd"}}
    block = cell_facts_block(CELLS, facts, RENDER, "## H", context_cells=HALO)
    assert "n1" not in block and "n2" not in block
    assert "  - c1: unit Evsf" in block


# --- the structural exclusion ----------------------------------------------


def test_parse_llm_response_cannot_map_a_halo_label_to_a_score():
    """The guarantee that makes halo exclusion structural.

    parse_llm_response builds its label map from the batch only, so a model that
    ignores the instruction and scores `n1` produces nothing. There is no filter
    to forget and no flag to set.
    """
    from app.agents.lithology_agent import LithologyAgent

    agent = LithologyAgent.__new__(LithologyAgent)
    response = (
        '[{"cell_id":"c1","score":0.8,"confidence":0.7,"evidence":["core"],'
        '"data_sources_used":["x"]},'
        '{"cell_id":"n1","score":0.9,"confidence":0.9,"evidence":["halo"],'
        '"data_sources_used":["x"]}]'
    )
    scored = agent.parse_llm_response(response, CELLS)
    ids = {c.cell_id for c in scored}
    assert ids == {"core_a"}, "a halo label must never become a ScoredCell"
    assert all("halo" not in e for c in scored for e in c.evidence)


def test_context_cells_are_excluded_from_the_cache_key():
    """Same cell, same evidence, different halo -> same key.

    `_cell_context`'s `aoi` branch sweeps in ANY list-valued spatial_context key.
    Left unguarded, a tile's halo would be folded into the key of every cell in
    that tile, so the same cell scored in a sweep and in a hand-drawn AOI would
    never share a hit — quietly destroying the reuse that makes the
    sweep-improve-re-sweep workflow affordable.
    """
    from app.agents.lithology_agent import LithologyAgent

    agent = LithologyAgent.__new__(LithologyAgent)
    base = {"cell_facts": {"core_a": {"geology": [{"unit": "Evsf"}]}}, "grid_cells": []}
    with_halo = dict(base, context_cells=[{"cell_id": "halo_x"}, {"cell_id": "halo_y"}])
    other_halo = dict(base, context_cells=[{"cell_id": "halo_z"}])

    cells = [{"cell_id": "core_a"}]
    k_none = agent._cache_keys(cells, "gold", None, base)["core_a"]
    k_halo = agent._cache_keys(cells, "gold", None, with_halo)["core_a"]
    k_other = agent._cache_keys(cells, "gold", None, other_halo)["core_a"]
    assert k_none == k_halo == k_other

    # ...but the cell's OWN evidence still moves the key, or the cache would be
    # serving scores computed against different facts.
    changed = dict(base, cell_facts={"core_a": {"geology": [{"unit": "Kigd"}]}})
    assert agent._cache_keys(cells, "gold", None, changed)["core_a"] != k_none
