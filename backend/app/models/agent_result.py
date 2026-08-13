"""
Pydantic models for agent result data structures.
These are NOT database models — they are used for in-memory data transfer
between agents, the orchestrator, and the scoring engine.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ScoredCell(BaseModel):
    """A single grid cell scored by one or more agents."""
    cell_id: str
    geometry: Dict[str, Any]  # GeoJSON geometry object
    score: float = Field(..., ge=0.0, le=1.0, description="Normalized score 0–1")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Agent confidence in score")
    # List of evidence strings explaining the score
    evidence: List[str] = Field(default_factory=list)
    # Source identifiers (channel names or feature IDs) used for this score
    data_sources_used: List[str] = Field(default_factory=list)
    # --- Fields set during synthesis / post-processing (None for per-agent cells) ---
    # Score rescaled relative to the other cells in this AOI (min-max stretch, 0–1)
    relative_score: Optional[float] = None
    # Percentile rank of this cell's composite score within the AOI (0–1)
    percentile: Optional[float] = None
    # Tier assigned from AOI-relative percentile (high/medium/low/negligible)
    tier: Optional[str] = None
    # When this cell was interpolated from a coarser analysis grid, the id of
    # the nearest coarse cell (used to look up per-agent evidence in the UI)
    parent_cell_id: Optional[str] = None
    # --- Novelty (set by orchestrator._attach_novelty) ---
    # Distance from this cell to the nearest recorded mineral occurrence, km.
    nearest_occurrence_km: Optional[float] = None
    nearest_occurrence_name: Optional[str] = None
    # confirms | extends | lead — whether a high score here is the model agreeing
    # with the record or pointing somewhere nothing is recorded. None means
    # UNKNOWN (no occurrence extract built) and must render as nothing: treating
    # missing data as "nothing recorded nearby" would turn an absent file into a
    # prospecting signal.
    novelty: Optional[str] = None


class AgentUsage(BaseModel):
    """Token accounting for one agent run.

    Populated by BaseAgent.run() from the `usage` block on every Anthropic
    response. `est_cost_usd` is a local estimate from MODEL_PRICING in
    base_agent.py — it is not billing data and will drift when prices change.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    llm_calls: int = 0
    est_cost_usd: float = 0.0
    duration_ms: int = 0


class AgentResult(BaseModel):
    """Output from a single specialist agent run."""
    agent_id: str
    status: str  # completed | failed | skipped
    scored_cells: List[ScoredCell] = Field(default_factory=list)
    # Freeform notes from the agent (LLM narrative, caveats, etc.)
    agent_notes: Optional[str] = None
    # Non-fatal issues encountered during the run
    warnings: List[str] = Field(default_factory=list)
    # Token/cost accounting for this run
    usage: Optional[AgentUsage] = None
    # Name of the knowledge file used as the system prompt, or None if the
    # agent ran ungrounded (system=None). Surfaced in the UI run log — four
    # of six agents currently have no knowledge file at all.
    knowledge_file: Optional[str] = None
    # Per-batch cache accounting, merged into the run record's cache block
    cache_hits: int = 0
    cache_misses: int = 0
    # Raw LLM responses, one entry per batch. Written to the run record when
    # SAVE_RAW_LLM is on and stripped before the result goes over SSE — it is
    # megabytes of text the browser has no use for.
    raw_batches: List[Dict[str, Any]] = Field(default_factory=list)
