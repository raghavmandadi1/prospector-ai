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
from app.scoring.engine import synthesize
from app.scoring.grid import generate_grid
from app.scoring.weights import DEFAULT_WEIGHTS
from app.config import settings

logger = logging.getLogger(__name__)

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

            # 1. Generate grid cells for the AOI
            resolution_m = config.get("resolution_m", 1000)
            grid_cells = generate_grid(aoi_geojson, resolution_m)
            logger.info(f"[{job_id}] Generated {len(grid_cells)} grid cells at {resolution_m}m")

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

            # 4. Synthesize scores
            weights = config.get("weights", DEFAULT_WEIGHTS.get(target_mineral, {}))
            scored_cells = synthesize(agent_results, grid_cells, weights, config)

            # 5. Build final output
            final_scores = {
                "scored_cells": [cell.model_dump() for cell in scored_cells],
                "cell_count": len(scored_cells),
                "target_mineral": target_mineral,
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

        TODO: Implement actual spatial queries per domain.
        For now, returns a stub with grid cells so agents can produce
        scores during development.
        """
        return {
            "grid_cells": [cell.model_dump() if hasattr(cell, "model_dump") else cell.__dict__ for cell in grid_cells],
            "aoi_geojson": aoi_geojson,
            "geology_units": [],
            "fault_traces": [],
            "known_deposits": [],
            "geochemical_samples": [],
            "historic_mines": [],
        }
