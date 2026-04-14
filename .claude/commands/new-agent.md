# /new-agent — GeoProspector Specialist Agent Scaffolder

You are a code generation agent for GeoProspector. When invoked, you scaffold a complete
new specialist agent from the project's established pattern.

The user provides the agent name and optionally a description in `$ARGUMENTS`.
Example: `/new-agent water_chemistry — Analyzes USGS NWIS stream chemistry for dissolved metal anomalies`

If `$ARGUMENTS` is empty, ask the user for:
1. Agent name (snake_case, e.g. `water_chemistry`)
2. Domain description (one sentence)
3. Primary data source(s) it will consume
4. Does it need LLM reasoning, or is it pure spatial scoring?

---

## What to Generate

### File to create: `backend/app/agents/<name>_agent.py`

Generate a complete, working Python file using the template below. Fill in all
`[PLACEHOLDER]` sections based on the agent name and description provided.

```python
"""
[AGENT_NAME_TITLE] Agent for GeoProspector.

Domain: [DOMAIN_DESCRIPTION]

Data consumed:
  - [DATA_SOURCE_1]
  - [DATA_SOURCE_2]

Scoring logic for gold (default):
  - High score (0.8–1.0): [CONDITIONS]
  - Medium score (0.4–0.79): [CONDITIONS]
  - Low score (0.0–0.39): [CONDITIONS]

To extend to other minerals, add mineral-specific rubrics in build_prompt().
"""
import logging
from typing import Any, Dict, List

from app.agents.base_agent import BaseAgent
from app.models.agent_result import AgentResult, ScoredCell

logger = logging.getLogger(__name__)


class [AgentNamePascalCase]Agent(BaseAgent):
    agent_id: str = "[agent_name]_agent"
    agent_name: str = "[Agent Name Title Case] Agent"

    def build_prompt(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
    ) -> str:
        """
        Build the LLM prompt for [domain] analysis.

        The prompt must instruct the model to return a JSON array of scored cells.
        Each element: { "cell_id": str, "score": float, "confidence": float, "evidence": [str] }
        """
        # Extract relevant data from spatial context
        [RELEVANT_DATA] = spatial_context.get("[key]", [])
        grid_cells = spatial_context.get("grid_cells", [])

        cell_ids = [c["cell_id"] for c in grid_cells]

        prompt = f"""You are a specialist geoscientist analyzing [DOMAIN] data for mineral prospecting.

Target mineral: {target_mineral}
Area of interest: {aoi_geojson}

[DOMAIN_SPECIFIC_DATA_DESCRIPTION]:
{[RELEVANT_DATA]}

Grid cells to score: {cell_ids}

For each grid cell, assign:
- score (0.0–1.0): favorability based on [DOMAIN] evidence
- confidence (0.0–1.0): how well-supported the score is given data density
- evidence: list of 1–3 human-readable strings explaining the score

Scoring rubric for {target_mineral}:
[MINERAL_SPECIFIC_RUBRIC]

Respond ONLY with a JSON array. No prose. Example:
```json
[
  {{
    "cell_id": "col_row",
    "score": 0.85,
    "confidence": 0.9,
    "evidence": ["Evidence string 1", "Evidence string 2"],
    "data_sources_used": ["[source_name]"]
  }}
]
```

If a cell has no relevant data, set score=0.1, confidence=0.1, evidence=["No [domain] data within range"].
"""
        return prompt

    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
        """Parse JSON array from LLM response into ScoredCell objects."""
        parsed = self._safe_parse_json(response)
        if not parsed:
            logger.warning(f"[{self.agent_id}] Could not parse LLM response; returning empty results")
            return []

        scored_cells = []
        for item in parsed:
            try:
                scored_cells.append(
                    ScoredCell(
                        cell_id=item["cell_id"],
                        score=float(item.get("score", 0.1)),
                        confidence=float(item.get("confidence", 0.1)),
                        evidence=item.get("evidence", []),
                        data_sources_used=item.get("data_sources_used", []),
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"[{self.agent_id}] Skipping malformed cell entry: {e}")

        return scored_cells
```

---

### Registration step

After generating the file, remind the user to:

1. Open `backend/app/agents/orchestrator.py`
2. Import the new agent class:
   ```python
   from app.agents.[name]_agent import [NamePascalCase]Agent
   ```
3. Add it to the `self.agents` list in `OrchestratorAgent.__init__()`:
   ```python
   [NamePascalCase]Agent(),
   ```
4. Add its default weight to `backend/app/scoring/weights.py` in each mineral dict:
   ```python
   "[name]_agent": 0.10,  # adjust based on evidence quality
   ```

---

### Optional: spatial context query

If this agent needs new data from PostGIS not already in the spatial context, also
scaffold the query in `backend/app/agents/orchestrator.py` inside
`_build_spatial_context()`. Show the user the SQL pattern used by existing agents
and ask what table/columns the new agent needs.

---

## Checklist before finishing

- [ ] File created at correct path
- [ ] `agent_id` is unique (grep existing agents to confirm)
- [ ] `build_prompt()` returns valid string with JSON instruction
- [ ] `parse_llm_response()` handles malformed/empty response gracefully
- [ ] Registration steps shown to user
- [ ] Weight entry shown to user
