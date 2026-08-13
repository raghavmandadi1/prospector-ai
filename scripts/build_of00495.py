#!/usr/bin/env python3
"""
Build ``data/derived/of00495.sqlite`` from the four USGS OF-00-495 ArcInfo grids.

OF-00-495 (Boleneus & Causey, 2000) is *Geologic raster data for
weights-of-evidence analysis in NE Washington* — the six 1:100,000 quadrangles
(Colville, Chewelah, Republic, Nespelem, Omak, Oroville) that contain most of
Washington's lode gold. It matters here for one specific reason: its lithology
grid is labelled with the **same unit codes as the published OF01-501 WofE
contrast table** (``Eck`` 4.55, ``Evkct`` 3.62, ``Evst`` 3.42, ``Evsf`` 3.21,
``Evkf`` 2.56, ``Eco`` 1.96 — see ``knowledge/lithology/gold.md``). The WA DNR
1:24k geology on disk does *not* use those codes (verified absent from all 2186
distinct ``GUNIT_TXT`` values), so this is the only dataset that can turn "this
cell is favourable" into a number someone else published.

What this produces: one row per **250 m EPSG:5070 cell** that has at least one
non-nodata source pixel, with the modal lithology label, the modal fault and fold
codes, and the modal dike label. 250 m is a rung of ``RESOLUTION_LADDER``, and
every cell id comes from ``app.scoring.grid.cell_id_for_point``, so these rows
nest inside the 500/1000/2000 m analysis cells the orchestrator actually uses and
join to the cell cache and run records by id alone.

Usage::

    .venv/bin/python scripts/build_of00495.py                    # full build
    .venv/bin/python scripts/build_of00495.py --grids geol       # one layer
    .venv/bin/python scripts/build_of00495.py --limit-rows 200   # smoke test

Notes on accuracy, so nobody over-trusts the output:

* The native CRS is EPSG:26711 (UTM 11N / **NAD27**). PROJ on this machine has
  no NADCON grid installed, so the NAD27 -> WGS84 step falls back to a
  Molodensky-Badekas/Helmert transform whose declared accuracy is 7-20 m. That
  is well inside a 250 m cell, but it is not survey grade, and it is why nothing
  here should be used as a *position* — only as an attribute of a cell.
* Aggregation is modal over source pixels (25 pixels per cell for the 50 m
  grids). ``geol_unit_frac`` reports the modal unit's share of the cell's
  non-nodata pixels, so a value near 0.5 means the cell straddles a contact and
  the single label is a simplification.
* Cells on the edge of the raster footprint may hold only a handful of pixels.
  The fraction still describes what was actually seen, but it is a weaker
  statement than a fully-covered cell. The schema has nowhere to record pixel
  counts, so that distinction is not recoverable downstream.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pyproj

REPO_ROOT = Path(__file__).resolve().parents[1]
# Same import shim scripts/benchmark.py uses: the app package lives under
# backend/, and cell ids must come from the app's own grid module so an offline
# build can never disagree with the runtime about which square of earth a cell is.
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.scoring.grid import (  # noqa: E402
    EPSG_ANALYSIS,
    GRID_ORIGIN_X,
    GRID_ORIGIN_Y,
    GRID_TAG,
    RESOLUTION_LADDER,
    cell_id_for_point,
    cell_id_to_bbox,
    parse_cell_id,
)
from lib.e00 import E00Grid, read_e00_grid  # noqa: E402

logger = logging.getLogger("build_of00495")

#: Analysis resolution for the derived table. A ladder rung, deliberately: 250 m
#: is fine enough that the 50 m lithology grid is not thrown away, coarse enough
#: that the whole footprint is ~300k rows, and it is an exact quadtree ancestor
#: of the 500/1000/2000 m grids the agents score on.
CELL_RES_M = 250

#: Native CRS of all four grids, per of00-495.met and CONTRACT.md.
SOURCE_EPSG = 26711

#: file stem -> logical grid name used in the schema and the CLI.
GRIDS: Dict[str, str] = {
    "geol": "newageol",
    "fault": "newafaul",
    "fold": "newafold",
    "dike": "newadike",
}

#: The six units carrying a published positive WofE contrast (OF01-501,
#: Appendix; mirrored in knowledge/lithology/gold.md). Reported at the end of the
#: build because their presence is the entire justification for this layer.
FAVOURABLE_UNITS = {
    "Eck": 4.55,
    "Evkct": 3.62,
    "Evst": 3.42,
    "Evsf": 3.21,
    "Evkf": 2.56,
    "Eco": 1.96,
}

SOURCE_CITATION = "USGS_OF00_495"

#: Raster rows per aggregation block. Bounds peak memory on the 10.3 M-pixel
#: lithology grid without making the pyproj calls small enough to be call-bound.
BLOCK_ROWS = 256


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

# Two hops on purpose. The second one is reconstructed from the app's own
# EPSG_ANALYSIS constant rather than hardcoded, so it cannot drift away from
# grid.py; every derived (col,row) is then re-checked against
# cell_id_for_point() before it is written, which is the real guarantee.
_TO_WGS84 = pyproj.Transformer.from_crs(
    f"EPSG:{SOURCE_EPSG}", "EPSG:4326", always_xy=True
).transform
_TO_ANALYSIS = pyproj.Transformer.from_crs(
    "EPSG:4326", f"EPSG:{EPSG_ANALYSIS}", always_xy=True
).transform
_FROM_ANALYSIS = pyproj.Transformer.from_crs(
    f"EPSG:{EPSG_ANALYSIS}", "EPSG:4326", always_xy=True
).transform

#: cellkey packing. Cell ids are formatted %06d, so anything outside
#: [0, 1e6) is a bug rather than a wide grid.
_KEY_BASE = 1_000_000


def _pack(col: np.ndarray, row: np.ndarray) -> np.ndarray:
    if col.size and (
        col.min() < 0
        or col.max() >= _KEY_BASE
        or row.min() < 0
        or row.max() >= _KEY_BASE
    ):
        raise ValueError(
            f"Grid indices out of the 6-digit cell-id range: "
            f"col {col.min()}..{col.max()}, row {row.min()}..{row.max()}. "
            f"Either the source CRS is wrong or the footprint is not in Washington."
        )
    return col.astype(np.int64) * _KEY_BASE + row.astype(np.int64)


def _unpack(key: int) -> Tuple[int, int]:
    return int(key // _KEY_BASE), int(key % _KEY_BASE)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _valid_mask(block: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Boolean mask of pixels whose value appears in the VAT.

    The VAT is the authority on what counts as data: ``newafaul`` legitimately
    contains code 0, and a grid can contain values the VAT never declared, which
    are nodata by definition (CONTRACT.md).
    """
    if valid.size == 0:
        return np.zeros(block.shape, dtype=bool)
    if valid.min() >= 0 and valid.max() < 1 << 20:
        lut = np.zeros(int(valid.max()) + 1, dtype=bool)
        lut[valid] = True
        mask = (block >= 0) & (block <= int(valid.max()))
        mask[mask] = lut[block[mask]]
        return mask
    return np.isin(block, valid)  # pragma: no cover - not hit by OF-00-495


def _groups(grid: E00Grid) -> Tuple[List, np.ndarray]:
    """``(group keys, value -> group index lookup)`` for one grid.

    Labelled grids group by **label**, not by raw code, because the VAT can give
    one unit two codes: ``newageol`` maps both 48 and 122 to ``Zmlv`` and both 152
    and 160 to ``Ytar``. Grouping by code would split the modal vote between two
    spellings of the same unit and understate ``geol_unit_frac`` for those cells.
    """
    valid = sorted(int(v) for v in grid.valid_values())
    labmap = grid.label_map()
    by_label = bool(labmap) and len(labmap) == len(valid)
    if labmap and not by_label:
        logger.warning(
            "%s: %d of %d VAT values carry a label — grouping by raw code instead",
            grid.vat_name,
            len(labmap),
            len(valid),
        )

    if by_label:
        keys: List = sorted({labmap[v] for v in valid})
        index = {k: i for i, k in enumerate(keys)}
        pairs = [(v, index[labmap[v]]) for v in valid]
    else:
        keys = list(valid)
        pairs = [(v, i) for i, v in enumerate(valid)]

    lut = np.full((max(valid) + 1) if valid else 1, -1, dtype=np.int64)
    for v, i in pairs:
        lut[v] = i
    return keys, lut


def aggregate(grid: E00Grid, name: str) -> Tuple[List, Dict[int, Tuple[int, int, int]]]:
    """Aggregate one raster onto the 250 m analysis grid.

    Returns ``(group_keys, cellkey -> (group_index, modal_count, total_count))``
    where ``group_keys[group_index]`` is the unit label (labelled grids) or the
    raw code (``newafaul``/``newafold``), and the counts are source pixels with a
    VAT-declared value. Ties on modal count resolve to the lowest group index —
    alphabetically first label, or lowest code — so a rebuild is reproducible.
    """
    if grid.values is None:
        raise ValueError("aggregate() needs a grid read with want_values=True")

    valid = np.array(sorted(grid.valid_values()), dtype=np.int64)
    group_keys, dense_lut = _groups(grid)
    n_groups = max(len(group_keys), 1)

    xc = grid.col_centres()
    yc = grid.row_centres()

    keys: List[np.ndarray] = []
    n_px = 0
    for r0 in range(0, grid.nrows, BLOCK_ROWS):
        r1 = min(r0 + BLOCK_ROWS, grid.nrows)
        block = grid.values[r0:r1]
        mask = _valid_mask(block, valid)
        if not mask.any():
            continue
        rr, cc = np.nonzero(mask)
        vals = block[rr, cc].astype(np.int64)
        lon, lat = _TO_WGS84(xc[cc], yc[r0 + rr])
        x5070, y5070 = _TO_ANALYSIS(lon, lat)
        col = np.floor((x5070 - GRID_ORIGIN_X) / CELL_RES_M).astype(np.int64)
        row = np.floor((y5070 - GRID_ORIGIN_Y) / CELL_RES_M).astype(np.int64)
        keys.append(_pack(col, row) * n_groups + dense_lut[vals])
        n_px += vals.size

    logger.info("  %s: %d source pixels with a VAT value", name, n_px)
    if not keys:
        return group_keys, {}

    uk, uc = np.unique(np.concatenate(keys), return_counts=True)
    cellkeys = uk // n_groups
    dense_idx = uk % n_groups

    # Sort by (cellkey asc, count asc, group index desc) so the last row of each
    # cellkey group is the modal group and ties resolve to the lowest group index.
    order = np.lexsort((-dense_idx, uc, cellkeys))
    cellkeys, dense_idx, uc = cellkeys[order], dense_idx[order], uc[order]

    starts = np.concatenate(([0], np.flatnonzero(np.diff(cellkeys)) + 1))
    ends = np.concatenate((starts[1:], [cellkeys.size]))
    totals = np.add.reduceat(uc, starts)

    return group_keys, {
        int(k): (int(g), int(mc), int(t))
        for k, g, mc, t in zip(
            cellkeys[starts], dense_idx[ends - 1], uc[ends - 1], totals
        )
    }


# ---------------------------------------------------------------------------
# Cell ids
# ---------------------------------------------------------------------------


def cell_ids_for(cellkeys: List[int]) -> Dict[int, str]:
    """``cellkey -> canonical cell id``, derived through the app's own function.

    Deliberately pays for one ``cell_id_for_point`` call per distinct cell (a few
    seconds for ~300k cells) instead of formatting the id from the (col,row) we
    already have. The point is parity: the runtime will look these rows up by
    calling the same function on a cell centre, and the assertion below fails the
    build if the two paths ever disagree about a single cell.
    """
    cols = np.array([k // _KEY_BASE for k in cellkeys], dtype=np.float64)
    rows = np.array([k % _KEY_BASE for k in cellkeys], dtype=np.float64)
    cx = GRID_ORIGIN_X + (cols + 0.5) * CELL_RES_M
    cy = GRID_ORIGIN_Y + (rows + 0.5) * CELL_RES_M
    lon, lat = _FROM_ANALYSIS(cx, cy)

    out: Dict[int, str] = {}
    for key, lo, la in zip(cellkeys, lon, lat):
        cid = cell_id_for_point(float(lo), float(la), CELL_RES_M)
        res, col, row = parse_cell_id(cid)
        want_col, want_row = _unpack(key)
        if (res, col, row) != (CELL_RES_M, want_col, want_row):
            raise AssertionError(
                f"cell_id_for_point round-trip mismatch: expected "
                f"({CELL_RES_M}, {want_col}, {want_row}) got ({res}, {col}, {row}) "
                f"for {cid}. The offline projection and app.scoring.grid disagree; "
                f"every derived row would be attached to the wrong ground."
            )
        out[key] = cid
    return out


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE wofe_cell (
  cell_id TEXT PRIMARY KEY,
  geol_unit TEXT,
  geol_unit_frac REAL,
  fault_code INTEGER,
  fold_code INTEGER,
  dike_unit TEXT
);
CREATE TABLE vat (grid TEXT, value INTEGER, count INTEGER, label TEXT);
"""


def write_sqlite(
    out_path: Path,
    rows: Iterable[Tuple[str, Optional[str], Optional[float], Optional[int], Optional[int], Optional[str]]],
    vat_rows: Iterable[Tuple[str, int, int, Optional[str]]],
    meta: Dict[str, str],
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Build to a sibling temp file and rename. A crashed build must not leave a
    # half-populated database behind: local_store.py opens this file if it
    # exists, and a truncated footprint looks exactly like a real one.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
        conn.executescript(_SCHEMA)
        n = 0
        with conn:
            for chunk in _chunked(rows, 50_000):
                conn.executemany(
                    "INSERT INTO wofe_cell (cell_id, geol_unit, geol_unit_frac, "
                    "fault_code, fold_code, dike_unit) VALUES (?,?,?,?,?,?)",
                    chunk,
                )
                n += len(chunk)
            conn.executemany(
                "INSERT INTO vat (grid, value, count, label) VALUES (?,?,?,?)",
                list(vat_rows),
            )
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?,?)", sorted(meta.items())
            )
        conn.execute("VACUUM")
    finally:
        conn.close()
    os.replace(tmp, out_path)
    return n


def _chunked(it: Iterable, size: int) -> Iterable[List]:
    buf: List = []
    for item in it:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build(raw_dir: Path, out_path: Path, want: List[str], limit_rows: Optional[int]) -> int:
    t_start = time.time()
    agg: Dict[str, Dict[int, Tuple[int, int, int]]] = {}
    groups: Dict[str, List] = {}
    vat_rows: List[Tuple[str, int, int, Optional[str]]] = []
    counts: Dict[str, int] = {}

    for name in want:
        path = raw_dir / f"{GRIDS[name]}.e00"
        if not path.exists():
            logger.warning("%s missing — skipping %s (data/raw is gitignored)", path, name)
            continue
        t0 = time.time()
        grid = read_e00_grid(path, max_rows=limit_rows)
        logger.info(
            "%s: %dx%d @ %.0f m, %d VAT records, read in %.1fs",
            name,
            grid.ncols,
            grid.nrows,
            grid.cellsize_x,
            len(grid.vat),
            time.time() - t0,
        )
        vat_rows.extend(
            (name, r["value"], r["count"], r["label"] or None) for r in grid.vat
        )
        t0 = time.time()
        groups[name], agg[name] = aggregate(grid, name)
        counts[f"{name}_cells"] = len(agg[name])
        logger.info("  -> %d cells in %.1fs", len(agg[name]), time.time() - t0)
        # Free the raster before reading the next one; newageol alone is 40 MB
        # of int32 and newafold another 36 MB.
        grid.values = None

    all_keys = sorted(set().union(*(set(a) for a in agg.values())) if agg else set())
    if not all_keys:
        raise SystemExit("No cells produced — nothing to write.")
    logger.info("union: %d distinct %d m cells", len(all_keys), CELL_RES_M)

    t0 = time.time()
    ids = cell_ids_for(all_keys)
    logger.info("cell ids derived via cell_id_for_point in %.1fs", time.time() - t0)

    geol, fault, fold, dike = (agg.get(k, {}) for k in ("geol", "fault", "fold", "dike"))

    def modal(cells: Dict[int, Tuple[int, int, int]], name: str, key: int):
        """Modal group key for a cell, or None if this grid never saw the cell."""
        hit = cells.get(key)
        return groups[name][hit[0]] if hit else None

    def row_iter():
        for key in all_keys:
            g = geol.get(key)
            unit = groups["geol"][g[0]] if g else None
            yield (
                ids[key],
                unit,
                round(g[1] / g[2], 4) if g and g[2] else None,
                modal(fault, "fault", key),
                modal(fold, "fold", key),
                modal(dike, "dike", key),
            )

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_CITATION,
        "source_title": (
            "USGS OF-00-495 (Boleneus & Causey 2000), geologic raster data for "
            "weights-of-evidence analysis, NE Washington"
        ),
        "version": "1",
        "resolution_m": str(CELL_RES_M),
        "grid_tag": GRID_TAG,
        "grids_built": ",".join(sorted(agg)),
        "source_crs": f"EPSG:{SOURCE_EPSG}",
        "counts": json.dumps({"wofe_cell": len(all_keys), **counts}, sort_keys=True),
        "complete": "false" if limit_rows else "true",
        "note": (
            "Modal aggregation of OF-00-495 rasters onto the fixed EPSG:5070 "
            f"{CELL_RES_M} m grid. NAD27->WGS84 used a Helmert fallback "
            "(no NADCON grid installed): ~7-20 m positional accuracy, fine for "
            "cell attribution, not for positions."
        ),
    }
    if limit_rows:
        meta["truncated_rows"] = str(limit_rows)

    n = write_sqlite(out_path, row_iter(), vat_rows, meta)
    logger.info(
        "wrote %d rows to %s (%.1f MB) in %.1fs total",
        n,
        out_path,
        out_path.stat().st_size / 1e6,
        time.time() - t_start,
    )
    return n


# ---------------------------------------------------------------------------
# Reporting — the build is only useful if these numbers look right
# ---------------------------------------------------------------------------


def report(out_path: Path) -> None:
    conn = sqlite3.connect(out_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM wofe_cell").fetchone()[0]
        with_geol = conn.execute(
            "SELECT COUNT(*) FROM wofe_cell WHERE geol_unit IS NOT NULL"
        ).fetchone()[0]
        print(f"\nwofe_cell rows: {total}   with a lithology label: {with_geol}")
        for col in ("fault_code", "fold_code", "dike_unit"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM wofe_cell WHERE {col} IS NOT NULL"
            ).fetchone()[0]
            print(f"  {col:14s} populated in {n} cells")

        built = dict(conn.execute("SELECT key, value FROM meta")).get("grids_built", "")
        if "geol" not in built.split(","):
            print(
                f"\nNo lithology grid in this build (grids_built={built!r}), so the "
                "OF01-501 favourable-unit check is not applicable."
            )
            return

        cell_km2 = (CELL_RES_M / 1000) ** 2
        print(f"\nOF01-501 favourable units ({CELL_RES_M} m cells, {cell_km2:g} km2 each):")
        found = 0
        for unit, contrast in FAVOURABLE_UNITS.items():
            n = conn.execute(
                "SELECT COUNT(*) FROM wofe_cell WHERE geol_unit = ?", (unit,)
            ).fetchone()[0]
            vat = conn.execute(
                "SELECT count FROM vat WHERE grid='geol' AND label=?", (unit,)
            ).fetchone()
            src_px = vat[0] if vat else 0
            found += 1 if n else 0
            print(
                f"  {unit:6s} contrast {contrast:4.2f}  modal in {n:6d} cells "
                f"({n * cell_km2:8.1f} km2)   source pixels {src_px:7d}"
            )
        if not found:
            print(
                "\n!! CRITICAL: none of the six OF01-501 favourable units are modal in "
                "any cell.\n   This layer exists only because it carries the published "
                "contrast codes.\n   Investigate before wiring it into scoring."
            )

        print("\nTop 12 units by cell count:")
        for unit, n in conn.execute(
            "SELECT geol_unit, COUNT(*) c FROM wofe_cell WHERE geol_unit IS NOT NULL "
            "GROUP BY geol_unit ORDER BY c DESC LIMIT 12"
        ):
            print(f"  {unit:8s} {n:7d}")

        # Hand check: an Evsf cell must land in NE Washington (Ferry/Okanogan).
        row = conn.execute(
            "SELECT cell_id, geol_unit_frac FROM wofe_cell WHERE geol_unit='Evsf' "
            "ORDER BY geol_unit_frac DESC, cell_id LIMIT 1"
        ).fetchone()
        if row:
            cid, frac = row
            w, s, e, n_ = cell_id_to_bbox(cid)
            print(
                f"\nSanity check — highest-fraction Evsf cell {cid} (frac {frac}):\n"
                f"  bbox  lon {w:.5f}..{e:.5f}  lat {s:.5f}..{n_:.5f}\n"
                f"  centre lon {(w + e) / 2:.5f} lat {(s + n_) / 2:.5f}"
            )
            in_ne_wa = -119.6 <= (w + e) / 2 <= -117.5 and 47.9 <= (s + n_) / 2 <= 49.1
            print(
                "  -> inside the NE Washington (Ferry/Okanogan) envelope"
                if in_ne_wa
                else "  -> !! OUTSIDE NE Washington — projection is wrong"
            )
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw" / "of00-495")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "derived" / "of00495.sqlite")
    ap.add_argument(
        "--grids",
        default=",".join(GRIDS),
        help=f"comma-separated subset of {list(GRIDS)} (default: all)",
    )
    ap.add_argument(
        "--limit-rows",
        type=int,
        default=None,
        metavar="N",
        help="read only the first N (northernmost) raster rows of each grid — "
        "smoke test only; the result is marked complete=false in meta",
    )
    ap.add_argument("--report-only", action="store_true", help="just re-print the summary")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.report_only:
        if not args.out.exists():
            raise SystemExit(f"{args.out} does not exist")
        report(args.out)
        return 0

    want = [g.strip() for g in args.grids.split(",") if g.strip()]
    unknown = [g for g in want if g not in GRIDS]
    if unknown:
        raise SystemExit(f"Unknown grid(s) {unknown}; choose from {list(GRIDS)}")
    if CELL_RES_M not in RESOLUTION_LADDER:  # pragma: no cover - guards a bad edit
        raise SystemExit(f"{CELL_RES_M} m is not on RESOLUTION_LADDER {RESOLUTION_LADDER}")
    if args.limit_rows:
        logger.warning(
            "--limit-rows %d: SMOKE TEST ONLY. Only the northern strip of each "
            "grid is read, and the strip is a different area per grid because the "
            "cell sizes differ (50/100/200 m).",
            args.limit_rows,
        )

    build(args.raw_dir, args.out, want, args.limit_rows)
    report(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
