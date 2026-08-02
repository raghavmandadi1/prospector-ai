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
from typing import Any, Callable, Dict, List, Optional

from app.agents.base_agent import BaseAgent
from app.agents.lithology_agent import LithologyAgent
from app.agents.structure_agent import StructureAgent
from app.agents.proximity_agent import ProximityAgent
from app.agents.geochemistry_agent import GeochemistryAgent
from app.agents.remote_sensing_agent import RemoteSensingAgent
from app.agents.historical_agent import HistoricalAgent
from app.models.agent_result import AgentResult
from app.scoring.engine import synthesize, normalize_relative
from app.scoring.grid import generate_grid, interpolate_to_fine_grid
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

        try:
            await emit_fn({"event": "started", "job_id": job_id})

            # 1. Generate grid cells for the AOI.
            # Two-level grid: the LLM agents score a coarse analysis grid
            # (≤ MAX_LLM_CELLS); if the user requested a finer display
            # resolution (e.g. 100 m), coarse scores are IDW-interpolated
            # down to it after synthesis.
            resolution_m = float(config.get("resolution_m", 1000))

            # Cap the display grid size (a 100 m grid over a large AOI can
            # produce tens of thousands of polygons)
            display_cells = generate_grid(aoi_geojson, resolution_m)
            while len(display_cells) > MAX_DISPLAY_CELLS:
                resolution_m *= 2
                display_cells = generate_grid(aoi_geojson, resolution_m)
            if resolution_m != float(config.get("resolution_m", 1000)):
                logger.info(
                    f"[{job_id}] Display resolution coarsened to "
                    f"{resolution_m:.0f}m to stay under {MAX_DISPLAY_CELLS} cells"
                )

            analysis_resolution_m = float(resolution_m)
            grid_cells = generate_grid(aoi_geojson, analysis_resolution_m)
            while len(grid_cells) > MAX_LLM_CELLS:
                analysis_resolution_m *= 2
                grid_cells = generate_grid(aoi_geojson, analysis_resolution_m)
            if analysis_resolution_m != resolution_m:
                logger.info(
                    f"[{job_id}] Display resolution {resolution_m}m → analysis "
                    f"grid coarsened to {analysis_resolution_m:.0f}m "
                    f"({len(grid_cells)} cells for LLM scoring)"
                )
                await emit_fn({
                    "event": "grid_info",
                    "display_resolution_m": resolution_m,
                    "analysis_resolution_m": analysis_resolution_m,
                    "analysis_cell_count": len(grid_cells),
                })
            logger.info(f"[{job_id}] Generated {len(grid_cells)} grid cells at {analysis_resolution_m:.0f}m")

            # 2. Build spatial context for each agent domain (PostGIS queries)
            spatial_context = await self._build_spatial_context(aoi_geojson, grid_cells)

            # 3. Fan out to enabled agents in parallel
            enabled_agents = config.get("enabled_agents", None)
            agents = self._build_agents(enabled_agents)
            logger.info(f"[{job_id}] Running {len(agents)} agents: {[a.agent_id for a in agents]}")

            agent_tasks = [
                self._run_agent_with_progress(emit_fn, agent, aoi_geojson, target_mineral, spatial_context, config)
                for agent in agents
            ]

            agent_results: List[AgentResult] = await asyncio.gather(*agent_tasks)
            logger.info(f"[{job_id}] All agents completed")

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
            scored_cells = normalize_relative(scored_cells)

            # 5. Build final output
            final_scores = {
                "scored_cells": [cell.model_dump() for cell in scored_cells],
                "cell_count": len(scored_cells),
                "target_mineral": target_mineral,
                "display_resolution_m": resolution_m,
                "analysis_resolution_m": analysis_resolution_m,
            }

            agent_results_dict = {r.agent_id: r.model_dump() for r in agent_results}

            await emit_fn({"event": "job_complete", "job_id": job_id, "status": "completed"})

            return final_scores, agent_results_dict

        except Exception as exc:
            logger.exception(f"[{job_id}] Orchestrator failed: {exc}")
            await emit_fn({"event": "error", "message": str(exc)})
            raise

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
        """Wrapper that emits SSE events before and after each agent run."""
        await emit_fn({"event": "agent_started", "agent_id": agent.agent_id})
        result = await agent.run(aoi_geojson, target_mineral, spatial_context, config)
        await emit_fn(
            {"event": "agent_complete", "agent_id": agent.agent_id, "status": result.status}
        )
        return result

    async def _build_spatial_context(
        self,
        aoi_geojson: Dict[str, Any],
        grid_cells: List,
    ) -> Dict[str, Any]:
        """
        Query PostGIS for features relevant to each agent domain.

        Best-effort: if the database is unavailable (e.g. dev mode without
        docker services), agents fall back to LLM regional knowledge with
        empty context lists.
        """
        context: Dict[str, Any] = {
            "grid_cells": [cell.model_dump() if hasattr(cell, "model_dump") else cell.__dict__ for cell in grid_cells],
            "aoi_geojson": aoi_geojson,
            "geology_units": [],
            "fault_traces": [],
            "known_deposits": [],
            "geochemical_samples": [],
            "historic_mines": [],
        }

        # AOI bbox (expanded ~2 km so district-edge records are visible)
        bboxes = [c.bbox if hasattr(c, "bbox") else c.get("bbox") for c in grid_cells]
        if not bboxes:
            return context
        pad = 0.02
        min_lon = min(b[0] for b in bboxes) - pad
        min_lat = min(b[1] for b in bboxes) - pad
        max_lon = max(b[2] for b in bboxes) + pad
        max_lat = max(b[3] for b in bboxes) + pad

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
            logger.warning(
                f"Spatial context query failed ({exc}); agents will run on "
                f"LLM regional knowledge only"
            )

        return context
