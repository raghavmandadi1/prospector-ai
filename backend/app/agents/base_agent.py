"""
Base class for all GeoProspector specialist agents.

Each specialist agent:
1. Receives an AOI, target mineral, and spatial context queried from PostGIS
2. Builds a domain-specific prompt for the LLM
3. Calls the Anthropic API
4. Parses the LLM response into structured ScoredCell objects
5. Returns an AgentResult

To add a new agent:
    1. Create a new file in app/agents/
    2. Subclass BaseAgent
    3. Implement build_prompt() and parse_llm_response()
    4. Register it in OrchestratorAgent.agents
"""
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

from app.config import settings
from app.models.agent_result import AgentResult, ScoredCell

logger = logging.getLogger(__name__)

# Root directory for agent knowledge base files
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


class BaseAgent(ABC):
    agent_id: str = "base"
    agent_name: str = "Base Agent"

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or settings.anthropic_api_key
        self._client = anthropic.AsyncAnthropic(api_key=key)

    async def run(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
        config: Dict[str, Any],
    ) -> AgentResult:
        """
        Main entry point called by the orchestrator.

        Steps:
        1. Build a domain-specific prompt with the given context
        2. Call the LLM
        3. Parse the response into ScoredCell objects
        4. Return AgentResult
        """
        grid_cells = spatial_context.get("grid_cells", [])
        try:
            prompt = self.build_prompt(aoi_geojson, target_mineral, spatial_context)
            llm_response = await self.call_llm(prompt)
            scored_cells = self.parse_llm_response(llm_response, grid_cells)
            return AgentResult(
                agent_id=self.agent_id,
                status="completed",
                scored_cells=scored_cells,
                agent_notes=llm_response[:500] if llm_response else None,
            )
        except Exception as exc:
            logger.exception(f"Agent {self.agent_id} failed: {exc}")
            return AgentResult(
                agent_id=self.agent_id,
                status="failed",
                warnings=[str(exc)],
            )

    @abstractmethod
    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        """
        Build the LLM prompt for this specialist domain.
        Should include:
        - System context explaining the agent's role
        - Structured spatial context (nearby features, geology, etc.)
        - Instructions for response format (JSON with scored cells)
        """
        raise NotImplementedError

    def load_knowledge(self, domain: str, target_mineral: str) -> Optional[str]:
        """
        Load a domain knowledge markdown file for the given mineral.

        Looks for: knowledge/<domain>/<mineral>.md
        Falls back to: knowledge/<domain>/default.md
        Returns None if no knowledge file exists.
        """
        mineral_key = target_mineral.lower().replace(" ", "_")
        knowledge_file = KNOWLEDGE_DIR / domain / f"{mineral_key}.md"
        if not knowledge_file.exists():
            knowledge_file = KNOWLEDGE_DIR / domain / "default.md"
        if not knowledge_file.exists():
            logger.info(
                f"[{self.agent_id}] No knowledge file for {domain}/{mineral_key}"
            )
            return None
        content = knowledge_file.read_text(encoding="utf-8")
        logger.info(
            f"[{self.agent_id}] Loaded knowledge: {knowledge_file.name} "
            f"({len(content)} chars)"
        )
        return content

    async def call_llm(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> str:
        """
        Call the Anthropic API with the constructed prompt.
        Uses claude-sonnet-4-6 by default; override in subclass for lighter tasks.

        If system_prompt is provided, it is sent as the system message,
        which is the recommended place for domain knowledge context.
        """
        kwargs = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        message = await self._client.messages.create(**kwargs)
        return message.content[0].text

    @abstractmethod
    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
        """
        Parse the raw LLM text response into a list of ScoredCell objects.
        Should handle malformed JSON gracefully (return empty list + log warning).
        """
        raise NotImplementedError

    def _safe_parse_json(self, text: str) -> Optional[Any]:
        """Extract and parse the first JSON block from LLM output."""
        # Try to extract JSON from markdown code block
        import re
        match = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[{self.agent_id}] Failed to parse LLM JSON response")
            return None
