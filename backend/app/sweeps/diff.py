"""Diffing two sweeps — which cells moved, how far, and in which direction.

§41.2 calls this "the most informative artifact this system can produce", and
the reason is that it answers "did that change help?" *spatially* rather than as
a single benchmark number. A new knowledge file that lifts every cell by 0.05 is
not discriminating between anything; one that lifts eight cells and drops forty
is doing work. A scalar cannot tell those apart and a map can.

TWO RULES
---------
**Diff the absolute score, never the relative one.** ``relative_score`` and
``percentile`` are min-max stretches within whatever population they were
normalized over, so if the two sweeps cover different ground — or one is partial
— comparing them measures the difference in denominators, not in geology. Only
``score`` survives leaving its run.

**A delta is uninterpretable without a noise floor.** The same AOI scored twice
on one commit does not return identical numbers, and nobody has measured how
much it varies (docs/07_stable_cell_ids.md records that the run has never been
done). Until it has, this module reports the deltas and refuses to call any of
them an improvement — hence ``noise_floor`` being an explicit argument that
defaults to None and a ``significant`` count that is None when it is absent.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.sweeps.manifest import SweepManifest
from app.sweeps.runner import load_cells

logger = logging.getLogger(__name__)


def diff_sweeps(
    a: SweepManifest,
    b: SweepManifest,
    noise_floor: Optional[float] = None,
) -> Dict[str, Any]:
    """Per-cell movement from sweep ``a`` to sweep ``b``.

    ``noise_floor`` is the absolute-score delta below which a change is
    indistinguishable from LLM nondeterminism. Supply the measured value; when
    it is None the report says so rather than guessing.
    """
    ca = load_cells(a.sweep_id, a.sweeps_dir)
    cb = load_cells(b.sweep_id, b.sweeps_dir)

    only_a = sorted(set(ca) - set(cb))
    only_b = sorted(set(cb) - set(ca))
    common = sorted(set(ca) & set(cb))

    rows: List[Dict[str, Any]] = []
    for cid in common:
        sa = ca[cid].get("score")
        sb = cb[cid].get("score")
        if sa is None or sb is None:
            continue
        delta = round(sb - sa, 6)
        rows.append(
            {
                "cell_id": cid,
                "score_a": sa,
                "score_b": sb,
                "delta": delta,
                "percentile_a": ca[cid].get("percentile"),
                "percentile_b": cb[cid].get("percentile"),
                "tier_a": ca[cid].get("tier"),
                "tier_b": cb[cid].get("tier"),
                "tier_changed": ca[cid].get("tier") != cb[cid].get("tier"),
                "significant": (
                    None if noise_floor is None else abs(delta) > noise_floor
                ),
            }
        )

    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    deltas = [r["delta"] for r in rows]
    n = len(deltas)

    summary: Dict[str, Any] = {
        "n_common": n,
        "n_only_a": len(only_a),
        "n_only_b": len(only_b),
        "mean_delta": round(sum(deltas) / n, 6) if n else 0.0,
        "mean_abs_delta": round(sum(abs(d) for d in deltas) / n, 6) if n else 0.0,
        "max_gain": round(max(deltas), 6) if n else 0.0,
        "max_loss": round(min(deltas), 6) if n else 0.0,
        "moved_up": sum(1 for d in deltas if d > 0),
        "moved_down": sum(1 for d in deltas if d < 0),
        "unchanged": sum(1 for d in deltas if d == 0),
        "tier_changes": sum(1 for r in rows if r["tier_changed"]),
        "noise_floor": noise_floor,
    }

    if noise_floor is None:
        summary["significant"] = None
        summary["interpretation_note"] = (
            "No noise floor has been measured, so no delta here can be called an "
            "improvement rather than nondeterminism. Run the same AOI twice on one "
            "commit with CACHE_ENABLED=false first."
        )
    else:
        summary["significant"] = sum(1 for r in rows if r["significant"])
        # A change that lifts everything equally is a recalibration, not a
        # discrimination — worth separating, because it is the failure mode
        # §41.2 explicitly wants to be able to see.
        spread = summary["mean_abs_delta"]
        drift = abs(summary["mean_delta"])
        summary["interpretation_note"] = (
            "Uniform shift — the change moved every cell the same way rather than "
            "discriminating between them."
            if spread > 0 and drift / spread > 0.8
            else "Differential — the change moved cells in different directions."
        )

    return {
        "sweep_a": a.sweep_id,
        "sweep_b": b.sweep_id,
        "summary": summary,
        "cells": rows,
        "only_in_a": only_a,
        "only_in_b": only_b,
    }
