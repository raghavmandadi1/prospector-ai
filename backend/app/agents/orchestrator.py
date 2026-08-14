"""
OrchestratorAgent coordinates the full analysis pipeline:
1. Divides the AOI into a grid
2. Queries PostGIS for domain-specific spatial context
3. Fans out to all specialist agents in parallel
4. Passes results to the ScoringEngine
5. Persists final scores to the AnalysisJob record
6. Emits SSE progress events via Redis pub/sub (or in-process callback in dev mode)
"""
import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from app.agents.base_agent import KNOWLEDGE_DIR, MODEL_NAME, BaseAgent
from app.agents.lithology_agent import LithologyAgent
from app.agents.structure_agent import StructureAgent
from app.agents.proximity_agent import ProximityAgent
from app.agents.geochemistry_agent import GeochemistryAgent
from app.agents.remote_sensing_agent import RemoteSensingAgent
from app.agents.historical_agent import HistoricalAgent
from app.models.agent_result import AgentResult
from app.runs.record import RunRecorder, provenance_block
from app.scoring.engine import synthesize, normalize_relative
from app.scoring.grid import (
    cells_from_ids,
    coarsen,
    generate_grid,
    interpolate_to_fine_grid,
    snap_to_ladder,
)
from app.scoring.weights import DEFAULT_WEIGHTS
from app.config import settings

logger = logging.getLogger(__name__)

# Upper bound on cells sent to the LLM agents. Above this the analysis grid
# is coarsened (doubling resolution) and results are interpolated back down
# to the requested display resolution.
MAX_LLM_CELLS = 150

# Upper bound on display cells returned to the frontend. Protects against a
# 100 m grid over a very large AOI producing a multi-MB GeoJSON payload.
MAX_DISPLAY_CELLS = 12000

# Registry of agent_id → class (used to instantiate only enabled agents)
AGENT_CLASSES = {
    "lithology": LithologyAgent,
    "structure": StructureAgent,
    "proximity": ProximityAgent,
    "geochemistry": GeochemistryAgent,
    "remote_sensing": RemoteSensingAgent,
    "historical": HistoricalAgent,
}


def _read_knowledge(knowledge_file: str) -> Optional[str]:
    """Read a knowledge file by its `<domain>/<name>.md` label, for hashing."""
    try:
        return (KNOWLEDGE_DIR / knowledge_file).read_text(encoding="utf-8")
    except Exception:
        return None


def _aoi_area_km2(aoi_geojson: Dict[str, Any]) -> Optional[float]:
    try:
        import pyproj
        from shapely.geometry import shape

        geom = aoi_geojson
        if geom.get("type") == "FeatureCollection":
            geom = geom["features"][0]["geometry"]
        elif geom.get("type") == "Feature":
            geom = geom["geometry"]
        area, _ = pyproj.Geod(ellps="WGS84").geometry_area_perimeter(shape(geom))
        return round(abs(area) / 1_000_000, 2)
    except Exception:
        return None


class OrchestratorAgent:
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    def _build_agents(self, enabled_agents: Optional[List[str]] = None) -> List[BaseAgent]:
        """Instantiate only the requested agents, passing through the API key."""
        ids = enabled_agents or list(AGENT_CLASSES.keys())
        agents = []
        for aid in ids:
            cls = AGENT_CLASSES.get(aid)
            if cls:
                agents.append(cls(api_key=self._api_key))
            else:
                logger.warning(f"Unknown agent id: {aid}")
        return agents

    async def run_analysis(
        self,
        job_id: str,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        config: Dict[str, Any],
        emit_fn: Optional[Callable] = None,
    ) -> tuple:
        """
        Full analysis pipeline.

        Args:
            job_id: Unique job identifier
            aoi_geojson: GeoJSON FeatureCollection with AOI polygon
            target_mineral: Target mineral to prospect for
            config: Analysis config (resolution_m, weights, enabled_agents)
            emit_fn: Optional async callback for SSE events.
                      If None, uses Redis pub/sub.
        """
        # Choose emit strategy
        if emit_fn is None:
            emit_fn = await self._make_redis_emitter(job_id)

        recorder = RunRecorder(run_id=job_id)
        # Agents read run_id off the config when writing cache rows, so cached
        # scores stay traceable to the run that produced them.
        config = {**config, "run_id": job_id}
        started_at = time.monotonic()
        agent_results: List[AgentResult] = []
        # Held outside the try so the run record can report spatial-context
        # availability even when the run dies before the query runs.
        spatial_ctx_ref: Dict[str, Any] = {}

        try:
            await emit_fn({"event": "started", "job_id": job_id})

            # 1. Generate grid cells for the AOI.
            # Two-level grid: the LLM agents score a coarse analysis grid
            # (≤ MAX_LLM_CELLS); if the user requested a finer display
            # resolution (e.g. 100 m), coarse scores are IDW-interpolated
            # down to it after synthesis.
            # Snap to the fixed ladder up front: the grid only nests (and so
            # only caches) on ladder steps, and coarsening walks the same ladder.
            requested_resolution_m = snap_to_ladder(config.get("resolution_m", 1000))
            resolution_m = requested_resolution_m

            # --- sweep tile seam ---------------------------------------------
            # A sweep tile arrives with its cell set already decided by index
            # arithmetic, and must NOT be re-derived from a polygon: handing a
            # tile-shaped polygon to generate_grid returns more cells than the
            # block contains (measured 110 for 10x10, 156 for a 12x12 halo), and
            # 156 trips the MAX_LLM_CELLS coarsening below — halving the
            # resolution of the whole sweep with only an INFO log to show for it.
            #
            # So when a tile is supplied the grid is rebuilt from its ids and
            # both coarsening loops are skipped entirely. The tiler already
            # guaranteed the tile fits.
            tile = config.get("tile")
            if tile:
                grid_cells = cells_from_ids(tile["core_cell_ids"])
                analysis_resolution_m = (
                    grid_cells[0].resolution_m if grid_cells else resolution_m
                )
                resolution_m = analysis_resolution_m
                display_cells = grid_cells
                logger.info(
                    "[%s] Tile %s: %d core cells at %d m, %d halo cells as context "
                    "(grid generation and coarsening bypassed)",
                    job_id,
                    tile.get("tile_id"),
                    len(grid_cells),
                    analysis_resolution_m,
                    len(tile.get("halo_cell_ids") or []),
                )
            else:
                # Cap the display grid size (a 125 m grid over a large AOI can
                # produce tens of thousands of polygons)
                display_cells = generate_grid(aoi_geojson, resolution_m)
                while (
                    len(display_cells) > MAX_DISPLAY_CELLS
                    and coarsen(resolution_m) != resolution_m
                ):
                    resolution_m = coarsen(resolution_m)
                    display_cells = generate_grid(aoi_geojson, resolution_m)
                if resolution_m != requested_resolution_m:
                    logger.info(
                        f"[{job_id}] Display resolution coarsened to "
                        f"{resolution_m}m to stay under {MAX_DISPLAY_CELLS} cells"
                    )

                analysis_resolution_m = resolution_m
                grid_cells = generate_grid(aoi_geojson, analysis_resolution_m)
                while (
                    len(grid_cells) > MAX_LLM_CELLS
                    and coarsen(analysis_resolution_m) != analysis_resolution_m
                ):
                    analysis_resolution_m = coarsen(analysis_resolution_m)
                    grid_cells = generate_grid(aoi_geojson, analysis_resolution_m)
            if analysis_resolution_m != resolution_m:
                logger.info(
                    f"[{job_id}] Display resolution {resolution_m}m → analysis "
                    f"grid coarsened to {analysis_resolution_m}m "
                    f"({len(grid_cells)} cells for LLM scoring)"
                )
                await emit_fn({
                    "event": "grid_info",
                    "display_resolution_m": resolution_m,
                    "analysis_resolution_m": analysis_resolution_m,
                    "analysis_cell_count": len(grid_cells),
                })
            logger.info(f"[{job_id}] Generated {len(grid_cells)} grid cells at {analysis_resolution_m}m")

            recorder.set_inputs(
                aoi_geojson=aoi_geojson,
                aoi_area_km2=_aoi_area_km2(aoi_geojson),
                target_mineral=target_mineral,
                weights=config.get("weights"),
                enabled_agents=config.get("enabled_agents"),
                requested_resolution_m=requested_resolution_m,
                display_resolution_m=resolution_m,
                analysis_resolution_m=analysis_resolution_m,
                use_cache=config.get("use_cache", True),
            )

            # 2. Build spatial context for each agent domain (local files, then
            #    PostGIS on top where it is reachable)
            #
            # A tile's halo cells go through the SAME context build as its core,
            # so `cell_facts` covers them and the prompt can show a neighbour's
            # real geology rather than a placeholder. They are then handed over
            # separately as `context_cells`, which is what keeps them out of the
            # batching, the scoring and the cache (see base_agent.neighbour_label
            # and _cell_context).
            halo_cells = cells_from_ids(tile["halo_cell_ids"]) if tile else []
            spatial_context = await self._build_spatial_context(
                aoi_geojson, grid_cells + halo_cells, target_mineral
            )
            if halo_cells:
                # CRITICAL. _build_spatial_context puts everything it was given
                # into spatial_context["grid_cells"], and BaseAgent.run() reads
                # its work list from exactly that key — so leaving the halo in it
                # would have every agent score the halo as if it were core, which
                # is the one thing this whole design exists to prevent. Put the
                # core back, and hand the halo over under its own key.
                spatial_context["grid_cells"] = [c.model_dump() for c in grid_cells]
                spatial_context["context_cells"] = [c.model_dump() for c in halo_cells]

                # Coverage must describe the cells that were SCORED, not the
                # cells that were looked up. Counting the halo here would report
                # a 100-cell tile as 144 and quietly inflate every coverage ratio
                # in the run record by ~44%.
                core_ids = {c.cell_id for c in grid_cells}
                facts = spatial_context.get("cell_facts") or {}
                core_facts = {k: v for k, v in facts.items() if k in core_ids}
                cov = spatial_context.setdefault("coverage", {})
                cov["cells_total"] = len(core_facts)
                cov["cells_with_geology"] = sum(1 for f in core_facts.values() if f.get("geology"))
                cov["cells_with_structures"] = sum(1 for f in core_facts.values() if f.get("structures"))
                cov["cells_with_wofe"] = sum(1 for f in core_facts.values() if f.get("wofe"))
                cov["cells_with_occurrences"] = sum(
                    1 for f in core_facts.values() if (f.get("occurrences") or {}).get("nearest")
                )
                cov["context_cells"] = len(halo_cells)
            spatial_ctx_ref = spatial_context

            # Report what the agents will actually see. A run where every count
            # is zero and `sources` is empty is a run scored entirely from LLM
            # regional priors, and it should be obvious in the log that it was.
            cells_with_facts = sum(
                1 for f in (spatial_context.get("cell_facts") or {}).values() if f
            )
            await emit_fn({
                "event": "spatial_context",
                "error": spatial_context.get("_error"),
                "sources": spatial_context.get("context_sources") or [],
                "cells_with_facts": cells_with_facts,
                "cells_total": len(grid_cells),
                # Which evidence actually covers THIS polygon, as opposed to
                # which artifacts happen to be installed. The 1:24k geology is a
                # quadrangle mosaic with real gaps, so those two are different
                # claims and only this one is about the run in front of you.
                "coverage": spatial_context.get("coverage") or {},
                "counts": {
                    k: len(v)
                    for k, v in spatial_context.items()
                    if k != "grid_cells" and isinstance(v, list)
                },
            })

            # 3. Fan out to enabled agents in parallel
            enabled_agents = config.get("enabled_agents", None)
            agents = self._build_agents(enabled_agents)
            logger.info(f"[{job_id}] Running {len(agents)} agents: {[a.agent_id for a in agents]}")

            agent_tasks = [
                self._run_agent_with_progress(emit_fn, agent, aoi_geojson, target_mineral, spatial_context, config)
                for agent in agents
            ]

            agent_results = list(await asyncio.gather(*agent_tasks))
            logger.info(f"[{job_id}] All agents completed")

            # Job-level token/cost rollup. Emitted before synthesis so the UI
            # ledger settles while the (potentially slow) scoring runs.
            usage_totals = self._roll_up_usage(agent_results)
            logger.info(
                f"[{job_id}] Usage: {usage_totals['input_tokens']} in / "
                f"{usage_totals['output_tokens']} out over "
                f"{usage_totals['llm_calls']} calls "
                f"(~${usage_totals['est_cost_usd']:.4f})"
            )
            await emit_fn({"event": "usage", "job_id": job_id, **usage_totals})

            # 4. Synthesize scores on the analysis grid
            weights = config.get("weights", DEFAULT_WEIGHTS.get(target_mineral, {}))
            scored_cells = synthesize(agent_results, grid_cells, weights, config)

            # 4b. Interpolate down to the requested display resolution
            if analysis_resolution_m != resolution_m:
                scored_cells = interpolate_to_fine_grid(
                    scored_cells,
                    aoi_geojson,
                    fine_resolution_m=float(resolution_m),
                    coarse_resolution_m=analysis_resolution_m,
                )
                logger.info(
                    f"[{job_id}] Interpolated to {len(scored_cells)} cells at {resolution_m}m"
                )

            # 4c. AOI-relative normalization — shading answers "best spots in
            # THIS area", not "score vs the rest of the world"
            #
            # A sweep tile is normalized here too, deliberately, even though the
            # sweep manager will re-normalize the whole region afterwards and
            # overwrite these values with scope="region".
            #
            # The alternative — emitting tiles with relative_score/percentile/tier
            # left null, as §39 proposes — is worse than it looks. Three
            # downstream consumers assume those fields are populated and every
            # one of them fails silently rather than loudly: the map's shading
            # expression coalesces relative_score to the absolute score, so a
            # null renders as a plausible map that is answering a different
            # question; and an existing integration test asserts every cell has a
            # real tier. Normalizing twice is cheap, and it means no cell ever
            # exists in a state where its scope is unknown.
            scored_cells = normalize_relative(scored_cells, scope="aoi")

            # 4d. Novelty — is a high score finding something, or re-finding it?
            self._attach_novelty(scored_cells, spatial_context)

            # 5. Build final output
            final_scores = {
                "scored_cells": [cell.model_dump() for cell in scored_cells],
                "cell_count": len(scored_cells),
                "target_mineral": target_mineral,
                "display_resolution_m": resolution_m,
                "analysis_resolution_m": analysis_resolution_m,
            }

            # raw_batches is megabytes of LLM text bound for the run record,
            # not for the browser.
            agent_results_dict = {
                r.agent_id: r.model_dump(exclude={"raw_batches"})
                for r in agent_results
            }

            recorder.set_composite_cells(scored_cells)
            recorder.set_status("completed")

            await emit_fn({"event": "job_complete", "job_id": job_id, "status": "completed"})

            return final_scores, agent_results_dict

        except asyncio.CancelledError:
            # User stopped the run. Do not emit — the client that would have
            # received the event is the one that just disconnected.
            logger.info(f"[{job_id}] Analysis cancelled")
            recorder.set_status("cancelled")
            raise
        except Exception as exc:
            logger.exception(f"[{job_id}] Orchestrator failed: {exc}")
            recorder.set_status("failed", error=f"{type(exc).__name__}: {exc}")
            await emit_fn({"event": "error", "message": str(exc)})
            raise
        finally:
            # A failed or cancelled run is diagnostically the most valuable
            # kind, so the record is written on every path out of here.
            self._finalize_record(
                recorder, agent_results, spatial_ctx_ref, started_at
            )

    @staticmethod
    def _attach_novelty(
        scored_cells: List, spatial_context: Dict[str, Any]
    ) -> None:
        """Annotate each scored cell with its distance to the nearest record.

        The reason this matters is the whole point of putting known workings on
        the map: when a cell scores high, the immediate question is *is this
        finding something, or re-finding something?* A hot cell sitting on three
        recorded workings is the model confirming known ground. A hot cell with
        nothing recorded within two miles is a lead. On a plain choropleth those
        two look identical and they mean opposite things.

        Cells interpolated down to a finer display grid inherit from their
        analysis parent — the occurrence facts were computed on the coarse cell,
        and inventing a finer distance we never measured would be worse than
        inheriting a coarse one.

        A cell left with ``novelty is None`` is *unknown*, not novel. The UI must
        render nothing in that case: with no occurrence extract built, calling
        every cell a lead would turn missing data into a prospecting signal.
        """
        novelty = spatial_context.get("cell_novelty") or {}
        if not novelty:
            return
        annotated = 0
        for cell in scored_cells:
            info = novelty.get(cell.cell_id)
            if info is None:
                parent = getattr(cell, "parent_cell_id", None)
                if parent:
                    info = novelty.get(parent)
            if not info:
                continue
            cell.nearest_occurrence_km = info.get("nearest_occurrence_km")
            cell.nearest_occurrence_name = info.get("nearest_occurrence_name")
            cell.novelty = info.get("novelty")
            annotated += 1
        if annotated:
            counts: Dict[str, int] = {}
            for cell in scored_cells:
                key = getattr(cell, "novelty", None) or "unknown"
                counts[key] = counts.get(key, 0) + 1
            logger.info(
                "Novelty: %s (of %d cells)",
                ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
                len(scored_cells),
            )

    @staticmethod
    def _finalize_record(
        recorder: RunRecorder,
        agent_results: List[AgentResult],
        spatial_context: Dict[str, Any],
        started_at: float,
    ) -> None:
        """Fill provenance/timings/cache and write the record. Never raises."""
        try:
            knowledge_files: Dict[str, Optional[str]] = {}
            ungrounded: List[str] = []
            hits = misses = 0
            for r in agent_results:
                if r.knowledge_file:
                    knowledge_files[r.knowledge_file] = _read_knowledge(r.knowledge_file)
                else:
                    ungrounded.append(r.agent_id)
                hits += r.cache_hits
                misses += r.cache_misses
                recorder.add_agent_result(r)

            counts = {
                k: len(v)
                for k, v in spatial_context.items()
                if k != "grid_cells" and isinstance(v, list)
            }
            recorder.set_provenance(
                provenance_block(
                    knowledge_files=knowledge_files,
                    agents_without_knowledge=ungrounded,
                    # "Available" means evidence actually reached the agents —
                    # not merely that a query did not raise. Per-cell facts count
                    # for this even when the AOI-wide lists are thin, because the
                    # per-cell block is now the primary evidence in every prompt.
                    spatial_context_available=(
                        spatial_context.get("_error") is None
                        and (
                            any(counts.values())
                            or any((spatial_context.get("cell_facts") or {}).values())
                        )
                    ),
                    model=MODEL_NAME,
                    context_sources=spatial_context.get("context_sources") or [],
                    pin_roles_active=spatial_context.get("roles_active") or {},
                )
            )
            recorder.set_timings(
                total_s=round(time.monotonic() - started_at, 2),
                per_agent_s={
                    r.agent_id: round((r.usage.duration_ms if r.usage else 0) / 1000, 2)
                    for r in agent_results
                },
            )
            recorder.set_cache_stats(hits, misses)
            # Which evidence covered this AOI. The benchmark needs it to explain
            # why two runs of nominally the same configuration differ, and it is
            # the difference between "the model was wrong" and "the model had
            # nothing to go on here".
            recorder.set_inputs(
                spatial_coverage=spatial_context.get("coverage") or {}
            )
            recorder.write()
        except Exception as exc:  # pragma: no cover — bookkeeping only
            logger.warning(f"Run record finalization failed: {exc}")

    @staticmethod
    def _roll_up_usage(agent_results: List[AgentResult]) -> Dict[str, Any]:
        """Aggregate per-agent token usage into job totals + a per-agent map."""
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "llm_calls": 0,
            "est_cost_usd": 0.0,
        }
        by_agent: Dict[str, Any] = {}
        for r in agent_results:
            if not r.usage:
                continue
            u = r.usage.model_dump()
            by_agent[r.agent_id] = u
            for k in totals:
                totals[k] += u.get(k, 0)
        totals["est_cost_usd"] = round(totals["est_cost_usd"], 6)
        totals["by_agent"] = by_agent
        # Agents that ran with system=None contribute ungrounded scores at
        # full weight. Surfaced here so the UI can warn before results land.
        totals["ungrounded_agents"] = [
            r.agent_id for r in agent_results if r.knowledge_file is None
        ]
        return totals

    async def _make_redis_emitter(self, job_id: str) -> Callable:
        """Create an emit function backed by Redis pub/sub."""
        import redis.asyncio as aioredis
        red = aioredis.from_url(settings.redis_url)
        channel = f"job:{job_id}:events"

        async def emit(payload: Dict):
            await red.publish(channel, json.dumps(payload))

        return emit

    async def _run_agent_with_progress(
        self,
        emit_fn: Callable,
        agent: BaseAgent,
        aoi_geojson: Dict,
        target_mineral: str,
        spatial_context: Dict,
        config: Dict,
    ) -> AgentResult:
        """Wrapper that emits SSE events before and after each agent run.

        emit_fn is threaded into agent.run() so the agent can emit its own
        per-batch telemetry; without it the stream is silent for the whole
        multi-minute span between agent_started and agent_complete.
        """
        await emit_fn({"event": "agent_started", "agent_id": agent.agent_id})
        result = await agent.run(
            aoi_geojson, target_mineral, spatial_context, config, emit_fn=emit_fn
        )
        scored = [c for c in result.scored_cells if c.confidence > 0]
        await emit_fn({
            "event": "agent_complete",
            "agent_id": agent.agent_id,
            "status": result.status,
            "cells_scored": len(scored),
            "cells_total": len(result.scored_cells),
            "knowledge_file": result.knowledge_file,
            "warnings": result.warnings,
            "usage": result.usage.model_dump() if result.usage else None,
        })
        return result

    async def _build_spatial_context(
        self,
        aoi_geojson: Dict[str, Any],
        grid_cells: List,
        target_mineral: str = "gold",
    ) -> Dict[str, Any]:
        """
        Everything the agents will know about this ground beyond their own prior.

        Two sources, in this order:

        1. **Local files** (`app.spatial.local_store`) — WA DNR mines and
           districts, the 1:24k surface geology, the OF-00-495 WofE grids, GNIS
           toponyms, imported field pins. This is the primary source and it works
           on every path, because it is `sqlite3` and `json` against files on
           disk.
        2. **PostGIS**, merged on top when it has rows. This is still the right
           home for ingested connector data in production, but it must not be the
           only source: under `DEV_MODE` the import on the second line of the
           block below raises `ModuleNotFoundError` (asyncpg is deliberately not
           in `requirements-dev.txt`), which is how every agent came to receive an
           empty dict on the path everyone actually runs.

        `_error` is set only when *both* sources came up empty, so it means what
        it says — the agents are scoring from model prior alone.
        """
        context: Dict[str, Any] = {
            "grid_cells": [cell.model_dump() if hasattr(cell, "model_dump") else cell.__dict__ for cell in grid_cells],
            "aoi_geojson": aoi_geojson,
            # Set when no evidence source produced anything, so the failure
            # reaches the run log instead of only the server's stderr. Agents
            # ignore this key.
            "_error": None,
            "geology_units": [],
            "fault_traces": [],
            "known_deposits": [],
            "geochemical_samples": [],
            "historic_mines": [],
            "cell_facts": {},
            "cell_novelty": {},
            "context_sources": [],
            "roles_active": {},
        }

        # --- 1. Local files -----------------------------------------------
        # Synchronous file and SQLite reads. Run off the event loop so a large
        # AOI's geometry work does not stall the SSE heartbeat — the dev path
        # streams events on the same task that would otherwise be blocked here.
        try:
            from app.spatial.local_store import build_local_context

            local = await asyncio.to_thread(
                build_local_context,
                aoi_geojson,
                context["grid_cells"],
                target_mineral,
            )
            context.update({k: v for k, v in local.items() if v or k in context})
        except Exception as exc:
            logger.warning("Local spatial context failed (%s)", exc, exc_info=True)
            context["local_error"] = f"{type(exc).__name__}: {exc}"

        # AOI bbox (expanded ~2 km so district-edge records are visible)
        bboxes = [c.bbox if hasattr(c, "bbox") else c.get("bbox") for c in grid_cells]
        if not bboxes:
            return context
        pad = 0.02
        min_lon = min(b[0] for b in bboxes) - pad
        min_lat = min(b[1] for b in bboxes) - pad
        max_lon = max(b[2] for b in bboxes) + pad
        max_lat = max(b[3] for b in bboxes) + pad

        # --- 2. PostGIS, merged on top ------------------------------------
        try:
            from sqlalchemy import select, func
            from app.db.session import AsyncSessionLocal
            from app.models.feature import Feature

            async with AsyncSessionLocal() as session:
                envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
                stmt = (
                    select(
                        Feature.name,
                        Feature.source_channel,
                        Feature.feature_type,
                        Feature.status,
                        Feature.deposit_type,
                        Feature.commodity_primary,
                        Feature.geologic_unit,
                        Feature.rock_type,
                        Feature.geochemical_values,
                        func.ST_X(func.ST_Centroid(Feature.geometry)).label("lon"),
                        func.ST_Y(func.ST_Centroid(Feature.geometry)).label("lat"),
                    )
                    .where(Feature.geometry.isnot(None))
                    .where(func.ST_Intersects(Feature.geometry, envelope))
                    .limit(1000)
                )
                rows = (await session.execute(stmt)).all()

            for row in rows:
                rec = {
                    "name": row.name,
                    "type": row.feature_type,
                    "status": row.status,
                    "deposit_type": row.deposit_type,
                    "commodity": row.commodity_primary,
                    "lon": round(row.lon, 5) if row.lon is not None else None,
                    "lat": round(row.lat, 5) if row.lat is not None else None,
                    "source": row.source_channel,
                }
                # Assay/grade values are first-class evidence — always include
                if row.geochemical_values:
                    rec["geochemical_values"] = row.geochemical_values

                channel = (row.source_channel or "").lower()
                ftype = (row.feature_type or "").lower()

                if channel == "macrostrat" or ftype in ("formation", "geology_unit"):
                    rec["geologic_unit"] = row.geologic_unit
                    rec["rock_type"] = row.rock_type
                    context["geology_units"].append(rec)
                elif channel == "usgs_ngdb" or ftype == "sample":
                    context["geochemical_samples"].append(rec)
                elif ftype == "fault" or "fault" in ftype:
                    context["fault_traces"].append(rec)
                else:
                    # MRDS deposits, MinDat localities, BLM claims, GLO patents
                    context["known_deposits"].append(rec)
                    if (row.status or "").lower() in ("historic", "past producer") or channel in ("usgs_mrds", "glo_records"):
                        context["historic_mines"].append(rec)

            logger.info(
                "Spatial context: %d deposits, %d historic, %d samples, "
                "%d geology units, %d faults",
                len(context["known_deposits"]),
                len(context["historic_mines"]),
                len(context["geochemical_samples"]),
                len(context["geology_units"]),
                len(context["fault_traces"]),
            )
        except Exception as exc:
            # Expected under DEV_MODE: `app.db.session` calls create_async_engine()
            # at module scope with a postgresql+asyncpg:// URL and asyncpg is not a
            # dev dependency. Not fatal, and no longer fatal to the *evidence* —
            # the local store above has already run.
            context["postgis_error"] = f"{type(exc).__name__}: {exc}"
            logger.info(
                "PostGIS spatial context unavailable (%s); using local files only",
                exc,
            )

        has_records = any(
            isinstance(v, list) and v
            for k, v in context.items()
            if k not in ("grid_cells",)
        ) or bool(context.get("cell_facts"))

        if not has_records:
            # Only now is the old warning accurate. Say which source failed, so
            # "no evidence" is actionable rather than a mystery.
            reason = context.get("local_error") or context.get("postgis_error") or (
                "no local artifacts built and no database"
            )
            context["_error"] = reason
            logger.warning(
                "No spatial evidence for this AOI (%s); agents will run on LLM "
                "regional knowledge only",
                reason,
            )

        return context
