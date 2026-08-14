"""Regional sweep endpoints.

Mode-independent, like `/cache` and `/reference`: everything here reads and
writes files on disk, never Postgres, so it works identically under DEV_MODE and
the production path.

THE REQUEST SHAPE IS ONE POST PER TILE
--------------------------------------
`POST /sweeps` creates a manifest and spends nothing. The client then POSTs
`/sweeps/{id}/tiles/{tile_id}/run` for each pending tile in turn, streaming that
tile's SSE events on the response — the same transport, the same disconnect
poll, and the same real cancellation as the single-AOI endpoint.

Pause is "stop asking for the next tile". Resume is "ask again"; the manifest
already knows which tiles are outstanding. The cost is that a closed browser
stops the sweep, which is why an interrupted tile is released back to `pending`
rather than marked failed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app.export import scored_cells_to_csv
from app.sweeps import runner as sweep_runner
from app.sweeps.diff import diff_sweeps
from app.sweeps.estimate import estimate_sweep
from app.sweeps.manifest import SweepManifest, delete_sweep, list_sweeps
from app.sweeps.tiles import TILE_BLOCK, TILE_HALO, refine_tiles, tiles_for_region

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sweeps", tags=["sweeps"])

DISCONNECT_POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0

#: Refuse to tile a region into more than this without an explicit override.
#: §42 asks for "a maximum sanity check" on the region — the mirror image of the
#: 25 km² minimum on a hand-drawn AOI, and for the opposite reason: the minimum
#: stops a pointless run, this stops an expensive one.
MAX_TILES_WITHOUT_CONFIRMATION = 40


class PreviewRequest(BaseModel):
    region_geojson: dict
    resolution_m: int = 1000
    enabled_agents: Optional[List[str]] = None
    block: int = TILE_BLOCK
    halo: int = TILE_HALO
    cache_hit_fraction: float = 0.0


class CreateSweepRequest(PreviewRequest):
    target_mineral: str = "gold"
    weights: Optional[Dict[str, float]] = None
    corridor_note: Optional[str] = None
    confirm_large: bool = False


class RunTileRequest(BaseModel):
    anthropic_api_key: str


@router.post("/preview")
async def preview_sweep(body: PreviewRequest):
    """Tile the region and estimate it. Spends nothing, persists nothing.

    This is what §42's preview panel draws: the tile grid on the map, plus the
    cell count, cost and time.
    """
    try:
        tiles = tiles_for_region(
            body.region_geojson, body.resolution_m, block=body.block, halo=body.halo
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    agents = body.enabled_agents or sweep_runner.DEFAULT_AGENTS
    est = estimate_sweep(
        tiles, agents, cache_hit_fraction=body.cache_hit_fraction
    ).to_dict()
    return {
        "tiles": [t.model_dump() for t in tiles],
        "tile_geometries": {
            t.tile_id: t.core_geojson() for t in tiles
        },
        "estimate": est,
        "needs_confirmation": len(tiles) > MAX_TILES_WITHOUT_CONFIRMATION,
        "max_tiles_without_confirmation": MAX_TILES_WITHOUT_CONFIRMATION,
    }


@router.post("")
async def create_sweep(body: CreateSweepRequest):
    try:
        tiles = tiles_for_region(
            body.region_geojson, body.resolution_m, block=body.block, halo=body.halo
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not tiles:
        raise HTTPException(status_code=400, detail="Region covers no grid cells")
    if len(tiles) > MAX_TILES_WITHOUT_CONFIRMATION and not body.confirm_large:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(tiles)} tiles exceeds the {MAX_TILES_WITHOUT_CONFIRMATION}-tile "
                "confirmation threshold. Re-submit with confirm_large=true."
            ),
        )

    m = sweep_runner.create_sweep(
        region_geojson=body.region_geojson,
        target_mineral=body.target_mineral,
        resolution_m=body.resolution_m,
        agent_ids=body.enabled_agents,
        weights=body.weights,
        block=body.block,
        halo=body.halo,
        corridor_note=body.corridor_note,
    )
    return m.doc


@router.get("")
async def get_sweeps():
    return {"sweeps": list_sweeps()}


@router.get("/{sweep_id}")
async def get_sweep(sweep_id: str):
    try:
        return SweepManifest.load(sweep_id).doc
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")


@router.delete("/{sweep_id}")
async def remove_sweep(sweep_id: str):
    if not delete_sweep(sweep_id):
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")
    p = sweep_runner.cells_path(sweep_id)
    if p.exists():
        p.unlink()
    return {"deleted": sweep_id}


@router.post("/{sweep_id}/cancel")
async def cancel(sweep_id: str):
    try:
        m = SweepManifest.load(sweep_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")
    sweep_runner.cancel_sweep(m)
    return m.doc


@router.post("/{sweep_id}/tiles/{tile_id}/run")
async def run_tile(request: Request, sweep_id: str, tile_id: str, body: RunTileRequest):
    """Run one tile, streaming its SSE events.

    Identical transport to the single-AOI endpoint, deliberately: the frontend's
    existing event→log-line mapping works on a tile without modification, and
    aborting the fetch really does stop the Anthropic calls.
    """
    try:
        manifest = SweepManifest.load(sweep_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")
    if manifest.tile(tile_id) is None:
        raise HTTPException(status_code=404, detail=f"No tile {tile_id} in {sweep_id}")

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(payload: Dict[str, Any]):
        await queue.put(payload)

    async def pipeline():
        try:
            await queue.put(
                {"event": "tile_started", "sweep_id": sweep_id, "tile_id": tile_id}
            )
            outcome = await sweep_runner.run_tile(
                manifest, tile_id, body.anthropic_api_key, emit_fn=emit
            )
            await queue.put(
                {
                    "event": "tile_complete",
                    "sweep_id": sweep_id,
                    "tile_id": tile_id,
                    "status": outcome.status,
                    "cells_scored": outcome.cells_scored,
                    "reason": outcome.reason,
                    "totals": manifest.doc["totals"],
                    "remaining": len(manifest.pending_tiles()),
                }
            )
            # Normalize the region the moment the last tile lands, so the map is
            # correct without a second request the user has to know to make.
            if manifest.is_complete:
                merged = sweep_runner.finalize_sweep(manifest)
                manifest.finish()
                await queue.put(
                    {
                        "event": "sweep_complete",
                        "sweep_id": sweep_id,
                        "cells": len(merged),
                        "normalization_scope": "region",
                    }
                )
        except asyncio.CancelledError:
            logger.info("[%s:%s] Tile cancelled by client", sweep_id, tile_id)
            raise
        except Exception as exc:
            logger.exception("Tile run failed: %s", exc)
            await queue.put({"event": "error", "message": str(exc)})
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(pipeline())
        last = time.monotonic()
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(
                        queue.get(), timeout=DISCONNECT_POLL_SECONDS
                    )
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        logger.info(
                            "[%s:%s] Client disconnected — stopping tile",
                            sweep_id,
                            tile_id,
                        )
                        break
                    if time.monotonic() - last >= HEARTBEAT_SECONDS:
                        last = time.monotonic()
                        yield ": keepalive\n\n"
                    continue
                if ev is None:
                    break
                last = time.monotonic()
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            # Whatever happened, an in-flight tile must not stay `running` in a
            # manifest nobody is going to touch again.
            manifest.release_tile(tile_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{sweep_id}/finalize")
async def finalize(sweep_id: str, force_partial: bool = False):
    """Region-wide normalization. Normally automatic on the last tile."""
    try:
        m = SweepManifest.load(sweep_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")
    try:
        merged = sweep_runner.finalize_sweep(m, force_partial=force_partial)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    m.finish()
    return {"sweep_id": sweep_id, "cells": len(merged), "status": m.status}


@router.get("/{sweep_id}/cells")
async def sweep_cells(
    sweep_id: str, min_percentile: float = 0.0, limit: Optional[int] = None
):
    """Merged, region-normalized cells as GeoJSON, ranked best-first."""
    try:
        m = SweepManifest.load(sweep_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")
    cells = sweep_runner.sweep_cells(m, min_percentile=min_percentile, limit=limit)
    return {
        "type": "FeatureCollection",
        "sweep_id": sweep_id,
        "count": len(cells),
        "partial": not m.is_complete,
        "features": [
            {
                "type": "Feature",
                "geometry": c.pop("geometry"),
                "properties": c,
            }
            for c in cells
        ],
    }


@router.get("/{sweep_id}/cells.csv", response_class=PlainTextResponse)
async def sweep_cells_csv(
    sweep_id: str, min_percentile: float = 0.0, limit: Optional[int] = None
):
    """The ranked target list — §42's actual deliverable, in a form you can
    take into the field. Coordinates in DD, DMS and UTM with the zone named."""
    try:
        m = SweepManifest.load(sweep_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")
    cells = sweep_runner.sweep_cells(m, min_percentile=min_percentile, limit=limit)
    return PlainTextResponse(
        scored_cells_to_csv(cells),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="sweep_{sweep_id}.csv"'
        },
    )


class RefineRequest(BaseModel):
    fine_resolution_m: int
    top_n: int = 10
    min_percentile: float = 0.0
    target_mineral: Optional[str] = None
    confirm_large: bool = False


@router.post("/{sweep_id}/refine")
async def refine(sweep_id: str, body: RefineRequest):
    """Create a finer sweep over the best cells of this one (§41.1).

    Genuine re-analysis at a finer resolution, not interpolation: the coarse
    cells' children are scored on their own evidence. Distinct from
    ``grid.interpolate_to_fine_grid``, which IDW-downscales for display and
    invents nothing.
    """
    try:
        coarse = SweepManifest.load(sweep_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No sweep {sweep_id}")

    top = sweep_runner.sweep_cells(
        coarse, min_percentile=body.min_percentile, limit=body.top_n
    )
    if not top:
        raise HTTPException(status_code=400, detail="No cells to refine")

    try:
        tiles = refine_tiles([c["cell_id"] for c in top], body.fine_resolution_m)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if len(tiles) > MAX_TILES_WITHOUT_CONFIRMATION and not body.confirm_large:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Refining {len(top)} cells to {body.fine_resolution_m} m needs "
                f"{len(tiles)} tiles. Re-submit with confirm_large=true."
            ),
        )

    inputs = coarse.doc["inputs"]
    from shapely.geometry import mapping
    from shapely.ops import unary_union

    region = mapping(unary_union([t.core_polygon() for t in tiles]))
    m = sweep_runner.create_sweep(
        region_geojson=region,
        target_mineral=body.target_mineral or inputs["target_mineral"],
        resolution_m=body.fine_resolution_m,
        agent_ids=inputs.get("enabled_agents"),
        weights=inputs.get("weights"),
        corridor_note=f"refined from sweep {sweep_id} (top {len(top)} cells)",
    )
    m.set_inputs(refined_from=sweep_id, refined_from_cells=[c["cell_id"] for c in top])
    m.write()
    return m.doc


@router.get("/{sweep_id}/diff/{other_id}")
async def diff(sweep_id: str, other_id: str, noise_floor: Optional[float] = None):
    """Which cells moved between two sweeps, and by how much (§41.2).

    ``noise_floor`` is the measured absolute-score delta below which a change is
    indistinguishable from LLM nondeterminism. Omit it and the response says so
    rather than implying every delta is real.
    """
    try:
        a = SweepManifest.load(sweep_id)
        b = SweepManifest.load(other_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return diff_sweeps(a, b, noise_floor=noise_floor)
