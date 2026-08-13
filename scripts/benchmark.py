#!/usr/bin/env python3
"""
Benchmark harness — the payoff for run records and stable cell ids.

Scores a set of labelled AOIs and reports metrics against a frozen baseline, so
that changing a weight, writing a knowledge file, or editing a prompt produces a
number rather than a vibe.

It runs **offline against `data/runs/`**: run records store absolute composite
scores and cell ids, and cell ids regenerate their own geometry, so historical
runs can be re-scored without spending a token.

    # 1. Establish the noise floor FIRST — see --noise-floor below
    python scripts/benchmark.py --noise-floor

    # 2. Report current state, optionally against a frozen baseline
    python scripts/benchmark.py --baseline benchmarks/baselines/2026-08-01.json

    # 3. Freeze the current state as the thing to compare against
    python scripts/benchmark.py --freeze benchmarks/baselines/$(date +%F).json

    # 4. Measure the deterministic baseline with no LLM runs at all
    python scripts/benchmark.py --wofe-only

Three things this deliberately refuses to do:

* **Report working-percentile or recall for an AOI whose `workings_verified` is
  false.** Draft coordinates produce confident, meaningless numbers. The gate is
  per-AOI, not global: `scripts/build_labels_workings.py` derives real
  survey/topo-grade workings for the AOIs it can and leaves the rest empty, and
  those two cases must not be averaged together. The global `verified` flag
  covers `approx_center` — a *different* claim, still unverified — and is
  reported separately rather than used as the gate, because the working metrics
  read working coordinates and never touch `approx_center`.
* **Call a delta an improvement when it is smaller than the measured noise
  floor.** LLM output is nondeterministic; without that comparison a team can
  spend months tuning noise.
* **Quietly hide what it dropped.** Every exclusion is printed.

The WofE column
---------------
`app.scoring.wofe_baseline` scores the same cells with a published statistical
model and no LLM (USGS OF01-501, NE Washington only — it refuses everywhere
else). For every matched run this harness reports the Spearman rank correlation
between the LLM composite and that baseline, and each one's mean working
percentile. If a language model and a fitted statistical model disagree
completely about which cells are good, that is the single most informative number
here, and it is reported plainly in either direction. A negative correlation is
not a bug in the harness — it is the finding.
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

BENCH_DIR = REPO_ROOT / "benchmarks"
RUNS_DIR = REPO_ROOT / "data" / "runs"

#: Resolution the deterministic baseline is evaluated at in `--wofe-only`. 250 m
#: is the native resolution of `of00495.sqlite`; the WofE predictors are presence
#: tests and lose their discrimination if applied at coarser cells (see the
#: wofe_baseline docstring).
WOFE_ONLY_RESOLUTION_M = 250

#: Fallback AOI radius for `--wofe-only` when an AOI has no `radius_km`.
DEFAULT_AOI_RADIUS_KM = 6.0


# --- loading ---------------------------------------------------------------


def load_labels(path: Optional[Path] = None) -> Dict[str, Any]:
    import yaml

    p = path or BENCH_DIR / "labels.yaml"
    if not p.exists():
        raise SystemExit(f"No labels file at {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_runs(runs_dir: Path) -> List[Dict[str, Any]]:
    if not runs_dir.exists():
        return []
    out = []
    for p in sorted(runs_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:  # a half-written or hand-edited record
            print(f"  ! skipping unreadable record {p.name}: {exc}", file=sys.stderr)
    return out


def aoi_centroid(run: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    geo = (run.get("inputs") or {}).get("aoi_geojson")
    if not geo:
        return None
    try:
        from shapely.geometry import shape

        g = geo
        if g.get("type") == "FeatureCollection":
            g = g["features"][0]["geometry"]
        elif g.get("type") == "Feature":
            g = g["geometry"]
        c = shape(g).centroid
        return c.x, c.y
    except Exception:
        return None


def match_runs_to_aois(
    runs: List[Dict[str, Any]], labels: Dict[str, Any], tolerance_deg: float = 0.15
) -> Dict[str, List[Dict[str, Any]]]:
    """Assign each completed run to the benchmark AOI it sits in.

    Matching is by centroid proximity rather than by an id in the request,
    because runs are drawn by hand on the map and carry no benchmark label.
    """
    aois = labels.get("aois", {})
    matched: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for run in runs:
        if run.get("status") != "completed":
            continue
        c = aoi_centroid(run)
        if c is None:
            continue
        best, best_d = None, tolerance_deg
        for name, spec in aois.items():
            ax, ay = spec["approx_center"]
            d = ((c[0] - ax) ** 2 + (c[1] - ay) ** 2) ** 0.5
            if d < best_d:
                best, best_d = name, d
        if best:
            matched[best].append(run)
    return matched


# --- metrics ---------------------------------------------------------------


def rank(values: Sequence[float]) -> List[float]:
    """Tie-averaged ranks, 1-based. The mid-rank convention Spearman needs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mid = (i + j) / 2.0 + 1.0  # 1-based mid-rank of the tied block
        for k in range(i, j + 1):
            ranks[order[k]] = mid
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman's rho, implemented here because scipy is not installed.

    Pearson correlation of tie-averaged ranks — the definition that stays correct
    when values tie, which they do constantly here: the WofE baseline gives every
    cell with no favourable lithology and no fault the same score, by design.
    The shortcut formula (1 − 6Σd²/n(n²−1)) is wrong under ties and is not used.

    Returns ``None`` when rho is undefined: fewer than three pairs, or one side
    constant (an all-ties ranking has zero variance and no correlation with
    anything). ``None`` must be reported as "not computable", never as 0.0.
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy)


def _percentile_in_map(
    by_id: Dict[str, float], lon: float, lat: float
) -> Optional[float]:
    """Percentile rank of the cell containing a point, within one score map.

    Shared by the LLM composite and the deterministic baseline so the two
    numbers are computed identically and are therefore comparable.
    """
    from app.scoring.grid import cell_id_for_point, parse_cell_id

    if not by_id:
        return None
    try:
        res, _, _ = parse_cell_id(next(iter(by_id)))
    except (ValueError, KeyError, StopIteration):
        return None

    target = cell_id_for_point(lon, lat, res)
    if target not in by_id:
        return None

    scores = sorted(by_id.values())
    s = by_id[target]
    lo = sum(1 for v in scores if v < s)
    hi = sum(1 for v in scores if v <= s)
    return ((lo + hi) / 2) / len(scores)


def composite_map(run: Dict[str, Any]) -> Dict[str, float]:
    """``cell_id -> absolute composite score`` for one run."""
    return {
        c["cell_id"]: float(c.get("score", 0.0))
        for c in (run.get("composite_cells") or [])
        if c.get("cell_id")
    }


def percentile_of_point(
    run: Dict[str, Any], lon: float, lat: float
) -> Optional[float]:
    """Percentile rank of the cell containing a point, within its own AOI.

    Uses the stored absolute scores and recomputes the rank here rather than
    trusting the run's stored `percentile`, so the metric is independent of
    whatever normalisation the run happened to apply.
    """
    return _percentile_in_map(composite_map(run), lon, lat)


# --- the deterministic baseline --------------------------------------------


def wofe_map(
    cell_ids: Sequence[str], target_mineral: str = "gold"
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """``cell_id -> deterministic WofE score``, plus why cells were refused.

    Refused cells are *absent* from the map rather than present as 0.0. A zero
    would be a claim that the ground is barren; the model's actual statement
    outside its NE Washington footprint is that it has nothing to say.
    """
    try:
        from app.scoring.wofe_baseline import score_cells_wofe
    except Exception as exc:  # pragma: no cover - import-shaped failure
        return {}, {"error": f"wofe_baseline unavailable: {exc}"}

    results = score_cells_wofe(list(cell_ids), target_mineral)
    scores = {
        cid: float(r["score"]) for cid, r in results.items() if r.get("score") is not None
    }
    reasons: Dict[str, int] = defaultdict(int)
    for r in results.values():
        if r.get("refused"):
            reasons[r["refused"]] += 1
    tracts: Dict[str, int] = defaultdict(int)
    for r in results.values():
        if r.get("tract"):
            tracts[r["tract"]] += 1
    return scores, {
        "cells": len(results),
        "scored": len(scores),
        "coverage": round(len(scores) / len(results), 4) if results else 0.0,
        "refusals": dict(reasons),
        "tracts": dict(tracts),
    }


def grounded_fraction(run: Dict[str, Any]) -> float:
    """Share of composite weight coming from agents that had a knowledge file.

    Reported on every run so it stays visible rather than getting forgotten:
    the majority of a gold composite is currently ungrounded model prior, scored
    and displayed identically to the grounded part.
    """
    prov = run.get("provenance") or {}
    weights = (run.get("inputs") or {}).get("weights") or {}
    agents = list((run.get("agent_results") or {}).keys())
    if not agents:
        return 0.0
    ungrounded = set(prov.get("agents_without_knowledge") or [])

    # Missing weights fall back to equal weighting, matching engine._weighted_mean
    w = {a: float(weights.get(a, 1.0)) for a in agents}
    total = sum(w.values())
    if total <= 0:
        return 0.0
    return round(sum(v for a, v in w.items() if a not in ungrounded) / total, 4)


def workings_gate(spec: Dict[str, Any]) -> Tuple[bool, str]:
    """May working-percentile and recall be reported for this AOI, and if not why.

    The reason string is printed verbatim. A metric that is silently absent is
    indistinguishable from a metric that is zero.
    """
    workings = spec.get("known_workings") or []
    if not workings:
        return False, (
            "no known_workings — run scripts/build_labels_workings.py, and if it "
            "still finds none, this AOI has no survey- or topo-grade gold "
            "occurrence within its radius"
        )
    if not spec.get("workings_verified"):
        return False, (
            f"{len(workings)} working(s) present but workings_verified is false "
            f"— for a null/control AOI that means the workings contradict the "
            f"label and a human has to resolve it before the numbers mean "
            f"anything"
        )
    return True, ""


def run_metrics(run: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    cells = run.get("composite_cells") or []
    scores = [c.get("score", 0.0) for c in cells]
    m: Dict[str, Any] = {
        "run_id": run.get("run_id"),
        "cells": len(cells),
        "mean_composite": round(statistics.fmean(scores), 4) if scores else 0.0,
        # Near-zero flatness means the agents are not discriminating and the
        # map is decorative.
        "flatness": round(statistics.pstdev(scores), 4) if len(scores) > 1 else 0.0,
        "grounded_fraction": grounded_fraction(run),
        "git_commit": (run.get("provenance") or {}).get("git_commit"),
        "git_dirty": (run.get("provenance") or {}).get("git_dirty"),
        "spatial_context": (run.get("provenance") or {}).get(
            "spatial_context_available"
        ),
        "cache_hits": (run.get("cache") or {}).get("hits", 0),
    }

    # --- the deterministic baseline over the same cells -------------------
    llm = composite_map(run)
    mineral = (run.get("inputs") or {}).get("target_mineral") or "gold"
    wofe, wofe_info = wofe_map(list(llm.keys()), mineral)
    m["wofe"] = wofe_info
    shared = [cid for cid in llm if cid in wofe]
    if shared:
        m["wofe_mean"] = round(statistics.fmean(wofe[c] for c in shared), 4)
        m["wofe_flatness"] = (
            round(statistics.pstdev([wofe[c] for c in shared]), 4)
            if len(shared) > 1
            else 0.0
        )
        rho = spearman([llm[c] for c in shared], [wofe[c] for c in shared])
        m["spearman_llm_vs_wofe"] = None if rho is None else round(rho, 4)
        m["spearman_n"] = len(shared)

    # --- ground truth, gated per AOI --------------------------------------
    ok, reason = workings_gate(spec)
    if not ok:
        m["workings_refused"] = reason
        return m

    workings = spec.get("known_workings") or []
    pcts: List[float] = []
    wofe_pcts: List[float] = []
    missed = 0
    for w in workings:
        p = _percentile_in_map(llm, w["lon"], w["lat"])
        if p is None:
            # The working is inside the labelled AOI's radius but outside the
            # polygon the operator actually drew. Not an error, but it must be
            # counted or the metric silently narrows to whatever was covered.
            missed += 1
            continue
        pcts.append(p)
        q = _percentile_in_map(wofe, w["lon"], w["lat"])
        if q is not None:
            wofe_pcts.append(q)
    if pcts:
        m["working_percentiles"] = [round(p, 4) for p in pcts]
        m["mean_working_percentile"] = round(statistics.fmean(pcts), 4)
        m["recall_at_high"] = round(sum(1 for p in pcts if p >= 0.90) / len(pcts), 4)
        m["workings_in_run"] = len(pcts)
    if wofe_pcts:
        m["mean_working_percentile_wofe"] = round(statistics.fmean(wofe_pcts), 4)
        m["recall_at_high_wofe"] = round(
            sum(1 for p in wofe_pcts if p >= 0.90) / len(wofe_pcts), 4
        )
        m["workings_in_wofe"] = len(wofe_pcts)
    if missed:
        m["workings_outside_run_grid"] = missed
    return m


def aggregate(
    matched: Dict[str, List[Dict[str, Any]]], labels: Dict[str, Any]
) -> Dict[str, Any]:
    aois = labels["aois"]
    per_aoi: Dict[str, Any] = {}
    for name, runs in matched.items():
        spec = aois[name]
        metrics = [run_metrics(r, spec) for r in runs]
        ok, reason = workings_gate(spec)

        def mean_key(key: str) -> Optional[float]:
            vals = [m[key] for m in metrics if m.get(key) is not None]
            return round(statistics.fmean(vals), 4) if vals else None

        per_aoi[name] = {
            "label": spec["label"],
            "toponym_revealing": bool(spec.get("toponym_revealing")),
            "n_runs": len(runs),
            "runs": metrics,
            "mean_composite": round(
                statistics.fmean([m["mean_composite"] for m in metrics]), 4
            ),
            "flatness": round(statistics.fmean([m["flatness"] for m in metrics]), 4),
            "grounded_fraction": round(
                statistics.fmean([m["grounded_fraction"] for m in metrics]), 4
            ),
            # WofE columns. None means "the deterministic model does not apply
            # here" (outside NE Washington) — not "no correlation".
            "wofe_coverage": round(
                statistics.fmean(
                    [(m.get("wofe") or {}).get("coverage", 0.0) for m in metrics]
                ),
                4,
            ),
            "wofe_mean": mean_key("wofe_mean"),
            "spearman_llm_vs_wofe": mean_key("spearman_llm_vs_wofe"),
            "workings_verified": bool(ok),
            "workings_refused": None if ok else reason,
            "mean_working_percentile": mean_key("mean_working_percentile"),
            "recall_at_high": mean_key("recall_at_high"),
            "mean_working_percentile_wofe": mean_key("mean_working_percentile_wofe"),
            "recall_at_high_wofe": mean_key("recall_at_high_wofe"),
        }

    def mean_of(label: str, exclude_revealing: bool = False) -> Optional[float]:
        vals = [
            v["mean_composite"]
            for v in per_aoi.values()
            if v["label"] == label
            and not (exclude_revealing and v["toponym_revealing"])
        ]
        return round(statistics.fmean(vals), 4) if vals else None

    pos, null = mean_of("positive"), mean_of("null")
    pos_blind = mean_of("positive", exclude_revealing=True)

    summary: Dict[str, Any] = {
        "aois_covered": len(per_aoi),
        "aois_labelled": len(aois),
        "mean_positive": pos,
        "mean_null": null,
        # The headline: positives minus nulls. Should be clearly positive.
        "separation": round(pos - null, 4) if pos is not None and null is not None else None,
        # The number that actually measures the model, with the AOIs whose
        # answer is legible from their own place names removed (§23).
        "separation_blind": round(pos_blind - null, 4)
        if pos_blind is not None and null is not None
        else None,
        "grounded_fraction": round(
            statistics.fmean([v["grounded_fraction"] for v in per_aoi.values()]), 4
        )
        if per_aoi
        else None,
    }

    def mean_over_aois(key: str) -> Optional[float]:
        vals = [v[key] for v in per_aoi.values() if v.get(key) is not None]
        return round(statistics.fmean(vals), 4) if vals else None

    summary.update(
        {
            "aois_with_verified_workings": sum(
                1 for v in per_aoi.values() if v["workings_verified"]
            ),
            "spearman_llm_vs_wofe": mean_over_aois("spearman_llm_vs_wofe"),
            "wofe_coverage": mean_over_aois("wofe_coverage"),
            "mean_working_percentile": mean_over_aois("mean_working_percentile"),
            "recall_at_high": mean_over_aois("recall_at_high"),
            "mean_working_percentile_wofe": mean_over_aois(
                "mean_working_percentile_wofe"
            ),
            "recall_at_high_wofe": mean_over_aois("recall_at_high_wofe"),
        }
    )
    return {"per_aoi": per_aoi, "summary": summary}


def wofe_only(labels: Dict[str, Any], resolution_m: int) -> Dict[str, Any]:
    """Score every labelled AOI with the deterministic model and no run records.

    This exists so the baseline is measurable *before* any LLM run — which is the
    current state of the repo, `data/runs/` being empty. It also makes the scope
    limit impossible to miss: the published model covers NE Washington, so most
    of the labelled AOIs come back entirely refused, and that refusal is the
    honest output rather than a gap to be filled in with something.
    """
    from app.scoring.grid import generate_grid
    from app.scoring.wofe_baseline import score_cells_wofe

    out: Dict[str, Any] = {"resolution_m": resolution_m, "per_aoi": {}}
    for name, spec in sorted((labels.get("aois") or {}).items()):
        lon, lat = spec["approx_center"]
        radius_km = float(spec.get("radius_km") or DEFAULT_AOI_RADIUS_KM)
        # Same square the workings were assigned from, so the two agree about
        # which ground this AOI is.
        aoi = _square_aoi(lon, lat, radius_km)
        entry: Dict[str, Any] = {
            "label": spec.get("label"),
            "radius_km": radius_km,
        }
        try:
            cells = generate_grid(aoi, resolution_m)
        except Exception as exc:
            entry["error"] = f"grid failed: {exc}"
            out["per_aoi"][name] = entry
            continue

        results = score_cells_wofe(cells)
        scores = {
            cid: r["score"] for cid, r in results.items() if r.get("score") is not None
        }
        refusals: Dict[str, int] = defaultdict(int)
        tracts: Dict[str, int] = defaultdict(int)
        for r in results.values():
            if r.get("refused"):
                refusals[r["refused"]] += 1
            if r.get("tract"):
                tracts[r["tract"]] += 1
        entry.update(
            {
                "cells": len(cells),
                "scored": len(scores),
                "coverage": round(len(scores) / len(cells), 4) if cells else 0.0,
                "refusals": dict(refusals),
                "tracts": dict(sorted(tracts.items())),
            }
        )
        if scores:
            vals = list(scores.values())
            entry["mean"] = round(statistics.fmean(vals), 4)
            entry["max"] = round(max(vals), 4)
            entry["flatness"] = (
                round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0
            )

        ok, reason = workings_gate(spec)
        entry["workings_verified"] = bool(ok)
        if not ok:
            entry["workings_refused"] = reason
        elif scores:
            pcts = []
            outside = 0
            for w in spec.get("known_workings") or []:
                p = _percentile_in_map(scores, w["lon"], w["lat"])
                if p is None:
                    outside += 1
                    continue
                pcts.append(p)
            if pcts:
                entry["mean_working_percentile"] = round(statistics.fmean(pcts), 4)
                entry["recall_at_high"] = round(
                    sum(1 for p in pcts if p >= 0.90) / len(pcts), 4
                )
                entry["workings_scored"] = len(pcts)
            if outside:
                entry["workings_outside_scored_area"] = outside
        out["per_aoi"][name] = entry
    return out


def noise_floor(matched: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Spread of mean composite across repeat runs of the same AOI on one commit.

    Any improvement smaller than this is not an improvement. Establishing it
    before measuring any change is the step teams skip and then spend months
    tuning noise.
    """
    spreads = []
    detail = {}
    for name, runs in matched.items():
        by_commit: Dict[Any, List[float]] = defaultdict(list)
        for r in runs:
            prov = r.get("provenance") or {}
            if prov.get("git_dirty"):
                continue  # not reproducible; cannot bound noise with it
            cells = r.get("composite_cells") or []
            if cells:
                by_commit[prov.get("git_commit")].append(
                    statistics.fmean([c.get("score", 0.0) for c in cells])
                )
        for commit, means in by_commit.items():
            if len(means) >= 2:
                spread = max(means) - min(means)
                spreads.append(spread)
                detail[f"{name}@{commit}"] = {
                    "n": len(means),
                    "spread": round(spread, 4),
                }
    if not spreads:
        return None
    return {
        "floor": round(max(spreads), 4),
        "mean_spread": round(statistics.fmean(spreads), 4),
        "samples": detail,
    }


# --- reporting -------------------------------------------------------------


def render(
    result: Dict[str, Any],
    labels: Dict[str, Any],
    floor: Optional[Dict[str, Any]],
    baseline: Optional[Dict[str, Any]],
    excluded: List[str],
) -> str:
    s = result["summary"]
    verified = bool(labels.get("verified"))
    lines: List[str] = []
    add = lines.append

    n_verified = s.get("aois_with_verified_workings", 0)

    add("# GeoProspector benchmark")
    add("")
    add(f"- labels: `{labels.get('version')}`")
    add(f"- AOIs with runs: {s['aois_covered']} / {s['aois_labelled']} labelled")
    add(
        f"- AOIs with verified ground truth: **{n_verified}** of "
        f"{s['aois_covered']} covered"
    )
    add("")

    # Two separate claims, two separate gates. Conflating them is how a harness
    # ends up either refusing everything forever or reporting nonsense.
    add("> **What is and is not verified.**")
    add(
        f"> `approx_center` coordinates: **{'checked' if verified else 'UNCHECKED'}** "
        f"(`verified: {str(verified).lower()}`). They are approximate district "
        f"centres. They are used only to match a drawn run to a labelled AOI, "
        f"within a 0.15° tolerance, so being a few km out does not corrupt "
        f"anything."
    )
    add(
        "> `known_workings` coordinates: derived by "
        "`scripts/build_labels_workings.py` from WA DNR / WGS Mines & Minerals, "
        "restricted to sites whose recorded positional accuracy is survey- or "
        "topo-grade. `district_centroid`, `variable` and `derived` positions are "
        "excluded."
    )
    add(
        "> Working-percentile and recall@high are gated on **per-AOI** "
        "`workings_verified`, because those metrics read working coordinates and "
        "never touch `approx_center`. Every AOI that is refused says why, below."
    )
    add("")

    add("## Summary")
    add("")
    add("| Metric | Value |")
    add("|---|---|")
    add(f"| Mean composite, positives | {_fmt(s['mean_positive'])} |")
    add(f"| Mean composite, nulls | {_fmt(s['mean_null'])} |")
    add(f"| **Separation** (positive − null) | {_fmt(s['separation'])} |")
    add(
        f"| **Separation, toponym-blind** | {_fmt(s['separation_blind'])} "
        "|"
    )
    add(f"| Grounded fraction | {_fmt(s['grounded_fraction'])} |")
    add(
        f"| **Spearman, LLM composite vs WofE baseline** | "
        f"{_fmt(s.get('spearman_llm_vs_wofe'))} |"
    )
    add(f"| WofE coverage (share of cells the model will score) | "
        f"{_fmt(s.get('wofe_coverage'))} |")
    add(
        f"| Mean working percentile — LLM | "
        f"{_fmt(s.get('mean_working_percentile'))} |"
    )
    add(
        f"| Mean working percentile — WofE | "
        f"{_fmt(s.get('mean_working_percentile_wofe'))} |"
    )
    add(f"| recall@high — LLM | {_fmt(s.get('recall_at_high'))} |")
    add(f"| recall@high — WofE | {_fmt(s.get('recall_at_high_wofe'))} |")
    add("")
    add(
        "*Grounded fraction* is the share of composite weight from agents that "
        "had a knowledge file. Everything else is ungrounded model prior, scored "
        "and displayed identically."
    )
    add("")
    add(
        "*Toponym-blind separation* excludes AOIs whose answer is legible from "
        "their own place names (Monte Cristo, Sultan Basin, Silver Creek). That "
        "is the number that measures the model rather than the label."
    )
    add("")
    add(
        "*Spearman vs WofE* compares the LLM composite against a deterministic "
        "reimplementation of USGS OF01-501 over the same cells. `—` means the "
        "deterministic model refused every cell, which it does everywhere outside "
        "its NE Washington study area — that is scope, not failure. Near 1.0 "
        "means the LLM has rediscovered the published model. Near 0 means they "
        "are ranking unrelated things, and at most one of them can be right. "
        "Negative means they actively disagree; before trusting the LLM in that "
        "case, note that the WofE side is a fitted model with 50 training sites "
        "behind it and the LLM side is not."
    )
    add("")

    add("## Noise floor")
    add("")
    if floor:
        add(
            f"Largest spread across repeat runs of one AOI on one clean commit: "
            f"**{floor['floor']:.4f}** (mean {floor['mean_spread']:.4f})."
        )
        add("")
        add("Any delta smaller than the floor is not an improvement.")
        add("")
        for k, v in sorted(floor["samples"].items()):
            add(f"- `{k}`: {v['n']} runs, spread {v['spread']:.4f}")
    else:
        add("**Not established.** Needs ≥2 runs of the same AOI on the same clean")
        add("commit with the cache off. Run:")
        add("")
        add("```")
        add("CACHE_ENABLED=false ./run-dev.sh   # then run the same AOI 3×")
        add("python scripts/benchmark.py --noise-floor")
        add("```")
        add("")
        add("Until it exists, no delta below can be called an improvement.")
    add("")

    add("## Per-AOI")
    add("")
    add(
        "| AOI | Label | Runs | Mean | Flatness | Grounded | Toponym-revealing "
        "| WofE cov | ρ(LLM,WofE) |"
    )
    add("|---|---|---|---|---|---|---|---|---|")
    for name, v in sorted(result["per_aoi"].items()):
        add(
            f"| {name} | {v['label']} | {v['n_runs']} | {v['mean_composite']:.4f} "
            f"| {v['flatness']:.4f} | {v['grounded_fraction']:.2f} "
            f"| {'yes' if v['toponym_revealing'] else 'no'} "
            f"| {_fmt(v.get('wofe_coverage'))} "
            f"| {_fmt(v.get('spearman_llm_vs_wofe'))} |"
        )
    add("")
    add(
        "*Flatness* is the standard deviation of composite within the AOI. Near "
        "zero means the agents are not discriminating and the map is decorative."
    )
    add("")

    add("## Ground truth per AOI")
    add("")
    add(
        "| AOI | Workings | Verified | Working pct (LLM) | recall@high (LLM) "
        "| Working pct (WofE) | recall@high (WofE) |"
    )
    add("|---|---|---|---|---|---|---|")
    refusals: List[Tuple[str, str]] = []
    for name, v in sorted(result["per_aoi"].items()):
        spec = labels["aois"][name]
        n_w = len(spec.get("known_workings") or [])
        add(
            f"| {name} | {n_w} | {'yes' if v['workings_verified'] else 'no'} "
            f"| {_fmt(v.get('mean_working_percentile'))} "
            f"| {_fmt(v.get('recall_at_high'))} "
            f"| {_fmt(v.get('mean_working_percentile_wofe'))} "
            f"| {_fmt(v.get('recall_at_high_wofe'))} |"
        )
        if v.get("workings_refused"):
            refusals.append((name, v["workings_refused"]))
    add("")
    if refusals:
        add("**Refused, and why:**")
        add("")
        for name, why in refusals:
            add(f"- `{name}`: {why}")
        add("")
    add(
        "A working percentile of 0.5 means the model ranked recorded workings no "
        "better than the ground around them — i.e. it has no signal. Only AOIs "
        "with `Verified: yes` contribute to the summary."
    )
    add("")

    if baseline:
        add("## Delta vs baseline")
        add("")
        bs = baseline.get("summary", {})
        add("| Metric | Baseline | Now | Δ | Exceeds noise floor? |")
        add("|---|---|---|---|---|")
        for key in ("mean_positive", "mean_null", "separation", "separation_blind",
                    "grounded_fraction", "spearman_llm_vs_wofe",
                    "mean_working_percentile", "recall_at_high"):
            b, n = bs.get(key), s.get(key)
            if b is None or n is None:
                add(f"| {key} | {_fmt(b)} | {_fmt(n)} | — | — |")
                continue
            d = n - b
            if floor:
                verdict = "**yes**" if abs(d) > floor["floor"] else "no — within noise"
            else:
                verdict = "unknown — no floor"
            add(f"| {key} | {b:.4f} | {n:.4f} | {d:+.4f} | {verdict} |")
        add("")

    if excluded:
        add("## Excluded")
        add("")
        for e in excluded:
            add(f"- {e}")
        add("")

    return "\n".join(lines)


def render_wofe_only(labels: Dict[str, Any], resolution_m: int) -> str:
    """Report for `--wofe-only`: the deterministic model, no run records at all."""
    result = wofe_only(labels, resolution_m)
    lines: List[str] = []
    add = lines.append

    add("# GeoProspector — deterministic WofE baseline only")
    add("")
    add(f"- labels: `{labels.get('version')}`")
    add(f"- resolution: {resolution_m} m (native resolution of `of00495.sqlite`)")
    add("- no run records were read; no LLM was involved")
    add("")
    add(
        "> This is `app.scoring.wofe_baseline` — a reimplementation of USGS "
        "OF01-501 (Boleneus et al. 2001) over the OF-00-495 rasters. It was "
        "fitted on 50 epithermal gold training sites in a 222 × 277 km area of "
        "**NE Washington** and it refuses to score anything outside that "
        "footprint. AOIs reported with 0 scored cells are out of scope, not "
        "broken: the North Cascades districts are orogenic gold in metamorphic "
        "rocks and this model has nothing to say about them."
    )
    add("")

    add("| AOI | Label | Cells | Scored | Cov | Mean | Max | Flatness "
        "| Working pct | recall@high |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for name, v in result["per_aoi"].items():
        if v.get("error"):
            add(f"| {name} | {v.get('label')} | — | — | — | — | — | — | — | — |")
            continue
        add(
            f"| {name} | {v.get('label')} | {v['cells']} | {v['scored']} "
            f"| {v['coverage']:.2f} | {_fmt(v.get('mean'))} | {_fmt(v.get('max'))} "
            f"| {_fmt(v.get('flatness'))} "
            f"| {_fmt(v.get('mean_working_percentile'))} "
            f"| {_fmt(v.get('recall_at_high'))} |"
        )
    add("")

    in_scope = [n for n, v in result["per_aoi"].items() if v.get("scored")]
    refused = [
        n
        for n, v in result["per_aoi"].items()
        if not v.get("error") and not v.get("scored")
    ]
    add(
        f"**{len(in_scope)} of {len(result['per_aoi'])} labelled AOIs are inside "
        f"the OF-00-495 footprint**"
        + (f": {', '.join(in_scope)}." if in_scope else ".")
    )
    add("")

    if refused:
        add("## Refused — outside the published model's footprint")
        add("")
        add(
            "These AOIs get no baseline score at all, and an extrapolated number "
            "here would be worse than none:"
        )
        add("")
        for name in refused:
            add(f"- {name} ({result['per_aoi'][name].get('label')})")
        add("")
        add(
            "**Consequence for the benchmark:** the deterministic baseline cannot "
            "sanity-check western Washington at all. Every AOI in the priority "
            "corridor is in this list, so for those the LLM composite has nothing "
            "independent to be checked against except the known workings."
        )
        add("")

    add("## Tracts and refusals per AOI")
    add("")
    for name, v in result["per_aoi"].items():
        if v.get("error"):
            add(f"- `{name}`: {v['error']}")
            continue
        bits = []
        if v.get("tracts"):
            bits.append(
                "tracts "
                + ", ".join(f"{k} {n}" for k, n in sorted(v["tracts"].items()))
            )
        for reason, n in sorted((v.get("refusals") or {}).items()):
            bits.append(f"{n} refused — {reason}")
        if v.get("workings_refused"):
            bits.append(f"ground truth refused — {v['workings_refused']}")
        if v.get("workings_outside_scored_area"):
            bits.append(
                f"{v['workings_outside_scored_area']} working(s) fell outside the "
                f"scored area"
            )
        add(f"- `{name}`: " + ("; ".join(bits) if bits else "nothing to report"))
    add("")
    add(
        "A working percentile near 1.0 here means the published statistical model "
        "puts recorded workings in the top cells of the AOI, i.e. the ground truth "
        "and the baseline agree. That is the number an LLM run has to beat to be "
        "worth its tokens."
    )
    add("")
    return "\n".join(lines)


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.4f}"


# --- main ------------------------------------------------------------------


def _square_aoi(lon: float, lat: float, radius_km: float) -> Dict[str, Any]:
    """A square AOI of ``radius_km`` half-width around a point, in WGS84."""
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * math.cos(math.radians(lat)))
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - dlon, lat - dlat],
                    [lon + dlon, lat - dlat],
                    [lon + dlon, lat + dlat],
                    [lon - dlon, lat + dlat],
                    [lon - dlon, lat - dlat],
                ]
            ],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-dir", default=str(RUNS_DIR))
    ap.add_argument("--labels", default=None)
    ap.add_argument("--baseline", help="frozen benchmark JSON to compare against")
    ap.add_argument("--freeze", help="write the current result as a new baseline")
    ap.add_argument(
        "--noise-floor",
        action="store_true",
        help="report only the nondeterminism floor and exit",
    )
    ap.add_argument(
        "--disable-toponyms",
        action="store_true",
        help=(
            "exclude AOIs whose positives are legible from their own place "
            "names. The standard report shows both; this forces the blind view."
        ),
    )
    ap.add_argument(
        "--wofe-only",
        action="store_true",
        help=(
            "score the labelled AOIs with the deterministic OF01-501 model alone "
            "and exit — no run records needed, no tokens spent"
        ),
    )
    ap.add_argument(
        "--wofe-resolution",
        type=int,
        default=WOFE_ONLY_RESOLUTION_M,
        help=(
            "cell size for --wofe-only, metres. Snapped to RESOLUTION_LADDER. "
            "Coarser than 250 m is measurable but less informative — the WofE "
            "predictors are presence tests and saturate."
        ),
    )
    ap.add_argument("--out", help="write the markdown report here as well as stdout")
    args = ap.parse_args()

    labels = load_labels(Path(args.labels) if args.labels else None)

    if args.wofe_only:
        report = render_wofe_only(labels, args.wofe_resolution)
        print(report)
        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
        return 0

    runs = load_runs(Path(args.runs_dir))
    if not runs:
        print(
            f"No run records in {args.runs_dir}. Run an analysis first — every "
            f"run writes one.",
            file=sys.stderr,
        )
        return 1

    matched = match_runs_to_aois(runs, labels)
    excluded: List[str] = []

    unmatched = sum(1 for r in runs if r.get("status") == "completed") - sum(
        len(v) for v in matched.values()
    )
    if unmatched:
        excluded.append(
            f"{unmatched} completed run(s) whose AOI centroid matched no "
            f"benchmark AOI within 0.15°"
        )
    failed = sum(1 for r in runs if r.get("status") != "completed")
    if failed:
        excluded.append(f"{failed} run(s) with status != completed")

    if args.disable_toponyms:
        dropped = [
            n for n, v in matched.items()
            if labels["aois"][n].get("toponym_revealing")
        ]
        for n in dropped:
            matched.pop(n)
        if dropped:
            excluded.append(
                f"--disable-toponyms dropped {len(dropped)} AOI(s): "
                + ", ".join(sorted(dropped))
            )

    floor = noise_floor(matched)

    if args.noise_floor:
        if floor:
            print(json.dumps(floor, indent=2))
            return 0
        print(
            "Noise floor not established: need ≥2 runs of the same AOI on the "
            "same clean commit.",
            file=sys.stderr,
        )
        return 1

    result = aggregate(matched, labels)
    if floor:
        result["noise_floor"] = floor

    report = render(result, labels, floor, _load_baseline(args.baseline), excluded)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")

    if args.freeze:
        p = Path(args.freeze)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nFroze baseline → {p}", file=sys.stderr)

    return 0


def _load_baseline(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"Baseline {p} not found — reporting without a delta", file=sys.stderr)
        return None
    return json.loads(p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
