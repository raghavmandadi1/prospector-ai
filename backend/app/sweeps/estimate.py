"""Cost and time preview for a sweep, before a single token is spent.

A sweep is the first thing in this project that can spend real money without the
user watching, so §40.3 requires an estimate up front. §42 additionally requires
an estimated *time*, which nothing else in the codebase produces.

TWO HONESTY RULES, BOTH LOAD-BEARING
------------------------------------
**Estimate from the tile-size distribution, never from area.** Per-tile overhead
does not scale with tile size: a tile pays one ``build_local_context()`` and at
least one batch per agent whether it holds 2 cells or 50. Because the grid origin
is fixed, a region straddling block boundaries produces ragged tiles — the proxy
corridor at 1000 m is 11 tiles holding 498 cells, ranging from 2 to 100. An
``area / tile_area`` estimate says 5 tiles and understates the call count by
more than half.

**Say what the numbers rest on.** ``MODEL_PRICING`` is hardcoded and will drift;
it is not billing data. And the per-batch token counts and wall-clock below are
defaults until somebody measures a real tile, so every estimate carries a
``basis`` of ``"default"`` or ``"measured"`` and the UI is expected to show it.
An estimate presented as a quote is worse than no estimate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Sequence

from app.agents.base_agent import (
    BATCH_SIZE,
    MAX_CONCURRENT_BATCHES,
    MODEL_NAME,
    MODEL_PRICING,
)

#: Defaults until a real tile is measured. Deliberately round numbers so nobody
#: mistakes them for observations.
#:
#: A 50-cell batch prompt is dominated by the knowledge file (16-36 KB of
#: markdown as the system prompt) plus per-cell evidence lines.
DEFAULT_INPUT_TOKENS_PER_BATCH = 12_000
DEFAULT_OUTPUT_TOKENS_PER_BATCH = 2_500
#: Wall clock for one batch call, seconds.
DEFAULT_SECONDS_PER_BATCH = 25.0
#: Non-LLM time per tile: build_local_context() over the tile's cells. Runs once
#: per tile and has never been profiled, which is why it is called out
#: separately rather than folded into the batch figure.
DEFAULT_SECONDS_PER_TILE_CONTEXT = 4.0


@dataclass
class SweepEstimate:
    tiles: int
    core_cells: int
    context_cells: int
    agents: int
    batches_per_agent: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    est_seconds: float
    model: str
    basis: str
    pricing_note: str
    tile_cell_counts: List[int]
    #: Calls the same cells would cost if they packed into full tiles. The gap
    #: is what ragged region-to-block alignment costs, and it is large enough to
    #: matter: the proxy corridor at 2000 m is 137 cells in 6 tiles, three of
    #: which hold 3 cells or fewer and each still pay one batch per agent.
    ideal_llm_calls: int
    raggedness_overhead: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def batches_for(cell_count: int, batch_size: int = BATCH_SIZE) -> int:
    """Batches one agent needs for a tile. A tile with cells always costs ≥1."""
    if cell_count <= 0:
        return 0
    return math.ceil(cell_count / batch_size)


def estimate_sweep(
    tiles: Sequence[Any],
    agent_ids: Sequence[str],
    model: str = MODEL_NAME,
    measured: Optional[Dict[str, float]] = None,
    cache_hit_fraction: float = 0.0,
) -> SweepEstimate:
    """Estimate a sweep from its actual tiles.

    ``measured`` may carry any of ``input_tokens_per_batch``,
    ``output_tokens_per_batch``, ``seconds_per_batch``,
    ``seconds_per_tile_context`` — taken from one real tile run. Supplying any of
    them flips ``basis`` to "measured", because a half-measured estimate is still
    better grounded than a fully guessed one and the UI should say so.

    ``cache_hit_fraction`` discounts cells expected to be served from the cell
    cache. Default 0.0: assuming a cold cache is the estimate that cannot
    surprise anyone with a bill.
    """
    m = measured or {}
    tok_in = m.get("input_tokens_per_batch", DEFAULT_INPUT_TOKENS_PER_BATCH)
    tok_out = m.get("output_tokens_per_batch", DEFAULT_OUTPUT_TOKENS_PER_BATCH)
    sec_batch = m.get("seconds_per_batch", DEFAULT_SECONDS_PER_BATCH)
    sec_ctx = m.get("seconds_per_tile_context", DEFAULT_SECONDS_PER_TILE_CONTEXT)

    n_agents = len(agent_ids)
    counts = [t.cell_count for t in tiles]
    core_cells = sum(counts)
    context_cells = sum(len(t.halo_cell_ids) for t in tiles)

    billable = max(0.0, 1.0 - max(0.0, min(1.0, cache_hit_fraction)))
    total_batches = 0
    for n in counts:
        billable_cells = int(round(n * billable))
        total_batches += batches_for(billable_cells)
    llm_calls = total_batches * n_agents

    input_tokens = int(llm_calls * tok_in)
    output_tokens = int(llm_calls * tok_out)

    price = MODEL_PRICING.get(model)
    if price:
        cost = (input_tokens * price["input"] + output_tokens * price["output"]) / 1e6
    else:
        cost = 0.0

    # Tiles run strictly sequentially (§40.1), and within a tile each agent runs
    # up to MAX_CONCURRENT_BATCHES at once with all agents in parallel. So a
    # tile's LLM time is its slowest agent's batch chain, not the sum.
    seconds = 0.0
    for n in counts:
        b = batches_for(int(round(n * billable)))
        waves = math.ceil(b / MAX_CONCURRENT_BATCHES) if b else 0
        seconds += sec_ctx + waves * sec_batch

    ideal_calls = batches_for(int(round(core_cells * billable))) * n_agents

    return SweepEstimate(
        tiles=len(tiles),
        core_cells=core_cells,
        context_cells=context_cells,
        agents=n_agents,
        batches_per_agent=total_batches,
        llm_calls=llm_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        est_cost_usd=round(cost, 4),
        est_seconds=round(seconds, 1),
        model=model,
        basis="measured" if m else "default",
        pricing_note=(
            "Local estimate from a hardcoded price table, not billing data. "
            "Order of magnitude only."
        ),
        tile_cell_counts=counts,
        ideal_llm_calls=ideal_calls,
        raggedness_overhead=(
            round(llm_calls / ideal_calls, 2) if ideal_calls else 1.0
        ),
    )


def measured_from_run(run_doc: Dict[str, Any]) -> Dict[str, float]:
    """Derive per-batch figures from a completed run record.

    Lets the second sweep be estimated from the first rather than from the
    guesses at the top of this module.
    """
    usage = (run_doc.get("outputs") or {}).get("usage") or run_doc.get("usage") or {}
    calls = usage.get("llm_calls") or 0
    if not calls:
        return {}
    out: Dict[str, float] = {
        "input_tokens_per_batch": (usage.get("input_tokens") or 0) / calls,
        "output_tokens_per_batch": (usage.get("output_tokens") or 0) / calls,
    }
    total_s = (run_doc.get("timings") or {}).get("total_s")
    if total_s:
        waves = max(1, math.ceil(calls / MAX_CONCURRENT_BATCHES))
        out["seconds_per_batch"] = float(total_s) / waves
    return out
