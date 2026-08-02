#!/usr/bin/env python3
"""
Build the static GNIS Washington extract used by the toponym matcher.

Downloads the USGS Domestic Names bulk file for Washington and reduces it to
the columns the matcher needs. A static file rather than the live service:
no runtime dependency, no rate limit, works offline, and deterministic across
runs — which is what the benchmark requires.

    python scripts/build_gnis_extract.py

Writes data/reference/gnis_wa.tsv (~1 MB) and a companion .json manifest.

Note on stream coordinates: GNIS places a stream's primary coordinate at its
*mouth*, which can be kilometres from the workings that named it. The bulk file
also carries source (headwaters) coordinates for every stream, and both are
preserved here so the matcher can attribute a name along the creek rather than
only at its confluence.

Note on the Mine feature class: GNIS retired administrative classes in an
earlier revision, so there is no `Mine` class to key on. This script reports the
classes actually present so that stays visible rather than assumed.
"""
import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "reference"
SOURCE_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/"
    "DomesticNames/DomesticNames_WA_Text.zip"
)

COLUMNS = [
    "feature_id",
    "name",
    "feature_class",
    "county",
    "map_name",
    "lat",
    "lon",
    "source_lat",
    "source_lon",
]


def download(url: str) -> bytes:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def build(raw_zip: bytes, out_path: Path) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    member = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
    text = zf.read(member).decode("utf-8-sig")

    rows = []
    classes: Counter = Counter()
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    for r in reader:
        # The bulk file includes a handful of out-of-state features whose
        # records happen to be maintained by WA; keep only WA ones.
        if (r.get("state_name") or "").strip() != "Washington":
            continue
        try:
            lat = float(r["prim_lat_dec"])
            lon = float(r["prim_long_dec"])
        except (TypeError, ValueError):
            continue
        if lat == 0.0 and lon == 0.0:
            continue
        classes[r.get("feature_class", "")] += 1
        rows.append(
            {
                "feature_id": r.get("feature_id", ""),
                "name": r.get("feature_name", ""),
                "feature_class": r.get("feature_class", ""),
                "county": r.get("county_name", ""),
                "map_name": r.get("map_name", ""),
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "source_lat": _num(r.get("source_lat_dec")),
                "source_lon": _num(r.get("source_long_dec")),
            }
        )

    rows.sort(key=lambda x: (x["feature_class"], x["name"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    return {
        "source_url": SOURCE_URL,
        "member": member,
        "rows": len(rows),
        "feature_classes": dict(classes.most_common()),
        "has_mine_class": "Mine" in classes,
        "streams_with_source_coords": sum(
            1 for r in rows if r["feature_class"] == "Stream" and r["source_lat"]
        ),
    }


def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if f == 0.0 else f"{f:.6f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="use a local copy instead of downloading")
    ap.add_argument("--out", default=str(OUT_DIR / "gnis_wa.tsv"))
    args = ap.parse_args()

    raw = Path(args.zip).read_bytes() if args.zip else download(SOURCE_URL)
    out_path = Path(args.out)
    manifest = build(raw, out_path)

    manifest_path = out_path.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Wrote {manifest['rows']} names to {out_path}")
    print(f"  streams with source coords: {manifest['streams_with_source_coords']}")
    print(f"  `Mine` feature class present: {manifest['has_mine_class']}")
    if not manifest["has_mine_class"]:
        print(
            "  (expected — GNIS retired administrative classes; historical\n"
            "   points and the USGS topo basemap carry mine symbols instead)"
        )
    top = list(manifest["feature_classes"].items())[:8]
    print("  top classes: " + ", ".join(f"{k}={v}" for k, v in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
