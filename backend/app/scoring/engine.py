"""
Scoring Engine

Synthesizes per-agent scored cells into final composite scores for each
grid cell. Uses confidence-weighted mean across agents, then assigns
a tier label (high / medium / low / negligible).

Score synthesis formula:
    composite_score(cell) = Σ(agent_weight_i × agent_score_i × confidence_i)
                            / Σ(agent_weight_i × confidence_i)

Where:
    agent_weight_i  — from DEFAULT_WEIGHTS[mineral][agent_id]
    agent_score_i   — ScoredCell.score (0.0–1.0)
    confidence_i    — ScoredCell.confidence (0.0–1.0)
"""
from typing import Any, Dict, List

from app.models.agent_result import AgentResult, ScoredCell
from app.scoring.grid import GridCell


# Tier thresholds (composite score)
TIER_HIGH = 0.65
TIER_MEDIUM = 0.40
TIER_LOW = 0.20


def synthesize(
    agent_results: List[AgentResult],
    grid_cells: List[GridCell],
    weights: Dict[str, float],
    config: Dict[str, Any],
) -> List[ScoredCell]:
    """
    Synthesize per-agent scores into composite scores for all grid cells.

    Args:
        agent_results:  List of AgentResult from each specialist agent
        grid_cells:     The grid cells that define the spatial extent
        weights:        Per-agent weight dict {agent_id: weight}. Agents not
                        in this dict receive a default weight of 1.0.
        config:         Analysis config (reserved for future normalisation options)

    Returns:
        List of ScoredCell with composite score, tier, and aggregated evidence
    """
    # Build lookup: cell_id → {agent_id → ScoredCell}
    cell_agent_scores: Dict[str, Dict[str, ScoredCell]] = {}
    for result in agent_results:
        if result.status != "completed":
            continue
        for scored_cell in result.scored_cells:
            if scored_cell.cell_id not in cell_agent_scores:
                cell_agent_scores[scored_cell.cell_id] = {}
            cell_agent_scores[scored_cell.cell_id][result.agent_id] = scored_cell

    # Also build a lookup for grid cell geometry by cell_id
    geom_lookup = {c.cell_id: c.geometry for c in grid_cells}

    final_cells = []
    for cell in grid_cells:
        agent_scores = cell_agent_scores.get(cell.cell_id, {})
        composite, confidence = _weighted_mean(agent_scores, weights)
        tier = _assign_tier(composite)

        # Aggregate evidence and data sources from all agents
        all_evidence = []
        all_sources = []
        for agent_id, sc in agent_scores.items():
            for ev in sc.evidence:
                all_evidence.append(f"[{agent_id}] {ev}")
            all_sources.extend(sc.data_sources_used)

        final_cells.append(
            ScoredCell(
                cell_id=cell.cell_id,
                geometry=cell.geometry,
                score=composite,
                confidence=confidence,
                evidence=all_evidence[:20],  # Cap to keep payload manageable
                data_sources_used=list(set(all_sources)),
            )
        )

    return final_cells


def _weighted_mean(
    agent_scores: Dict[str, ScoredCell],
    weights: Dict[str, float],
) -> tuple:
    """
    Compute confidence-weighted mean score across agents.

    Returns (composite_score, mean_confidence).
    Returns (0.0, 0.0) if no agent has scores for this cell.
    """
    if not agent_scores:
        return 0.0, 0.0

    weighted_sum = 0.0
    weight_total = 0.0

    for agent_id, sc in agent_scores.items():
        w = weights.get(agent_id, 1.0)
        effective_weight = w * sc.confidence
        weighted_sum += effective_weight * sc.score
        weight_total += effective_weight

    if weight_total == 0:
        return 0.0, 0.0

    composite = weighted_sum / weight_total
    mean_confidence = weight_total / (len(agent_scores) * max(weights.values() if weights else [1.0]))
    mean_confidence = min(mean_confidence, 1.0)

    return round(composite, 4), round(mean_confidence, 4)


def _assign_tier(score: float) -> str:
    """Assign a qualitative tier label to a composite score."""
    if score >= TIER_HIGH:
        return "high"
    elif score >= TIER_MEDIUM:
        return "medium"
    elif score >= TIER_LOW:
        return "low"
    else:
        return "negligible"
