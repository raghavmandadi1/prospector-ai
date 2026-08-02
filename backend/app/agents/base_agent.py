"""
Base class for all GeoProspector specialist agents.

Each specialist agent:
1. Receives an AOI, target mineral, and spatial context queried from PostGIS
2. Builds a domain-specific prompt for the LLM
3. Calls the Anthropic API (in parallel batches when the grid is large)
4. Parses the LLM response into structured ScoredCell objects
5. Returns an AgentResult

To add a new agent:
    1. Create a new file in app/agents/
    2. Subclass BaseAgent
    3. Implement build_prompt() (parse_llm_response() has a shared default)
    4. Register it in OrchestratorAgent.agents
"""
import asyncio
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import anthropic

from app.config import settings
from app.models.agent_result import AgentResult, AgentUsage, ScoredCell

logger = logging.getLogger(__name__)

# Root directory for agent knowledge base files
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

# Cells per LLM call. Sized so the response JSON comfortably fits in the
# output token budget even with several evidence strings per cell.
BATCH_SIZE = 50

# Max concurrent LLM calls per agent (6 agents run in parallel on top of this)
MAX_CONCURRENT_BATCHES = 4

# Model used by every agent. Was inlined in call_llm(); hoisted so the usage
# ledger can report which model the numbers belong to.
MODEL_NAME = "claude-sonnet-4-6"

# USD per million tokens. This is a LOCAL ESTIMATE for the run-log ledger,
# not billing data — update when Anthropic pricing changes.
MODEL_PRICING = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
}

# Chars of raw LLM response kept per batch for the UI run log. Enough to see
# whether the model returned JSON or started apologizing, small enough that
# 150 cells of telemetry doesn't bloat the SSE stream.
RESPONSE_PREVIEW_CHARS = 400


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate USD cost for one or more LLM calls. Unknown models cost 0."""
    price = MODEL_PRICING.get(model)
    if not price:
        return 0.0
    return round(
        (input_tokens * price["input"]
         + output_tokens * price["output"]
         + cache_read_tokens * price["cache_read"]
         + cache_creation_tokens * price["cache_write"]) / 1_000_000,
        6,
    )


def cell_summary(cells: List[Dict]) -> str:
    """Compact, human-readable cell list with center coordinates.

    Shared by all agents — full GeoJSON geometry dumps waste thousands of
    tokens and were the reason prompts had to be capped at 50 cells.
    """
    lines = []
    for c in cells:
        bbox = c.get("bbox", [0, 0, 0, 0])
        center_lon = (bbox[0] + bbox[2]) / 2
        center_lat = (bbox[1] + bbox[3]) / 2
        lines.append(
            f'  - {c["cell_id"]}: center ({center_lat:.4f}, {center_lon:.4f})'
        )
    return "\n".join(lines)


def aoi_description(grid_cells: List[Dict]) -> str:
    """Human-readable AOI bounding box derived from the grid cells."""
    bboxes = [c.get("bbox", [0, 0, 0, 0]) for c in grid_cells]
    if not bboxes:
        return "Unknown location"
    min_lon = min(b[0] for b in bboxes)
    min_lat = min(b[1] for b in bboxes)
    max_lon = max(b[2] for b in bboxes)
    max_lat = max(b[3] for b in bboxes)
    return (
        f"Bounding box: {min_lat:.4f}°N to {max_lat:.4f}°N, "
        f"{abs(min_lon):.4f}°W to {abs(max_lon):.4f}°W"
    )


RESPONSE_FORMAT_INSTRUCTIONS = """## Response Format
Return ONLY a JSON array covering ALL cells listed above. Use COMPACT JSON
(no indentation, no spaces after separators) so the full array fits in the
response. Do not include any text outside the JSON code block.
```json
[{"cell_id":"c0_r0","score":0.75,"confidence":0.7,"evidence":["specific evidence string"],"data_sources_used":["source_name"]}]
```"""


def _clamp(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


class BaseAgent(ABC):
    agent_id: str = "base"
    agent_name: str = "Base Agent"
    # Knowledge directory name; defaults to agent_id when None
    knowledge_domain: Optional[str] = None
    # Output budget per LLM call. 4096 was the root cause of the all-zero
    # scores bug: responses for >~40 cells were truncated mid-array, JSON
    # parsing failed, and every cell fell into the zero-score fill-in path.
    max_output_tokens: int = 16000

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or settings.anthropic_api_key
        self._client = anthropic.AsyncAnthropic(api_key=key)

    async def run(
        self,
        aoi_geojson: Dict[str, Any],
        target_mineral: str,
        spatial_context: Dict[str, Any],
        config: Dict[str, Any],
        emit_fn: Optional[Callable] = None,
    ) -> AgentResult:
        """
        Main entry point called by the orchestrator.

        Splits the grid into batches of BATCH_SIZE cells, scores each batch
        with its own LLM call (bounded concurrency), merges the results, and
        fills any unscored cells with zero-confidence placeholders so the
        scoring engine can ignore them without losing grid coverage.

        emit_fn is an optional async telemetry callback (agent_id, payload
        dict). It is best-effort: a failing emitter must never take down a
        run, so every call goes through _emit().
        """
        grid_cells = spatial_context.get("grid_cells", [])
        warnings: List[str] = []
        usage = AgentUsage()
        started_at = time.monotonic()
        try:
            domain = self.knowledge_domain or self.agent_id
            knowledge_path = self.resolve_knowledge_path(domain, target_mineral)
            knowledge = self.load_knowledge(domain, target_mineral)

            await self._emit(emit_fn, {
                "event": "agent_grounding",
                "agent_id": self.agent_id,
                "knowledge_file": (
                    f"{domain}/{knowledge_path.name}" if knowledge_path else None
                ),
                "knowledge_chars": len(knowledge) if knowledge else 0,
            })

            batches = [
                grid_cells[i : i + BATCH_SIZE]
                for i in range(0, len(grid_cells), BATCH_SIZE)
            ]
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

            async def score_batch(index: int, batch: List[Dict]) -> tuple:
                async with semaphore:
                    ctx = dict(spatial_context)
                    ctx["grid_cells"] = batch
                    prompt = self.build_prompt(aoi_geojson, target_mineral, ctx)
                    logger.info(
                        f"[{self.agent_id}] Scoring batch of {len(batch)} cells "
                        f"(prompt {len(prompt)} chars)"
                    )
                    await self._emit(emit_fn, {
                        "event": "batch_started",
                        "agent_id": self.agent_id,
                        "batch_index": index,
                        "batch_count": len(batches),
                        "cell_count": len(batch),
                        "prompt_chars": len(prompt),
                    })
                    t0 = time.monotonic()
                    response, call_usage = await self.call_llm_with_usage(
                        prompt, system_prompt=knowledge
                    )
                    cells = self.parse_llm_response(response, batch)
                    # parse_llm_response drops cells it could not map, so the
                    # scored/requested ratio is the parse-health signal. It is
                    # derived from counts rather than parser state so it stays
                    # correct with MAX_CONCURRENT_BATCHES calls in flight.
                    if not cells:
                        parse_status = "failed"
                    elif len(cells) < len(batch):
                        parse_status = "partial"
                    else:
                        parse_status = "ok"
                    await self._emit(emit_fn, {
                        "event": "batch_complete",
                        "agent_id": self.agent_id,
                        "batch_index": index,
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                        "cells_scored": len(cells),
                        "cells_requested": len(batch),
                        "parse_status": parse_status,
                        "response_chars": len(response or ""),
                        "response_preview": (response or "")[:RESPONSE_PREVIEW_CHARS],
                        **call_usage,
                    })
                    return response, cells, call_usage

            results = await asyncio.gather(
                *(score_batch(i, b) for i, b in enumerate(batches)),
                return_exceptions=True,
            )

            scored: List[ScoredCell] = []
            agent_notes: Optional[str] = None
            for i, res in enumerate(results):
                if isinstance(res, BaseException):
                    logger.error(f"[{self.agent_id}] Batch {i} failed: {res}")
                    warnings.append(f"Batch {i} failed: {res}")
                    await self._emit(emit_fn, {
                        "event": "batch_failed",
                        "agent_id": self.agent_id,
                        "batch_index": i,
                        "error": f"{type(res).__name__}: {res}",
                    })
                    continue
                response, cells, call_usage = res
                usage.llm_calls += 1
                usage.input_tokens += call_usage.get("input_tokens", 0)
                usage.output_tokens += call_usage.get("output_tokens", 0)
                usage.cache_read_tokens += call_usage.get("cache_read_tokens", 0)
                usage.cache_creation_tokens += call_usage.get("cache_creation_tokens", 0)
                if agent_notes is None and response:
                    agent_notes = response[:1000]
                scored.extend(cells)

            # Fill any cells the LLM missed with zero-confidence placeholders.
            # confidence=0 means the scoring engine gives them zero weight.
            scored_ids = {s.cell_id for s in scored}
            missing = [c for c in grid_cells if c.get("cell_id") not in scored_ids]
            for c in missing:
                scored.append(
                    ScoredCell(
                        cell_id=c.get("cell_id", ""),
                        geometry=c.get("geometry", {}),
                        score=0.0,
                        confidence=0.0,
                        evidence=["Cell not scored by LLM"],
                        data_sources_used=[],
                    )
                )
            if missing:
                warnings.append(
                    f"{len(missing)}/{len(grid_cells)} cells not scored by LLM "
                    f"(zero-confidence placeholders inserted)"
                )

            usage.duration_ms = int((time.monotonic() - started_at) * 1000)
            usage.est_cost_usd = estimate_cost_usd(
                MODEL_NAME,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_creation_tokens,
            )

            status = "completed" if len(scored_ids) > 0 else "failed"
            return AgentResult(
                agent_id=self.agent_id,
                status=status,
                scored_cells=scored,
                agent_notes=agent_notes,
                warnings=warnings,
                usage=usage,
                knowledge_file=(
                    f"{domain}/{knowledge_path.name}" if knowledge_path else None
                ),
            )
        except asyncio.CancelledError:
            # Raised when the client stops the run. Must propagate: swallowing
            # it here would let the orchestrator's gather() carry on and keep
            # spending tokens on the remaining agents.
            logger.info(f"[{self.agent_id}] Cancelled after {usage.llm_calls} LLM calls")
            raise
        except Exception as exc:
            logger.exception(f"Agent {self.agent_id} failed: {exc}")
            usage.duration_ms = int((time.monotonic() - started_at) * 1000)
            return AgentResult(
                agent_id=self.agent_id,
                status="failed",
                warnings=[str(exc)],
                usage=usage,
            )

    @staticmethod
    async def _emit(emit_fn: Optional[Callable], payload: Dict[str, Any]) -> None:
        """Best-effort telemetry emit. Never raises, never cancels a run.

        CancelledError is deliberately re-raised — it is not a telemetry
        failure, it is the stop signal passing through.
        """
        if emit_fn is None:
            return
        try:
            await emit_fn(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - telemetry must not break runs
            logger.warning(f"Telemetry emit failed: {exc}")

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
        - RESPONSE_FORMAT_INSTRUCTIONS for the JSON response contract
        """
        raise NotImplementedError

    def resolve_knowledge_path(
        self, domain: str, target_mineral: str
    ) -> Optional[Path]:
        """
        Resolve which knowledge file load_knowledge() would use, without
        reading it. Returns None when the agent will run ungrounded.

        Split out from load_knowledge so telemetry can report the filename
        without changing that method's documented signature.
        """
        mineral_key = target_mineral.lower().replace(" ", "_")
        knowledge_file = KNOWLEDGE_DIR / domain / f"{mineral_key}.md"
        if not knowledge_file.exists():
            knowledge_file = KNOWLEDGE_DIR / domain / "default.md"
        return knowledge_file if knowledge_file.exists() else None

    def load_knowledge(self, domain: str, target_mineral: str) -> Optional[str]:
        """
        Load a domain knowledge markdown file for the given mineral.

        Looks for: knowledge/<domain>/<mineral>.md
        Falls back to: knowledge/<domain>/default.md
        Returns None if no knowledge file exists.
        """
        knowledge_file = self.resolve_knowledge_path(domain, target_mineral)
        if knowledge_file is None:
            mineral_key = target_mineral.lower().replace(" ", "_")
            logger.warning(
                f"[{self.agent_id}] No knowledge file for {domain}/{mineral_key} "
                f"— running with system=None (ungrounded)"
            )
            return None
        content = knowledge_file.read_text(encoding="utf-8")
        logger.info(
            f"[{self.agent_id}] Loaded knowledge: {knowledge_file.name} "
            f"({len(content)} chars)"
        )
        return content

    async def call_llm_with_usage(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Tuple[str, Dict[str, int]]:
        """
        Call the Anthropic API and return (text, usage_dict).

        The usage block is the only place per-call token counts exist — the
        Messages API does not expose them anywhere else, so discarding the
        response object (as call_llm does) loses them permanently.
        """
        kwargs = {
            "model": MODEL_NAME,
            "max_tokens": self.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        message = await self._client.messages.create(**kwargs)

        u = getattr(message, "usage", None)
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "stop_reason": getattr(message, "stop_reason", None),
            "model": MODEL_NAME,
        }
        # max_tokens truncation is the documented root cause of the all-zero
        # scores bug. Worth flagging loudly rather than leaving it to the
        # JSON repair path to quietly paper over.
        if usage["stop_reason"] == "max_tokens":
            logger.warning(
                f"[{self.agent_id}] Response hit max_tokens "
                f"({self.max_output_tokens}) — JSON likely truncated"
            )
        return message.content[0].text, usage

    async def call_llm(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> str:
        """
        Call the Anthropic API with the constructed prompt.
        Uses claude-sonnet-4-6 by default; override in subclass for lighter tasks.

        If system_prompt is provided, it is sent as the system message,
        which is the recommended place for domain knowledge context.

        Thin wrapper over call_llm_with_usage that drops the token counts.
        """
        text, _ = await self.call_llm_with_usage(prompt, system_prompt)
        return text

    def parse_llm_response(
        self, response: str, grid_cells: List[Dict[str, Any]]
    ) -> List[ScoredCell]:
        """
        Shared default parser: map the LLM's JSON array onto the batch cells.
        Scores/confidences are clamped to [0, 1]. Cells the LLM skipped are
        NOT filled here — run() fills them once across all batches.
        """
        parsed = self._safe_parse_json(response)
        if not parsed or not isinstance(parsed, list):
            logger.warning(f"[{self.agent_id}] Could not parse LLM response")
            return []

        cell_map = {c.get("cell_id"): c for c in grid_cells}
        scored = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            cell_id = item.get("cell_id")
            cell = cell_map.get(cell_id)
            if not cell:
                continue
            evidence = item.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence]
            scored.append(
                ScoredCell(
                    cell_id=cell_id,
                    geometry=cell.get("geometry", {}),
                    score=_clamp(item.get("score"), 0.0),
                    confidence=_clamp(item.get("confidence"), 0.5),
                    evidence=[str(e) for e in evidence],
                    data_sources_used=[
                        str(s) for s in item.get("data_sources_used", [])
                    ],
                )
            )
        return scored

    def _safe_parse_json(self, text: str) -> Optional[Any]:
        """
        Extract and parse the first JSON array/object from LLM output.

        Robust to: markdown fences, prose before/after the JSON, and — most
        importantly — responses truncated mid-array (the parser salvages all
        complete objects rather than discarding the whole batch).
        """
        if not text:
            return None

        # Prefer the contents of a fenced code block if present
        fenced = re.search(r"```(?:json)?\s*(.*?)(?:```|\Z)", text, re.DOTALL)
        candidate = fenced.group(1) if fenced else text

        # Locate the outermost array (fall back to object)
        start = candidate.find("[")
        if start == -1:
            start = candidate.find("{")
        if start == -1:
            logger.warning(f"[{self.agent_id}] No JSON found in LLM response")
            return None
        body = candidate[start:]

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            pass

        # Try trimming trailing prose after the closing bracket
        end = body.rfind("]") if body[0] == "[" else body.rfind("}")
        if end != -1:
            try:
                return json.loads(body[: end + 1])
            except json.JSONDecodeError:
                pass

        # Truncation repair: keep everything up to the last complete object
        # and close the array. Salvages partial batches instead of zeroing them.
        if body[0] == "[":
            last_obj_end = body.rfind("}")
            while last_obj_end != -1:
                repaired = body[: last_obj_end + 1].rstrip().rstrip(",") + "]"
                try:
                    parsed = json.loads(repaired)
                    logger.warning(
                        f"[{self.agent_id}] Repaired truncated JSON response "
                        f"({len(parsed)} objects salvaged)"
                    )
                    return parsed
                except json.JSONDecodeError:
                    last_obj_end = body.rfind("}", 0, last_obj_end)

        logger.warning(f"[{self.agent_id}] Failed to parse LLM JSON response")
        return None
