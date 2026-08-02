"""
End-to-end tests for run records (§3) and the cell cache (§4), with the
Anthropic client stubbed so no tokens are spent.

These cover the acceptance criteria that are cheap to assert and expensive to
get wrong: that a re-run is a full cache hit, that editing a knowledge file
invalidates exactly one agent, that relative fields are never cached, and that
the API key never reaches disk.

Run:  .venv/bin/python -m pytest backend/tests/test_run_record_and_cache.py -q
"""
import json
from typing import Any, Dict, List

import pytest

from app.agents.base_agent import BaseAgent, batch_label
from app.cache.cell_cache import CellCache
from app.models.agent_result import AgentResult
from app.runs.record import RunRecorder, assert_no_secrets, sha256_text

AOI = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [-121.49, 47.98],
                [-121.39, 47.98],
                [-121.39, 48.08],
                [-121.49, 48.08],
                [-121.49, 47.98],
            ]
        ],
    },
}


class FakeMessage:
    def __init__(self, text: str):
        self.content = [type("Block", (), {"text": text})()]
        self.usage = type(
            "U",
            (),
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )()
        self.stop_reason = "end_turn"


class FakeMessages:
    def __init__(self, counter: Dict[str, int]):
        self._counter = counter

    async def create(self, **kwargs) -> FakeMessage:
        self._counter["calls"] += 1
        # Recover the labels the prompt listed and score them all.
        prompt: str = kwargs["messages"][0]["content"]
        labels = [
            line.split(":")[0].strip("- ").strip()
            for line in prompt.splitlines()
            if line.strip().startswith("- c")
        ]
        payload = [
            {
                "cell_id": lbl,
                "score": 0.5,
                "confidence": 0.8,
                "evidence": ["stub evidence"],
                "data_sources_used": ["stub"],
            }
            for lbl in labels
        ]
        return FakeMessage("```json\n" + json.dumps(payload) + "\n```")


class FakeClient:
    def __init__(self, counter: Dict[str, int]):
        self.messages = FakeMessages(counter)


class StubAgent(BaseAgent):
    agent_id = "stub"
    agent_name = "Stub Agent"

    def build_prompt(self, aoi_geojson, target_mineral, spatial_context) -> str:
        from app.agents.base_agent import cell_summary

        return "Cells:\n" + cell_summary(spatial_context["grid_cells"])


@pytest.fixture
def grid_cells() -> List[Dict[str, Any]]:
    from app.scoring.grid import generate_grid

    return [c.model_dump() for c in generate_grid(AOI, 2000)]


@pytest.fixture
def cache(tmp_path, monkeypatch) -> CellCache:
    c = CellCache(tmp_path / "cells.sqlite")
    monkeypatch.setattr("app.cache.cell_cache.get_cache", lambda: c)
    return c


def make_agent(counter: Dict[str, int], knowledge: str = "KB v1") -> StubAgent:
    agent = StubAgent.__new__(StubAgent)
    agent._client = FakeClient(counter)
    agent._knowledge_text = knowledge
    # Pin grounding so the test controls the knowledge hash directly.
    agent.load_knowledge = lambda domain, mineral: agent._knowledge_text  # type: ignore
    agent.resolve_knowledge_path = lambda domain, mineral: None  # type: ignore
    return agent


async def run_agent(agent: StubAgent, grid_cells, config=None) -> AgentResult:
    ctx = {"grid_cells": grid_cells, "aoi_geojson": AOI, "_error": None}
    return await agent.run(AOI, "gold", ctx, config or {"run_id": "t"})


# --- §4.5 cache acceptance criteria ---------------------------------------


@pytest.mark.asyncio
async def test_identical_rerun_is_a_full_cache_hit(cache, grid_cells):
    counter = {"calls": 0}
    first = await run_agent(make_agent(counter), grid_cells)
    assert first.cache_hits == 0
    assert counter["calls"] > 0
    first_calls = counter["calls"]

    second = await run_agent(make_agent(counter), grid_cells)
    assert second.cache_misses == 0
    assert second.cache_hits == len(grid_cells)
    assert counter["calls"] == first_calls, "second run must make no LLM calls"

    # And the scores survive the round trip intact
    a = {c.cell_id: c.score for c in first.scored_cells}
    b = {c.cell_id: c.score for c in second.scored_cells}
    assert a == b


@pytest.mark.asyncio
async def test_overlapping_aoi_gets_partial_hits(cache, grid_cells):
    from app.scoring.grid import generate_grid

    counter = {"calls": 0}
    await run_agent(make_agent(counter), grid_cells)

    shifted = json.loads(json.dumps(AOI))
    shifted["geometry"]["coordinates"] = [
        [[lon + 0.05, lat] for lon, lat in AOI["geometry"]["coordinates"][0]]
    ]
    shifted_cells = [c.model_dump() for c in generate_grid(shifted, 2000)]

    result = await run_agent(make_agent(counter), shifted_cells)
    assert result.cache_hits > 0, "the overlap should hit"
    assert result.cache_misses > 0, "the new ground should miss"
    assert result.cache_hits + result.cache_misses == len(shifted_cells)


@pytest.mark.asyncio
async def test_editing_the_knowledge_file_invalidates_that_agent(cache, grid_cells):
    counter = {"calls": 0}
    await run_agent(make_agent(counter, knowledge="KB v1"), grid_cells)

    # One byte different → every cell must be rescored
    result = await run_agent(make_agent(counter, knowledge="KB v2"), grid_cells)
    assert result.cache_hits == 0
    assert result.cache_misses == len(grid_cells)


@pytest.mark.asyncio
async def test_spatial_context_change_invalidates(cache, grid_cells):
    counter = {"calls": 0}
    agent = make_agent(counter)
    ctx = {"grid_cells": grid_cells, "aoi_geojson": AOI, "known_deposits": []}
    await agent.run(AOI, "gold", ctx, {"run_id": "t"})

    # Known Gaps #2 gets fixed and records start arriving
    ctx2 = dict(ctx, known_deposits=[{"name": "Pride of the Mountains"}])
    result = await make_agent(counter).run(AOI, "gold", ctx2, {"run_id": "t"})
    assert result.cache_hits == 0, "new spatial context must invalidate the cache"


@pytest.mark.asyncio
async def test_no_cache_flag_forces_a_fresh_run(cache, grid_cells):
    counter = {"calls": 0}
    await run_agent(make_agent(counter), grid_cells)
    before = counter["calls"]
    result = await run_agent(
        make_agent(counter), grid_cells, config={"run_id": "t", "use_cache": False}
    )
    assert result.cache_hits == 0
    assert counter["calls"] > before


@pytest.mark.asyncio
async def test_relative_fields_are_never_cached(cache, grid_cells):
    """tier/percentile/relative_score describe the AOI, not the ground."""
    counter = {"calls": 0}
    await run_agent(make_agent(counter), grid_cells)
    import sqlite3

    conn = sqlite3.connect(cache.path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_cell_scores)")}
    assert not (cols & {"relative_score", "percentile", "tier"})

    restored = await run_agent(make_agent(counter), grid_cells)
    for c in restored.scored_cells:
        assert c.relative_score is None
        assert c.percentile is None
        assert c.tier is None


@pytest.mark.asyncio
async def test_zero_confidence_cells_are_not_cached(cache, grid_cells):
    """A parse failure must not become a permanent zero."""
    counter = {"calls": 0}
    agent = make_agent(counter)

    class BrokenMessages(FakeMessages):
        async def create(self, **kwargs):
            self._counter["calls"] += 1
            return FakeMessage("I'm sorry, I can't help with that.")

    agent._client.messages = BrokenMessages(counter)
    result = await run_agent(agent, grid_cells)
    assert all(c.confidence == 0.0 for c in result.scored_cells)

    import sqlite3

    conn = sqlite3.connect(cache.path)
    (n,) = conn.execute("SELECT COUNT(*) FROM agent_cell_scores").fetchone()
    assert n == 0, "zero-confidence placeholders must never be cached"


@pytest.mark.asyncio
async def test_cache_failure_degrades_to_a_normal_run(grid_cells, monkeypatch):
    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("app.cache.cell_cache.get_cache", boom)
    counter = {"calls": 0}
    result = await run_agent(make_agent(counter), grid_cells)
    assert result.status == "completed"
    assert len(result.scored_cells) == len(grid_cells)


# --- §3.6 run record acceptance criteria ----------------------------------


def test_run_record_writes_one_file_and_round_trips(tmp_path):
    rec = RunRecorder(run_id="abc123", runs_dir=tmp_path, enabled=True)
    rec.set_inputs(target_mineral="gold", requested_resolution_m=1000)
    rec.set_composite_cells(
        [
            {
                "cell_id": "wa5070-1000m-000277-000380",
                "geometry": {"type": "Polygon", "coordinates": []},
                "score": 0.7,
                "tier": "high",
            }
        ]
    )
    rec.set_status("completed")
    path = rec.write()

    assert path is not None
    assert len(list(tmp_path.glob("*.json"))) == 1
    doc = json.loads(path.read_text())
    assert doc["run_id"] == "abc123"
    assert doc["status"] == "completed"
    # Geometry is dropped — cell_id regenerates it
    assert "geometry" not in doc["composite_cells"][0]
    assert doc["composite_cells"][0]["cell_id"] == "wa5070-1000m-000277-000380"


def test_cell_ids_in_a_record_are_locatable_without_the_record():
    from app.scoring.grid import cell_id_to_bbox

    b = cell_id_to_bbox("wa5070-1000m-000277-000380")
    assert -125.5 < b[0] < -116.0 and 45.0 < b[1] < 49.5


def test_api_key_can_never_reach_a_run_record(tmp_path):
    rec = RunRecorder(run_id="leak", runs_dir=tmp_path, enabled=True)
    rec.set_inputs(anthropic_api_key="sk-ant-secret", target_mineral="gold")
    assert rec.write() is None, "a record containing a key must not be written"
    assert not list(tmp_path.glob("*.json"))


def test_assert_no_secrets_finds_nested_keys():
    with pytest.raises(ValueError):
        assert_no_secrets({"inputs": {"config": {"api_key": "sk-x"}}})
    assert_no_secrets({"inputs": {"config": {"resolution_m": 1000}}})


def test_deleting_the_runs_dir_breaks_nothing(tmp_path):
    import shutil

    rec = RunRecorder(run_id="r1", runs_dir=tmp_path / "runs", enabled=True)
    rec.set_status("completed")
    assert rec.write() is not None
    shutil.rmtree(tmp_path / "runs")
    rec2 = RunRecorder(run_id="r2", runs_dir=tmp_path / "runs", enabled=True)
    rec2.set_status("completed")
    assert rec2.write() is not None


def test_provenance_records_ungrounded_agents_and_knowledge_hashes():
    from app.runs.record import provenance_block

    block = provenance_block(
        knowledge_files={"lithology/gold.md": "# WA gold lithology"},
        agents_without_knowledge=["structure", "geochemistry"],
        spatial_context_available=False,
        model="claude-sonnet-4-6",
    )
    assert block["knowledge_files"]["lithology/gold.md"] == sha256_text(
        "# WA gold lithology"
    )
    assert block["agents_without_knowledge"] == ["geochemistry", "structure"]
    assert block["spatial_context_available"] is False
    assert "grid_version" in block, "a grid change must never be silent"


def test_failed_run_still_records(tmp_path):
    rec = RunRecorder(run_id="boom", runs_dir=tmp_path, enabled=True)
    rec.set_status("failed", error="ValueError: nope")
    path = rec.write()
    doc = json.loads(path.read_text())
    assert doc["status"] == "failed"
    assert doc["error"] == "ValueError: nope"
