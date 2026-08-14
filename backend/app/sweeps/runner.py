"""Running a sweep: one tile at a time, resumably, then normalized as a whole.

EXECUTION MODEL — ONE REQUEST PER TILE
--------------------------------------
The client drives the loop: create the sweep, then POST each pending tile in
turn. That choice buys three things and costs one.

Buys: pause is "stop asking for the next tile" and needs no job registry; the
existing per-request cancellation in ``analysis_dev`` works unchanged, including
its ``is_disconnected()`` poll that actually tears down in-flight Anthropic
calls; and a multi-hour sweep never sits behind a single HTTP response that
every proxy in the path wants to time out.

Costs: the sweep stops if the browser closes. That is mitigated rather than
solved — the manifest is rewritten atomically after every tile transition, and
an interrupted tile goes back to ``pending`` rather than ``failed``, so resuming
re-runs at most one tile and the cell cache makes even that nearly free.

TILE RESULTS ARE ACCUMULATED OUTSIDE THE MANIFEST
-------------------------------------------------
Cells go to ``data/sweeps/<id>_cells.json`` rather than into the manifest, which
is rewritten on every tile transition and would otherwise grow a megabyte of
evidence strings that get serialised dozens of times per sweep.

Geometry is not stored: a cell id regenerates its square exactly, which is the
whole point of the fixed grid.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from uuid import uuid4

from app.agents.base_agent import MODEL_NAME
from app.agents.orchestrator import OrchestratorAgent
from app.config import SWEEPS_DIR
from app.models.agent_result import ScoredCell
from app.scoring.engine import normalize_relative
from app.scoring.grid import cell_id_to_geojson
from app.sweeps.estimate import estimate_sweep
from app.sweeps.manifest import (
    SWEEP_CANCELLED,
    SweepManifest,
    TileOutcome,
    classify_tile,
)
from app.sweeps.tiles import TILE_BLOCK, TILE_HALO, Tile, tiles_for_region

logger = logging.getLogger(__name__)

DEFAULT_AGENTS = [
    "lithology",
    "structure",
    "geochemistry",
    "historical",
    "remote_sensing",
    "proximity",
]


# --- cell accumulation ------------------------------------------------------


def cells_path(sweep_id: str, sweeps_dir: Optional[Path] = None) -> Path:
    d = Path(sweeps_dir) if sweeps_dir else SWEEPS_DIR
    return d / f"{sweep_id}_cells.json"


def load_cells(sweep_id: str, sweeps_dir: Optional[Path] = None) -> Dict[str, Dict]:
    p = cells_path(sweep_id, sweeps_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[%s] Could not read sweep cells: %s", sweep_id, exc)
        return {}


def store_cells(
    sweep_id: str, cells: Dict[str, Dict], sweeps_dir: Optional[Path] = None
) -> None:
    p = cells_path(sweep_id, sweeps_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cells, default=str), encoding="utf-8")
    os.replace(tmp, p)


def _thin(cell: Any) -> Dict[str, Any]:
    d = cell if isinstance(cell, dict) else cell.model_dump()
    d = dict(d)
    d.pop("geometry", None)  # regenerated from cell_id
    d.pop("display_geometry", None)
    return d


# --- creating ---------------------------------------------------------------


def create_sweep(
    region_geojson: Dict[str, Any],
    target_mineral: str,
    resolution_m: int,
    agent_ids: Optional[Sequence[str]] = None,
    weights: Optional[Dict[str, float]] = None,
    block: int = TILE_BLOCK,
    halo: int = TILE_HALO,
    sweeps_dir: Optional[Path] = None,
    corridor_note: Optional[str] = None,
) -> SweepManifest:
    """Tile a region and write the manifest. Spends nothing."""
    tiles = tiles_for_region(region_geojson, resolution_m, block=block, halo=halo)
    agents = list(agent_ids or DEFAULT_AGENTS)

    m = SweepManifest(sweeps_dir=sweeps_dir)
    m.set_inputs(
        region_geojson=region_geojson,
        target_mineral=target_mineral,
        resolution_m=resolution_m,
        block=block,
        halo=halo,
        enabled_agents=agents,
        weights=weights,
        # The corridor polygon is a proxy bbox until a real one is supplied
        # (§44). Recording which one was used makes a later swap a diffable
        # rebuild rather than an undocumented change of ground.
        corridor_note=corridor_note,
    )
    m.set_tiles(tiles)
    m.set_estimate(estimate_sweep(tiles, agents).to_dict())
    m.write()
    logger.info(
        "Sweep %s created: %d tiles, %d cells at %d m",
        m.sweep_id,
        len(tiles),
        sum(t.cell_count for t in tiles),
        resolution_m,
    )
    return m


def tile_from_manifest_entry(entry: Dict[str, Any]) -> Tile:
    return Tile(
        tile_id=entry["tile_id"],
        resolution_m=entry["resolution_m"],
        block=entry["block"],
        tile_col=entry["tile_col"],
        tile_row=entry["tile_row"],
        core_cell_ids=tuple(entry["core_cell_ids"]),
        halo_cell_ids=tuple(entry["halo_cell_ids"]),
    )


# --- running one tile -------------------------------------------------------


async def run_tile(
    manifest: SweepManifest,
    tile_id: str,
    api_key: str,
    emit_fn: Optional[Callable] = None,
) -> TileOutcome:
    """Run one tile and record what it actually produced.

    Never raises for a tile-level failure: a bad tile marks itself failed and
    the sweep goes on, because one unlucky tile should not cost the other ten.
    An ``asyncio.CancelledError`` IS propagated — that is the stop signal, and
    the tile is released back to ``pending`` on the way out.
    """
    entry = manifest.tile(tile_id)
    if entry is None:
        raise KeyError(f"{tile_id} is not a tile of sweep {manifest.sweep_id}")

    tile = tile_from_manifest_entry(entry)
    inputs = manifest.doc["inputs"]
    job_id = f"{manifest.sweep_id}:{tile_id}"
    manifest.mark_tile_running(tile_id)

    config = {
        "resolution_m": tile.resolution_m,
        "enabled_agents": inputs.get("enabled_agents") or DEFAULT_AGENTS,
        "tile": tile.model_dump(),
        "sweep_id": manifest.sweep_id,
    }
    # Only set `weights` when there are some. The orchestrator reads it as
    # `config.get("weights", DEFAULT_WEIGHTS[mineral])`, and a present-but-None
    # key defeats that default — `synthesize` then dies on `None.get(...)` and
    # every tile of the sweep fails identically.
    if inputs.get("weights"):
        config["weights"] = inputs["weights"]

    # run_analysis returns scores but NOT usage — the token ledger goes out as a
    # `usage` SSE event instead. The classifier needs it (a tile that made zero
    # calls and served zero cache hits did not do the work, whatever it
    # returned), so tap the event stream on the way past rather than widen the
    # orchestrator's return contract.
    seen: Dict[str, Any] = {"usage": {}, "cache_hits": 0}

    async def tap(payload: Dict[str, Any]) -> None:
        ev = payload.get("event")
        if ev == "usage":
            seen["usage"] = payload
        elif ev == "cache_status":
            seen["cache_hits"] += int(payload.get("hits") or 0)
        if emit_fn is not None:
            await emit_fn(payload)

    started = time.monotonic()
    try:
        final, agent_results = await OrchestratorAgent(api_key=api_key).run_analysis(
            job_id=job_id,
            # The tile's own footprint is the AOI. The orchestrator ignores it
            # for gridding (the tile decides that) but still uses it for the
            # AOI-wide spatial summaries.
            aoi_geojson=tile.core_geojson(),
            target_mineral=inputs["target_mineral"],
            config=config,
            emit_fn=tap,
        )
    except asyncio.CancelledError:
        manifest.release_tile(tile_id)
        raise
    except Exception as exc:
        logger.exception("[%s] Tile failed: %s", job_id, exc)
        outcome = TileOutcome("failed", 0, 0, 0, 0, str(exc))
        manifest.mark_tile_outcome(tile_id, outcome)
        return outcome

    scored = (final or {}).get("scored_cells") or []
    usage = seen["usage"]
    outcome = classify_tile(scored, agent_results, usage, cache_hits=seen["cache_hits"])

    if outcome.status == "complete":
        cells = load_cells(manifest.sweep_id, manifest.sweeps_dir)
        for c in scored:
            cells[c["cell_id"] if isinstance(c, dict) else c.cell_id] = _thin(c)
        store_cells(manifest.sweep_id, cells, manifest.sweeps_dir)
    else:
        logger.warning("[%s] Tile did not complete: %s", job_id, outcome.reason)

    usage_block = {
        k: v for k, v in usage.items() if k not in ("event", "job_id", "by_agent")
    }
    usage_block["cache_hits"] = seen["cache_hits"]
    usage_block["duration_s"] = round(time.monotonic() - started, 2)
    manifest.mark_tile_outcome(tile_id, outcome, run_id=job_id, usage=usage_block)
    return outcome


# --- finishing --------------------------------------------------------------


def finalize_sweep(
    manifest: SweepManifest, force_partial: bool = False
) -> List[Dict[str, Any]]:
    """Normalize the whole region ONCE, after every tile has completed.

    This is the step that makes tiling mean anything (§39). Normalizing each
    tile against itself makes the best cell of a barren tile "high" and a
    mediocre cell of a rich tile "medium" — both correct per tile, both wrong
    regionally — and stitches into a map with checkerboard artifacts that follow
    the tile grid rather than the geology.

    Gated on every tile being complete. A partial sweep can still be viewed with
    ``force_partial``, but it is labelled partial and normalized only over what
    exists, because ranking half a corridor against itself and then silently
    re-ranking it when the rest lands is worse than saying "partial".
    """
    # Completeness is checked BEFORE the empty-cells shortcut, deliberately.
    # Checking it second means a sweep that has not run at all finalizes
    # "successfully" over zero cells and reports 200, which reads as done.
    if not manifest.is_complete and not force_partial:
        raise ValueError(
            f"sweep {manifest.sweep_id} has "
            f"{manifest.doc['totals'].get('pending', 0)} tiles outstanding — "
            "normalizing now would rank part of the region against itself"
        )
    stored = load_cells(manifest.sweep_id, manifest.sweeps_dir)
    if not stored:
        return []

    cells = [ScoredCell(**dict(d, geometry=cell_id_to_geojson(cid)))
             for cid, d in stored.items()]
    normalize_relative(cells, scope="region")

    merged = [c.model_dump() for c in cells]
    store_cells(
        manifest.sweep_id,
        {c["cell_id"]: _thin(c) for c in merged},
        manifest.sweeps_dir,
    )
    manifest.doc["totals"]["normalized_scope"] = "region"
    manifest.doc["totals"]["normalized_cells"] = len(merged)
    manifest.doc["totals"]["partial"] = not manifest.is_complete
    manifest.write()
    logger.info(
        "Sweep %s normalized at region scope over %d cells%s",
        manifest.sweep_id,
        len(merged),
        " (PARTIAL)" if not manifest.is_complete else "",
    )
    return merged


def sweep_cells(
    manifest: SweepManifest,
    min_percentile: float = 0.0,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Merged cells with geometry, ranked best-first."""
    stored = load_cells(manifest.sweep_id, manifest.sweeps_dir)
    out = []
    for cid, d in stored.items():
        pct = d.get("percentile")
        if pct is not None and pct < min_percentile:
            continue
        out.append(dict(d, cell_id=cid, geometry=cell_id_to_geojson(cid)))
    out.sort(key=lambda c: (c.get("percentile") or 0.0, c.get("score") or 0.0), reverse=True)
    return out[:limit] if limit else out


def cancel_sweep(manifest: SweepManifest) -> None:
    """Mark a sweep cancelled, leaving it resumable rather than corrupt."""
    for t in manifest.tiles:
        if t["status"] == "running":
            manifest.release_tile(t["tile_id"])
    manifest.doc["status"] = SWEEP_CANCELLED
    manifest.write()
