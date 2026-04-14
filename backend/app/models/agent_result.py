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


class AgentResult(BaseModel):
    """Output from a single specialist agent run."""
    agent_id: str
    status: str  # completed | failed | skipped
    scored_cells: List[ScoredCell] = Field(default_factory=list)
    # Freeform notes from the agent (LLM narrative, caveats, etc.)
    agent_notes: Optional[str] = None
    # Non-fatal issues encountered during the run
    warnings: List[str] = Field(default_factory=list)
