#!/usr/bin/env python3
"""
Derive real ``known_workings`` for ``benchmarks/labels.yaml`` from WA DNR data.

This is the verification the labels file's own header asks for. Until now every
``known_workings`` list was ``[]`` with a ``TODO``, so ``scripts/benchmark.py``
refused to report working-percentile or recall@high at all — correctly, because
the only coordinates available were approximate district centres and a district
centre masquerading as a mine site produces confident numbers about the wrong
cells.

``data/reference/wa_occurrences.geojson`` (built by
``scripts/build_reference_extracts.py`` from the WA DNR / WGS Mines & Minerals
geodatabase) carries a per-site positional accuracy class. That is what makes
this possible: the sites can be filtered down to the ones whose coordinates are
survey- or topo-grade and used as ground truth, while the rest are kept out.

Filter rules, and why each one
------------------------------
* **``accuracy_class`` must be ``survey`` or ``topo``.** Nothing else.
  - ``district_centroid`` is excluded absolutely. It is a district centre stored
    as a point — the exact failure mode the labels header warns about, and
    invariant 6 of the change-set contract.
  - ``variable`` ("coordinate accuracy highly variable") is excluded too, even
    though it is 917 of the 1467 gold/silver rows and dropping it costs most of
    the data. A coordinate that might be a kilometre out cannot say whether a
    250 m–1000 m cell scored well.
  - ``derived`` (estimated from a location or legal description) is excluded for
    the same reason: a quarter-section estimate is coarser than the grid.
* **Gold-relevant only.** ``commodity_primary == "Gold (Au)"`` or gold named in
  ``commodities``. The AOIs are gold benchmarks; a silver-only or copper-only
  prospect is not evidence that a gold model should have scored that cell high.
* **Nearest labelled AOI within its ``radius_km``.** Monte Cristo and Silver
  Creek are 8 km apart, so a site inside both radii must go to exactly one —
  the nearer.
* **Ordered production first, then assays, then distance.** A site with recorded
  production is the strongest possible statement that ore was actually there.

What this script deliberately does not do
-----------------------------------------
It does **not** touch ``approx_center``, and it does **not** flip the global
``verified`` flag. Those are two different claims:

* ``verified`` (global) means *a human has checked the AOI centre coordinates*.
  Nothing here checks them, so it stays ``false``.
* ``workings_verified`` (per AOI, new) means *this AOI's ``known_workings`` are
  survey- or topo-grade positions from a named authoritative source*. That is
  what working-percentile and recall@high actually depend on — the metric reads
  working coordinates, not AOI centres — so it is the correct gate, and it is
  per-AOI because some AOIs get workings and some do not.

Setting one global boolean true would have defeated the benchmark's refusal
rather than satisfied it. Per-AOI, the harness can report for the AOIs where the
ground truth is real and keep refusing, with a printed reason, everywhere else.

An AOI labelled ``null`` that nonetheless picks up survey-grade gold workings is
reported as a **label conflict** and gets ``workings_verified: false``: the
workings are real, so the *label* is what is suspect, and it needs a human.

Comments in ``labels.yaml`` are load-bearing documentation, so the file is
rewritten by line-oriented surgery rather than by a YAML dumper (which would
drop every ``#``). Managed keys are replaced in place; everything else, comments
included, survives byte-for-byte. Trailing comments on a managed key are
preserved as ``# previously:`` lines rather than silently discarded.

Usage
-----
    .venv/bin/python scripts/build_labels_workings.py            # rewrite in place
    .venv/bin/python scripts/build_labels_workings.py --dry-run  # print, touch nothing
    .venv/bin/python scripts/build_labels_workings.py --out /tmp/labels.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
LABELS_PATH = REPO_ROOT / "benchmarks" / "labels.yaml"
OCCURRENCES_PATH = REPO_ROOT / "data" / "reference" / "wa_occurrences.geojson"

#: Positional accuracy classes good enough to be ground truth for a 250-1000 m
#: grid cell. See the module docstring for why the other four are out.
GROUND_TRUTH_ACCURACY = ("survey", "topo")

#: Excluded, named explicitly so the exclusion is auditable rather than implied
#: by the absence of a class from the list above.
EXCLUDED_ACCURACY = ("district_centroid", "variable", "derived", "unknown")

#: Default assignment radius, km. A hand-drawn district AOI is typically 5-12 km
#: across (the UI enforces a 25 km² minimum), and the labelled ``approx_center``
#: values are themselves district centres good to a few kilometres. 6 km is the
#: smallest radius at which every *positive* AOI picks up at least one
#: survey/topo gold working — measured, not guessed.
DEFAULT_RADIUS_KM = 6.0

#: Keys this script owns inside an AOI block. Everything else in the file is
#: passed through untouched.
MANAGED_AOI_KEYS = ("radius_km", "workings_verified", "known_workings")

#: Fenced regions this script owns. Everything between a BEGIN and its END is
#: stripped before the rewrite and regenerated afterwards, which is what makes
#: repeated runs idempotent instead of accumulating duplicate banners.
BANNER_BEGIN = "# >>> build_labels_workings status block (regenerated; do not edit)"
BANNER_END = "# <<< end build_labels_workings status block"
PROV_BEGIN = "# >>> build_labels_workings provenance (regenerated; do not edit)"
PROV_END = "# <<< end build_labels_workings provenance"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def km_between(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Equirectangular approximation. Good to <0.1% at these distances."""
    mid = math.radians((lat1 + lat2) / 2.0)
    dx = (lon2 - lon1) * 111.320 * math.cos(mid)
    dy = (lat2 - lat1) * 110.574
    return math.hypot(dx, dy)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def is_gold_relevant(props: Dict[str, Any]) -> bool:
    if (props.get("commodity_primary") or "") == "Gold (Au)":
        return True
    return "gold" in (props.get("commodities") or "").lower()


def load_occurrences(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(
            f"No occurrence extract at {path}.\n"
            f"Build it first:  .venv/bin/python scripts/build_reference_extracts.py "
            f"occurrences"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc.get("features") or [], doc.get("properties") or {}


def select_workings(
    features: Sequence[Dict[str, Any]],
    aois: Dict[str, Dict[str, Any]],
    radius_default: float,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, int]]:
    """Assign qualifying sites to labelled AOIs. Returns (per-AOI list, counts)."""
    counts = {
        "features_read": len(features),
        "excluded_by_accuracy": 0,
        "excluded_not_gold": 0,
        "candidates": 0,
        "assigned": 0,
        "unassigned_no_aoi_in_range": 0,
    }
    per_aoi: Dict[str, List[Dict[str, Any]]] = {name: [] for name in aois}

    radii = {
        name: float(spec.get("radius_km") or radius_default)
        for name, spec in aois.items()
    }
    centers = {name: tuple(spec["approx_center"]) for name, spec in aois.items()}

    for feat in features:
        props = feat.get("properties") or {}
        if props.get("accuracy_class") not in GROUND_TRUTH_ACCURACY:
            counts["excluded_by_accuracy"] += 1
            continue
        if not is_gold_relevant(props):
            counts["excluded_not_gold"] += 1
            continue
        counts["candidates"] += 1

        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])

        best: Optional[str] = None
        best_d = float("inf")
        for name, (cx, cy) in centers.items():
            d = km_between(cx, cy, lon, lat)
            if d <= radii[name] and d < best_d:
                best, best_d = name, d
        if best is None:
            counts["unassigned_no_aoi_in_range"] += 1
            continue

        per_aoi[best].append(
            {
                "site_uid": props.get("uid"),
                "name": props.get("name") or "",
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "accuracy_class": props.get("accuracy_class"),
                "assays": bool(props.get("assays")),
                "production": bool(props.get("production")),
                "distance_km": round(best_d, 2),
                "source_layer": props.get("source_layer"),
            }
        )
        counts["assigned"] += 1

    for name in per_aoi:
        # Production first, then assays, then distance. Stable and reproducible.
        per_aoi[name].sort(
            key=lambda w: (
                not w["production"],
                not w["assays"],
                w["distance_km"],
                w["site_uid"] or "",
            )
        )
    return per_aoi, counts


# ---------------------------------------------------------------------------
# YAML emission (hand-rolled, to preserve comments)
# ---------------------------------------------------------------------------


def _yaml_str(value: Any) -> str:
    """Quote only when needed, so the diff stays small and readable."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    if s == "" or re.search(r'[:#\'"\[\]{},&*?|>%@`]|^\s|\s$|^-', s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def render_workings(workings: Sequence[Dict[str, Any]], indent: str) -> List[str]:
    if not workings:
        return [f"{indent}known_workings: []"]
    lines = [f"{indent}known_workings:"]
    for w in workings:
        lines.append(
            f"{indent}  - {{site_uid: {_yaml_str(w['site_uid'])}, "
            f"name: {_yaml_str(w['name'])}, "
            f"lon: {w['lon']}, lat: {w['lat']}, "
            f"accuracy_class: {_yaml_str(w['accuracy_class'])}, "
            f"assays: {_yaml_str(w['assays'])}, "
            f"production: {_yaml_str(w['production'])}, "
            f"distance_km: {w['distance_km']}, "
            f"source_layer: {_yaml_str(w['source_layer'])}}}"
        )
    return lines


def render_provenance(block: Dict[str, Any], indent: str = "") -> List[str]:
    """Minimal block-style YAML for the provenance mapping. Scalars/lists only."""
    out: List[str] = []
    for key, value in block.items():
        if isinstance(value, dict):
            out.append(f"{indent}{key}:")
            out.extend(render_provenance(value, indent + "  "))
        elif isinstance(value, (list, tuple)):
            if not value:
                out.append(f"{indent}{key}: []")
                continue
            out.append(f"{indent}{key}:")
            for item in value:
                if isinstance(item, dict):
                    inner = render_provenance(item, indent + "      ")
                    first = inner[0].strip()
                    out.append(f"{indent}  - {first}")
                    out.extend(inner[1:])
                else:
                    out.append(f"{indent}  - {_yaml_str(item)}")
        else:
            out.append(f"{indent}{key}: {_yaml_str(value)}")
    return out


# ---------------------------------------------------------------------------
# Line-oriented surgery on labels.yaml
# ---------------------------------------------------------------------------

_AOI_KEY_RE = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*):\s*$")


def _key_of(line: str, indent: int) -> Optional[str]:
    m = re.match(rf"^ {{{indent}}}([A-Za-z_][A-Za-z0-9_]*):", line)
    return m.group(1) if m else None


def _split_trailing_comment(line: str) -> str:
    """The trailing ``# ...`` of a line, or ``''``. Naive but adequate here."""
    i = line.find("#")
    return line[i:].rstrip() if i > 0 else ""


def strip_generated_regions(text: str) -> str:
    """Remove previously generated fenced regions so a rerun is idempotent."""
    out: List[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in (BANNER_BEGIN, PROV_BEGIN):
            skipping = True
            continue
        if stripped in (BANNER_END, PROV_END):
            skipping = False
            continue
        if not skipping:
            out.append(line)
    # Removing a fence leaves the blank line that surrounded it, so a rerun would
    # grow one blank line per pass. Collapse runs of blanks; the hand-written
    # file has none, so nothing authored is lost.
    collapsed: List[str] = []
    for line in out:
        if line.strip() == "" and collapsed and collapsed[-1].strip() == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed)


def rewrite_labels(
    text: str,
    per_aoi: Dict[str, List[Dict[str, Any]]],
    verified_flags: Dict[str, bool],
    radii: Dict[str, float],
    provenance: Dict[str, Any],
    version: str,
    banner: Sequence[str],
) -> str:
    """Replace the managed keys, keep everything else — comments included."""
    lines = strip_generated_regions(text).splitlines()
    out: List[str] = []

    i = 0
    in_aois = False
    current_aoi: Optional[str] = None
    aoi_body: List[str] = []
    banner_written = False

    def flush_aoi() -> None:
        """Emit the current AOI's surviving lines, then its managed keys."""
        nonlocal aoi_body, current_aoi
        if current_aoi is None:
            return
        kept: List[str] = []
        skip_until_dedent = False
        for line in aoi_body:
            if skip_until_dedent:
                # Continuation of a dropped key: deeper indent, a list item, or
                # a blank line inside the block.
                if line.strip() == "" or line.startswith("      ") or re.match(
                    r"^\s+-\s", line
                ):
                    continue
                skip_until_dedent = False
            key = _key_of(line, 4)
            if key in MANAGED_AOI_KEYS:
                if _split_trailing_comment(line):
                    # A TODO or note attached to the key we are replacing. It is
                    # the record of what someone intended; keep it.
                    kept.append(f"    # previously: {line.strip()}")
                skip_until_dedent = True
                continue
            kept.append(line)

        # A run of comments at the end of a block is almost always a heading for
        # the *next* block ("# --- Nulls ---", "# NE Washington. Absent from the
        # original draft list…"). Appending generated keys after it would leave
        # the heading dangling inside the wrong AOI, so it is moved back out.
        trailer: List[str] = []
        while kept and (kept[-1].strip() == "" or kept[-1].lstrip().startswith("#")):
            trailer.insert(0, kept.pop())
        while trailer and trailer[0].strip() == "":
            trailer.pop(0)
        while trailer and trailer[-1].strip() == "":
            trailer.pop()

        out.append(f"  {current_aoi}:")
        out.extend(kept)
        out.append(f"    radius_km: {radii[current_aoi]}")
        out.append(
            f"    workings_verified: "
            f"{_yaml_str(bool(verified_flags.get(current_aoi)))}"
        )
        out.extend(render_workings(per_aoi.get(current_aoi) or [], "    "))
        out.append("")
        if trailer:
            out.extend(trailer)
            out.append("")
        aoi_body = []
        current_aoi = None

    while i < len(lines):
        line = lines[i]

        # --- header: append the status banner just after `verified:` ---------
        if not in_aois and line.startswith("version:"):
            out.append(f'version: "{version}"')
            i += 1
            continue
        if not in_aois and line.startswith("verified:"):
            out.append(line)
            if not banner_written:
                out.append("")
                out.append(BANNER_BEGIN)
                out.extend(banner)
                out.append(BANNER_END)
                banner_written = True
            i += 1
            continue

        if line.startswith("aois:"):
            in_aois = True
            out.append(line)
            i += 1
            continue

        if in_aois:
            m = _AOI_KEY_RE.match(line)
            if m:
                flush_aoi()
                current_aoi = m.group(1)
                i += 1
                continue
            if current_aoi is not None:
                if line and not line.startswith("  "):
                    # Dedent out of `aois:` entirely.
                    flush_aoi()
                    in_aois = False
                    out.append(line)
                    i += 1
                    continue
                aoi_body.append(line)
                i += 1
                continue

        out.append(line)
        i += 1

    flush_aoi()

    while out and out[-1].strip() == "":
        out.pop()
    out.append("")
    out.append(PROV_BEGIN)
    out.extend(render_provenance({"workings_provenance": provenance}))
    out.append(PROV_END)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build_banner(
    n_verified: int, n_aois: int, conflicts: Sequence[str], generated: str
) -> List[str]:
    """The machine-generated status note, inserted below the human header.

    Deliberately just the *status* — counts, the date, and the conflicts. The
    prose explaining the two-level verification lives in the hand-written header
    above it, and restating it here would give the file two copies of the same
    argument that could drift apart.
    """
    lines = [
        f"# Regenerated {generated[:10]} by scripts/build_labels_workings.py.",
        "#",
        f"# {n_verified} of {n_aois} AOIs have `workings_verified: true`. Filter rules,",
        "# source hash and per-AOI counts are in `workings_provenance` at the foot of",
        "# this file; re-run the script to refresh both blocks.",
    ]
    if conflicts:
        lines += [
            "#",
            "# LABEL CONFLICTS — a human needs to look at these. Each is an AOI",
            "# labelled `null` or `control` that nonetheless contains survey- or",
            "# topo-grade gold workings. The workings are real, so it is the label",
            "# that is suspect; `workings_verified` is false for them.",
        ]
        for c in conflicts:
            lines.append(f"#   - {c}")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--labels", default=str(LABELS_PATH))
    ap.add_argument("--occurrences", default=str(OCCURRENCES_PATH))
    ap.add_argument("--out", default=None, help="write here instead of in place")
    ap.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args(argv)

    import yaml

    labels_path = Path(args.labels)
    if not labels_path.exists():
        raise SystemExit(f"No labels file at {labels_path}")
    text = labels_path.read_text(encoding="utf-8")
    labels = yaml.safe_load(text)
    aois: Dict[str, Dict[str, Any]] = labels.get("aois") or {}
    if not aois:
        raise SystemExit(f"{labels_path} has no `aois:` mapping")

    occ_path = Path(args.occurrences)
    features, occ_props = load_occurrences(occ_path)
    per_aoi, counts = select_workings(features, aois, args.radius_km)

    # --- verification flags, and label conflicts -------------------------
    verified_flags: Dict[str, bool] = {}
    conflicts: List[str] = []
    for name, spec in aois.items():
        label = str(spec.get("label") or "")
        workings = per_aoi.get(name) or []
        if not workings:
            verified_flags[name] = False
            continue
        if label == "positive":
            verified_flags[name] = True
        else:
            # Real workings inside ground labelled as barren. Do not quietly use
            # them, and do not quietly drop them either.
            verified_flags[name] = False
            conflicts.append(
                f"{name} (label: {label}) has {len(workings)} survey/topo gold "
                f"working(s) within {aois[name].get('radius_km', args.radius_km)} "
                f"km, nearest {workings[0]['distance_km']} km "
                f"({workings[0]['name']})"
            )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n_verified = sum(1 for v in verified_flags.values() if v)

    provenance: Dict[str, Any] = {
        "generated_at": generated,
        "generator": "scripts/build_labels_workings.py",
        "source_file": str(occ_path.relative_to(REPO_ROOT)),
        "source_sha256": hashlib.sha256(occ_path.read_bytes()).hexdigest(),
        "source_built_at": occ_props.get("built_at"),
        "source_name": occ_props.get("source"),
        "source_layers": list(occ_props.get("layers") or []),
        "source_feature_count": occ_props.get("count"),
        "assignment": (
            "nearest labelled AOI whose approx_center is within radius_km of the site"
        ),
        "radius_km_default": args.radius_km,
        "accuracy_classes_accepted": list(GROUND_TRUTH_ACCURACY),
        "accuracy_classes_excluded": list(EXCLUDED_ACCURACY),
        "commodity_filter": (
            'commodity_primary == "Gold (Au)" or "gold" in commodities'
        ),
        "ordering": "production desc, assays desc, distance asc, uid asc",
        "counts": counts,
        "aois_with_verified_workings": sorted(
            n for n, v in verified_flags.items() if v
        ),
        "aois_without_workings": sorted(
            n for n in aois if not (per_aoi.get(n) or [])
        ),
        "label_conflicts": conflicts,
        "notes": [
            "approx_center values are untouched and still unverified; the global "
            "`verified` flag stays false.",
            "working-percentile and recall@high depend on these working "
            "coordinates, not on approx_center, which is why they are gated on "
            "per-AOI workings_verified instead.",
        ],
    }

    version = f"{generated[:10]}-workings"
    banner = build_banner(n_verified, len(aois), conflicts, generated)
    new_text = rewrite_labels(
        text,
        per_aoi,
        verified_flags,
        {n: float(aois[n].get("radius_km") or args.radius_km) for n in aois},
        provenance,
        version,
        banner,
    )

    # --- prove the result is still valid YAML and still says what we meant ---
    reparsed = yaml.safe_load(new_text)
    _assert_round_trip(labels, reparsed, per_aoi, verified_flags)

    print(f"Occurrence extract: {occ_path}")
    print(f"  features read            {counts['features_read']}")
    print(f"  excluded by accuracy     {counts['excluded_by_accuracy']}")
    print(f"  excluded, not gold       {counts['excluded_not_gold']}")
    print(f"  survey/topo gold sites   {counts['candidates']}")
    print(f"  assigned to an AOI       {counts['assigned']}")
    print(f"  in range of no AOI       {counts['unassigned_no_aoi_in_range']}")
    print()
    print(f"{'AOI':34s} {'label':9s} {'n':>3s}  prod  assay  verified")
    for name in sorted(aois):
        w = per_aoi.get(name) or []
        print(
            f"{name:34s} {str(aois[name].get('label')):9s} {len(w):3d}  "
            f"{sum(1 for x in w if x['production']):4d}  "
            f"{sum(1 for x in w if x['assays']):5d}  "
            f"{'yes' if verified_flags[name] else 'no'}"
        )
    print()
    for c in conflicts:
        print(f"LABEL CONFLICT: {c}")
    print(
        f"\n{n_verified} of {len(aois)} AOIs have verified workings. "
        f"Global `verified` stays false (approx_center unchecked)."
    )

    if args.dry_run:
        print("\n--dry-run: nothing written.", file=sys.stderr)
        return 0

    out_path = Path(args.out) if args.out else labels_path
    out_path.write_text(new_text, encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


def _assert_round_trip(
    before: Dict[str, Any],
    after: Dict[str, Any],
    per_aoi: Dict[str, List[Dict[str, Any]]],
    verified_flags: Dict[str, bool],
) -> None:
    """Fail loudly rather than write a labels file that lost something.

    Hand-rolled YAML surgery is exactly the kind of code that silently eats a
    key. Everything that is not a managed key must survive identically, and the
    managed keys must come back as what was intended.
    """
    if set(before["aois"]) != set(after.get("aois") or {}):
        raise SystemExit("BUG: rewrite changed the set of AOIs — refusing to write")
    if after.get("verified") is not before.get("verified"):
        raise SystemExit("BUG: rewrite changed the global `verified` flag")
    for name, spec in before["aois"].items():
        new = after["aois"][name]
        for key, value in spec.items():
            if key in MANAGED_AOI_KEYS:
                continue
            if new.get(key) != value:
                raise SystemExit(
                    f"BUG: rewrite changed {name}.{key}: {value!r} -> {new.get(key)!r}"
                )
        if new.get("approx_center") != spec.get("approx_center"):
            raise SystemExit(f"BUG: rewrite moved {name}.approx_center")
        got = new.get("known_workings") or []
        want = per_aoi.get(name) or []
        if len(got) != len(want):
            raise SystemExit(
                f"BUG: {name} has {len(got)} workings after rewrite, expected "
                f"{len(want)}"
            )
        for g, w in zip(got, want):
            if (
                g.get("site_uid") != w["site_uid"]
                or abs(float(g.get("lon")) - w["lon"]) > 1e-9
                or abs(float(g.get("lat")) - w["lat"]) > 1e-9
            ):
                raise SystemExit(f"BUG: {name} working round-trip mismatch: {g}")
        if bool(new.get("workings_verified")) is not bool(verified_flags[name]):
            raise SystemExit(f"BUG: {name}.workings_verified did not round-trip")


if __name__ == "__main__":
    sys.exit(main())
