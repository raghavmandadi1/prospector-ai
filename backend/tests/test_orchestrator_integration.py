"""
Whole-pipeline test with the Anthropic client stubbed: AOI in → grid → agents →
synthesis → relative normalization → run record on disk.

This is the test that would have caught the all-zero-scores bug in
.claude/mistakes-log.md, which passed the "it ran without an exception" bar for
weeks.

Run:  .venv/bin/python -m pytest backend/tests/test_orchestrator_integration.py -q
"""
import json

import pytest

from app.agents import orchestrator as orch
from app.agents.orchestrator import OrchestratorAgent
from tests.test_run_record_and_cache import AOI, FakeClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """Every agent gets a fake client; records and cache go to tmp_path."""
    counter = {"calls": 0}
    from app.cache.cell_cache import CellCache

    cache = CellCache(tmp_path / "cells.sqlite")
    monkeypatch.setattr("app.cache.cell_cache.get_cache", lambda: cache)

    real_init = orch.BaseAgent.__init__

    def fake_init(self, api_key=None):
        self._client = FakeClient(counter)

    monkeypatch.setattr(orch.BaseAgent, "__init__", fake_init)

    class Recorder(orch.RunRecorder):
        def __init__(self, run_id=None, runs_dir=None, enabled=None):
            super().__init__(run_id=run_id, runs_dir=tmp_path / "runs", enabled=True)

    monkeypatch.setattr(orch, "RunRecorder", Recorder)
    yield counter, tmp_path
    monkeypatch.setattr(orch.BaseAgent, "__init__", real_init)


async def run_once(config=None):
    events = []

    async def emit(payload):
        events.append(payload)

    final, agents = await OrchestratorAgent(api_key="stub").run_analysis(
        job_id="job-1",
        aoi_geojson=AOI,
        target_mineral="gold",
        config=config or {"resolution_m": 2000, "enabled_agents": ["lithology", "structure"]},
        emit_fn=emit,
    )
    return final, agents, events


async def test_full_run_produces_scores_and_a_record(stubbed):
    counter, tmp_path = stubbed
    final, agents, events = await run_once()

    cells = final["scored_cells"]
    assert cells, "a run must produce cells"

    # The bug this suite exists for: everything scored zero and nobody noticed.
    assert any(c["score"] > 0 for c in cells), "all-zero composite is the known bug"
    assert all(0.0 <= c["score"] <= 1.0 for c in cells)
    assert all(c["tier"] in ("high", "medium", "low", "negligible") for c in cells)

    # Cell ids are the durable, globally-anchored kind
    assert all(c["cell_id"].startswith("wa5070-") for c in cells)

    # Exactly one record, and it is complete
    records = list((tmp_path / "runs").glob("*.json"))
    assert len(records) == 1
    doc = json.loads(records[0].read_text())
    assert doc["status"] == "completed"
    assert doc["inputs"]["target_mineral"] == "gold"
    assert doc["inputs"]["aoi_area_km2"] > 0
    assert set(doc["agent_results"]) == {"lithology", "structure"}
    assert doc["provenance"]["prompt_version"]
    assert doc["timings"]["total_s"] >= 0
    # Both agents are grounded now. This assertion used to read
    # `"structure" in agents_without_knowledge` — a deliberate canary on Known
    # Gap #1, and it fired the moment knowledge/structure/gold.md was written,
    # which is exactly what it was for. Inverted rather than deleted, so a
    # knowledge file going missing again is still a test failure.
    assert doc["provenance"]["agents_without_knowledge"] == []
    assert "lithology/gold.md" in doc["provenance"]["knowledge_files"]
    assert "structure/gold.md" in doc["provenance"]["knowledge_files"]

    # Provenance now records which local evidence the agents actually saw. An
    # empty list here means the run scored from model prior alone, and a
    # benchmark delta taken across a change in this list is not a like-for-like
    # comparison — so it is recorded per run rather than assumed constant.
    assert "context_sources" in doc["provenance"]
    assert isinstance(doc["provenance"]["context_sources"], list)
    # No field pins may have been promoted to `truth` or `evidence` by accident:
    # a truth pin visible to the model makes every later benchmark number
    # meaningless, so the census is asserted rather than merely stored.
    assert doc["provenance"]["pin_roles_active"].get("truth", 0) == 0

    # GROUNDED IS NOT COVERED. The assertion above says every agent loaded a
    # knowledge file; it says nothing about whether any *data* reached them.
    # "Steps for Raghav 3.0" §34 gates a regional sweep on grounding for exactly
    # the right reason — a sweep multiplies whatever the agents are — but on the
    # wrong variable: all six agents have been grounded since 2026-08-12 while
    # the priority corridor still had zero mapped geology (Known Gap #2b).
    #
    # So the real tripwire is coverage, and the thing that must never break is
    # that coverage is *reported per run* rather than assumed. A run that scored
    # nothing but model prior and a run that scored on 300 faults must be
    # distinguishable after the fact, from the record alone.
    coverage = doc["inputs"]["spatial_coverage"]
    assert isinstance(coverage, dict), "coverage must be recorded, not assumed"
    for key in ("cells_total", "cells_with_geology", "cells_with_structures"):
        assert key in coverage, f"run record must report {key}"
        assert isinstance(coverage[key], int)
    # cells_with_geology <= cells_total is the invariant that makes the ratio
    # meaningful; a count that can exceed the denominator is a counting bug.
    assert coverage["cells_with_geology"] <= coverage["cells_total"]
    assert coverage["cells_with_structures"] <= coverage["cells_total"]


async def test_second_identical_run_costs_nothing(stubbed):
    counter, tmp_path = stubbed
    await run_once()
    calls_after_first = counter["calls"]
    assert calls_after_first > 0

    final, agents, events = await run_once()
    assert counter["calls"] == calls_after_first, "identical re-run must hit cache"

    doc = json.loads(sorted((tmp_path / "runs").glob("*.json"))[-1].read_text())
    assert doc["cache"]["misses"] == 0
    assert doc["cache"]["hits"] > 0
    # Same absolute scores, freshly recomputed tiers
    assert any(c["score"] > 0 for c in final["scored_cells"])


async def test_a_failed_run_still_leaves_a_record(stubbed, monkeypatch):
    counter, tmp_path = stubbed
    monkeypatch.setattr(
        orch, "generate_grid", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("grid exploded"))
    )
    with pytest.raises(RuntimeError):
        await run_once()

    records = list((tmp_path / "runs").glob("*.json"))
    assert len(records) == 1
    doc = json.loads(records[0].read_text())
    assert doc["status"] == "failed"
    assert "grid exploded" in doc["error"]


async def test_api_key_never_lands_in_the_record(stubbed):
    counter, tmp_path = stubbed
    await run_once(
        config={
            "resolution_m": 2000,
            "enabled_agents": ["lithology"],
            # A careless passthrough of the dev-mode request body
            "weights": {"lithology": 1.0},
        }
    )
    text = list((tmp_path / "runs").glob("*.json"))[0].read_text()
    assert "sk-ant" not in text
    assert "anthropic_api_key" not in text


async def test_coarsening_walks_the_ladder(stubbed):
    """A fine resolution over a large AOI must land on a ladder step."""
    from app.scoring.grid import RESOLUTION_LADDER

    counter, tmp_path = stubbed
    final, _, events = await run_once(
        config={"resolution_m": 125, "enabled_agents": ["lithology"]}
    )
    assert final["analysis_resolution_m"] in RESOLUTION_LADDER
    assert final["display_resolution_m"] in RESOLUTION_LADDER
    assert final["analysis_resolution_m"] >= final["display_resolution_m"]


async def test_interpolated_cells_inherit_their_containing_parent(stubbed):
    from app.scoring.grid import parent_cell_id

    counter, tmp_path = stubbed
    final, _, _ = await run_once(
        config={"resolution_m": 125, "enabled_agents": ["lithology"]}
    )
    coarse = final["analysis_resolution_m"]
    fine = final["display_resolution_m"]
    if coarse == fine:
        pytest.skip("no interpolation happened for this AOI size")

    interpolated = [c for c in final["scored_cells"] if c.get("parent_cell_id")]
    assert interpolated
    for c in interpolated:
        # Exact containment, not nearest-centre
        assert c["parent_cell_id"] == parent_cell_id(c["cell_id"], coarse)
