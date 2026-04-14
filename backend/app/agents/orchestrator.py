"""
OrchestratorAgent coordinates the full analysis pipeline:
1. Divides the AOI into a grid
2. Queries PostGIS for domain-specific spatial context
3. Fans out to all specialist agents in parallel
4. Passes results to the ScoringEngine
5. Persists final scores to the AnalysisJob record
6. Emits SSE progress events via Redis pub/sub
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

import redis.asyncio as aioredis

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


class OrchestratorAgent:
    def __init__(self):
        self.agents: List[BaseAgent] = [
            LithologyAgent(),
            StructureAgent(),
            ProximityAgent(),
            GeochemistryAgent(),
            RemoteSensingAgent(),
            HistoricalAgent(),
        ]

    async def run_analysis(
        self,
        job_id: str,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Full analysis pipeline. Called by the Celery worker.

        Returns the final_scores dict to be persisted to the DB.
        """
        red = aioredis.from_url(settings.redis_url)

        try:
            await self._emit(red, job_id, {"event": "started", "job_id": job_id})

            # 1. Generate grid cells for the AOI
            resolution_m = config.get("resolution_m", 1000)
            grid_cells = generate_grid(aoi_geojson, resolution_m)
            logger.info(f"[{job_id}] Generated {len(grid_cells)} grid cells at {resolution_m}m")

            # 2. Build spatial context for each agent domain (PostGIS queries)
            spatial_context = await self._build_spatial_context(aoi_geojson, grid_cells)

            # 3. Fan out to all enabled agents in parallel
            enabled_agents = config.get("enabled_agents", None)  # None = all
            agent_tasks = []
            for agent in self.agents:
                if enabled_agents and agent.agent_id not in enabled_agents:
                    continue
                agent_tasks.append(
                    self._run_agent_with_progress(red, job_id, agent, aoi_geojson, target_mineral, spatial_context, config)
                )

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

            await self._emit(red, job_id, {"event": "job_complete", "job_id": job_id, "status": "completed"})

            return final_scores, agent_results_dict

        except Exception as exc:
            logger.exception(f"[{job_id}] Orchestrator failed: {exc}")
            await self._emit(red, job_id, {"event": "error", "message": str(exc)})
            raise
        finally:
            await red.aclose()

    async def _run_agent_with_progress(
        self,
        red: aioredis.Redis,
        job_id: str,
        agent: BaseAgent,
        aoi_geojson: Dict,
        target_mineral: str,
        spatial_context: Dict,
        config: Dict,
    ) -> AgentResult:
        """Wrapper that emits SSE events before and after each agent run."""
        await self._emit(red, job_id, {"event": "agent_started", "agent_id": agent.agent_id})
        result = await agent.run(aoi_geojson, target_mineral, spatial_context, config)
        await self._emit(
            red, job_id,
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

        TODO: Implement actual spatial queries per domain:
        - lithology_agent: query geology units intersecting AOI
        - structure_agent: query fault traces within buffer
        - proximity_agent: query known mines/deposits within AOI
        - geochemistry_agent: query geochemical samples within AOI
        - remote_sensing_agent: fetch raster tile references
        - historical_agent: query historic mining records

        For now, returns a stub with grid cells so agents can produce
        placeholder scores during development.
        """
        return {
            "grid_cells": [cell.model_dump() if hasattr(cell, "model_dump") else cell.__dict__ for cell in grid_cells],
            "aoi_geojson": aoi_geojson,
            # Domain-specific context — to be populated:
            "geology_units": [],
            "fault_traces": [],
            "known_deposits": [],
            "geochemical_samples": [],
            "historic_mines": [],
        }

    @staticmethod
    async def _emit(red: aioredis.Redis, job_id: str, payload: Dict):
        """Publish a progress event to the Redis pub/sub channel for this job."""
        channel = f"job:{job_id}:events"
        await red.publish(channel, json.dumps(payload))
