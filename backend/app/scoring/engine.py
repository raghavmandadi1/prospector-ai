"""
Scoring Engine

Synthesizes per-agent scored cells into final composite scores for each
grid cell. Uses confidence-weighted mean across agents, then normalizes
scores RELATIVE TO THE AOI so the shading answers "where are the best spots
in this area?" rather than "how does this area compare to the world?".

Score synthesis formula:
    composite_score(cell) = Σ(agent_weight_i × agent_score_i × confidence_i)
                            / Σ(agent_weight_i × confidence_i)

Relative normalization (applied across all cells in the AOI):
    relative_score = (composite - min) / (max - min)      # min-max stretch
    percentile     = rank of composite among all cells    # 0–1
    tier           = percentile-based (top 10% = high, etc.)

The absolute composite score is preserved on each cell so the UI can always
show both views.
"""
import logging
from typing import Any, Dict, List

from app.models.agent_result import AgentResult, ScoredCell
from app.scoring.grid import GridCell

logger = logging.getLogger(__name__)

# Percentile thresholds for AOI-relative tiers
PCT_HIGH = 0.90    # top 10% of cells in the AOI
PCT_MEDIUM = 0.65
PCT_LOW = 0.35


def synthesize(
    agent_results: List[AgentResult],
    grid_cells: List[GridCell],
    weights: Dict[str, float],
    config: Dict[str, Any],
) -> List[ScoredCell]:
    """
    Synthesize per-agent scores into composite scores for all grid cells.

    Cells scored with confidence 0 (LLM-missed placeholders) contribute zero
    weight and are effectively ignored.

    Note: relative normalization is NOT applied here — call
    normalize_relative() on the final cell set (after any interpolation to a
    finer display grid) so percentiles reflect what the user actually sees.
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

    final_cells = []
    for cell in grid_cells:
        agent_scores = cell_agent_scores.get(cell.cell_id, {})
        composite, confidence = _weighted_mean(agent_scores, weights)

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
                # Cells are scored on their full square but drawn clipped to the
                # AOI, so the map never shows grid poking outside the polygon.
                geometry=getattr(cell, "display_geometry", None) or cell.geometry,
                score=composite,
                confidence=confidence,
                evidence=all_evidence[:20],  # Cap to keep payload manageable
                data_sources_used=list(set(all_sources)),
            )
        )

    return final_cells


def normalize_relative(cells: List[ScoredCell]) -> List[ScoredCell]:
    """
    Annotate cells with AOI-relative fields (in place, returns same list):

    - relative_score: min-max stretch of the composite within this AOI.
      Even when every composite sits in the "negligible" absolute band, the
      stretch spreads them across 0–1 so shading shows the best spots.
    - percentile: fraction of cells with a strictly lower composite
      (ties get the midpoint of their tied block).
    - tier: assigned from percentile, not from absolute thresholds.
    """
    if not cells:
        return cells

    scores = [c.score for c in cells]
    smin, smax = min(scores), max(scores)
    spread = smax - smin
    n = len(scores)

    # Percentile rank with midpoint tie handling
    sorted_scores = sorted(scores)
    import bisect

    for c in cells:
        if spread > 1e-9:
            c.relative_score = round((c.score - smin) / spread, 4)
        else:
            c.relative_score = 0.5  # uniform AOI — no meaningful ranking

        lo = bisect.bisect_left(sorted_scores, c.score)
        hi = bisect.bisect_right(sorted_scores, c.score)
        c.percentile = round(((lo + hi) / 2) / n, 4)
        c.tier = _tier_from_percentile(c.percentile) if spread > 1e-9 else "low"

    logger.info(
        f"Relative normalization: composite range [{smin:.3f}, {smax:.3f}] "
        f"across {n} cells"
    )
    return cells


def _tier_from_percentile(percentile: float) -> str:
    if percentile >= PCT_HIGH:
        return "high"
    elif percentile >= PCT_MEDIUM:
        return "medium"
    elif percentile >= PCT_LOW:
        return "low"
    else:
        return "negligible"


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
    conf_weighted_sum = 0.0
    raw_weight_total = 0.0

    for agent_id, sc in agent_scores.items():
        w = weights.get(agent_id, 1.0)
        effective_weight = w * sc.confidence
        weighted_sum += effective_weight * sc.score
        weight_total += effective_weight
        conf_weighted_sum += w * sc.confidence
        raw_weight_total += w

    if weight_total == 0:
        return 0.0, 0.0

    composite = weighted_sum / weight_total
    # Confidence = weight-averaged agent confidence (0–1 by construction)
    mean_confidence = conf_weighted_sum / raw_weight_total if raw_weight_total else 0.0

    return round(composite, 4), round(mean_confidence, 4)
