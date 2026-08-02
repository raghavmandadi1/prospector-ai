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

Three things this deliberately refuses to do:

* **Report working-percentile or recall while `labels.yaml` says
  `verified: false`.** Draft coordinates produce confident, meaningless numbers.
* **Call a delta an improvement when it is smaller than the measured noise
  floor.** LLM output is nondeterministic; without that comparison a team can
  spend months tuning noise.
* **Quietly hide what it dropped.** Every exclusion is printed.
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

BENCH_DIR = REPO_ROOT / "benchmarks"
RUNS_DIR = REPO_ROOT / "data" / "runs"


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


def percentile_of_point(
    run: Dict[str, Any], lon: float, lat: float
) -> Optional[float]:
    """Percentile rank of the cell containing a point, within its own AOI.

    Uses the stored absolute scores and recomputes the rank here rather than
    trusting the run's stored `percentile`, so the metric is independent of
    whatever normalisation the run happened to apply.
    """
    from app.scoring.grid import cell_id_for_point, parse_cell_id

    cells = run.get("composite_cells") or []
    if not cells:
        return None
    try:
        res, _, _ = parse_cell_id(cells[0]["cell_id"])
    except (ValueError, KeyError):
        return None

    target = cell_id_for_point(lon, lat, res)
    by_id = {c["cell_id"]: c.get("score", 0.0) for c in cells}
    if target not in by_id:
        return None

    scores = sorted(by_id.values())
    s = by_id[target]
    lo = sum(1 for v in scores if v < s)
    hi = sum(1 for v in scores if v <= s)
    return ((lo + hi) / 2) / len(scores)


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

    workings = spec.get("known_workings") or []
    if workings:
        pcts = []
        for w in workings:
            p = percentile_of_point(run, w["lon"], w["lat"])
            if p is not None:
                pcts.append(p)
        if pcts:
            m["working_percentiles"] = [round(p, 4) for p in pcts]
            m["mean_working_percentile"] = round(statistics.fmean(pcts), 4)
            m["recall_at_high"] = round(
                sum(1 for p in pcts if p >= 0.90) / len(pcts), 4
            )
    return m


def aggregate(
    matched: Dict[str, List[Dict[str, Any]]], labels: Dict[str, Any]
) -> Dict[str, Any]:
    aois = labels["aois"]
    per_aoi: Dict[str, Any] = {}
    for name, runs in matched.items():
        spec = aois[name]
        metrics = [run_metrics(r, spec) for r in runs]
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
    return {"per_aoi": per_aoi, "summary": summary}


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

    add("# GeoProspector benchmark")
    add("")
    add(f"- labels: `{labels.get('version')}` (verified: **{verified}**)")
    add(f"- AOIs with runs: {s['aois_covered']} / {s['aois_labelled']} labelled")
    add("")

    if not verified:
        add("> ⚠ **Ground truth is unverified.**")
        add("> `benchmarks/labels.yaml` has `verified: false`, so every coordinate in")
        add("> it is an approximate district centre rather than a survey position.")
        add("> Working-percentile and recall@high are therefore **not reported** —")
        add("> they would be confident numbers about the wrong cells. Separation,")
        add("> flatness and grounded fraction do not depend on point coordinates and")
        add("> are reported below.")
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
    add("| AOI | Label | Runs | Mean | Flatness | Grounded | Toponym-revealing |")
    add("|---|---|---|---|---|---|---|")
    for name, v in sorted(result["per_aoi"].items()):
        add(
            f"| {name} | {v['label']} | {v['n_runs']} | {v['mean_composite']:.4f} "
            f"| {v['flatness']:.4f} | {v['grounded_fraction']:.2f} "
            f"| {'yes' if v['toponym_revealing'] else 'no'} |"
        )
    add("")
    add(
        "*Flatness* is the standard deviation of composite within the AOI. Near "
        "zero means the agents are not discriminating and the map is decorative."
    )
    add("")

    if baseline:
        add("## Delta vs baseline")
        add("")
        bs = baseline.get("summary", {})
        add("| Metric | Baseline | Now | Δ | Exceeds noise floor? |")
        add("|---|---|---|---|---|")
        for key in ("mean_positive", "mean_null", "separation", "separation_blind",
                    "grounded_fraction"):
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


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.4f}"


# --- main ------------------------------------------------------------------


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
    ap.add_argument("--out", help="write the markdown report here as well as stdout")
    args = ap.parse_args()

    labels = load_labels(Path(args.labels) if args.labels else None)
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
