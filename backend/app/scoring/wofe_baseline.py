"""
Deterministic weights-of-evidence baseline — the thing the LLM composite is
measured against.

Every score this application currently produces is a language model's opinion
plus prose. That is unfalsifiable on its own: a composite of 0.82 cannot be
called right or wrong, only plausible. This module scores the *same* fixed-grid
cells with no model in the loop at all, by reimplementing a published,
peer-reviewed statistical model — USGS OF01-501 (Boleneus et al., 2001),
weights-of-evidence for epithermal gold in northeastern Washington — over the
raster data that study was built on (USGS OF-00-495, aggregated by
``scripts/build_of00495.py`` into ``data/derived/of00495.sqlite``).

Two numbers then become available that were not before: whether the LLM ranks
cells the way a fitted statistical model does, and whether either of them ranks
recorded workings above the ground around them. ``scripts/benchmark.py`` reports
both.

Scope, and why it refuses rather than extrapolates
--------------------------------------------------
The published contrasts were fitted on **50 epithermal gold training sites** in
a **222 × 277 km area of northeastern Washington** — the Colville, Chewelah,
Republic, Nespelem, Omak and Oroville 1:100,000 quadrangles. Inside that area
the model is a measurement. Outside it, it is nothing:

* The **North Cascades** districts (Monte Cristo, Blewett, Sultan Basin, the
  Snoqualmie batholith margin) are **orogenic** gold in metamorphic rocks. The
  six favourable units in this model are all Eocene volcanic or volcaniclastic
  and do not exist there. A high score would be meaningless; a low score would
  be worse, because it would read as evidence of absence.
* **West of the Cascade crest** the study has nothing to say at all.

So ``score_cells_wofe`` returns ``score=None`` and a ``refused`` reason for
every cell outside the OF-00-495 footprint, and for any target mineral other
than gold. An out-of-scope confident number is worse than no number: downstream
it is indistinguishable from a real one.

How the published model is reimplemented
----------------------------------------
Three predictors, all with published optimum parameters:

1. **Lithology**, six units with measured contrasts (``WOFE_CONTRASTS`` in
   ``app.spatial.wofe_grid`` — single-sourced, do not re-type the table), plus
   the published **150 m** lithologic buffer.
2. **Structure**: normal faults trending 345°–030°, published optimum buffer
   **1700 m**.
3. **Tract classification** into favourable / permissive / non-permissive, whose
   published score guidance (0.70–0.95, 0.35–0.65, 0.00–0.30) is what this
   module emits.

**Scoring is always done at 250 m, whatever resolution the caller asks about.**
That is not a detail. The lithologic and structural predictors are *presence*
tests, and presence saturates as cells grow: measured on a Republic AOI, scoring
1000 m cells directly put 97 of 105 of them in the favourable tract, because a
1 km cell almost always contains *some* favourable pixel and *some* fault. The
published model was run at 50–100 m. So a coarse analysis cell here is
decomposed into its 250 m quadtree descendants, each scored natively, and rolled
up as an equal-area mean — which preserves the discrimination the presence tests
have at their own scale. ``score_max`` and ``tract_fracs`` expose the spread
inside a coarse cell so a "0.42 average" is never mistaken for "uniformly
mediocre ground".

Departures from the publication, each forced by the data and each visible in the
output:

* **The posterior probabilities are not reproducible from this raster.** The
  build of ``of00495.sqlite`` measured the areas of the six favourable units off
  ``newageol`` and they disagree with the published area column by up to 5×
  (Evsf 659.6 km² measured vs 302.5 published; Evst 17.1 vs 50.6). The
  publication's tract percentages therefore describe a different clip or a
  different geology version. The *contrasts* remain usable as a per-unit lookup;
  the posteriors do not. ``posterior`` is reported for transparency and is not
  what ``score`` is derived from — the tract score bands are.
* **The tract rule is rebuilt from the predictors, not from a posterior
  threshold**, for the reason above:

      favourable      favourable unit within the 150 m buffer AND a mapped
                      fault within 1700 m
      permissive      favourable unit within the buffer, no fault in range
      non_permissive  neither

  Measured over the whole 395,605-cell footprint that gives **3.80% / 2.05% /
  94.14%** against the published **4.6% / 3.4% / 92.0%**. Three independent
  predictors landing that close is the strongest available evidence that this
  reimplementation is the model the paper describes rather than a lookalike.
* **The 150 m buffer becomes one 250 m cell of dilation.** The derived table is
  aggregated at 250 m (a rung of ``RESOLUTION_LADDER``), so sub-cell position is
  gone: the finest available buffer is "the neighbouring cell", 250 m rook and
  354 m diagonal. That is more generous than the published 150 m. It was chosen
  on the only external evidence available — the published tract shares. Measured
  favourable share: no dilation 2.56%, rook-only 3.49%, rook+diagonal 3.80%,
  against a published 4.6%. The most generous option is the closest, so the
  generosity is at least empirically defensible rather than convenient.
* **Fault azimuth is usually unknown.** OF-00-495's fault raster carries a type
  code and no orientation, so the published 345°–030° filter cannot be applied
  to it — the fault term uses fault *presence* within 1700 m and is therefore
  more permissive than published. Where ``wa_geology.sqlite`` (WA DNR 1:24k)
  maps the same ground, the real trend test is applied and reported as
  ``favourable_trend``; where it does not, ``favourable_trend`` is ``None`` and
  the trend component is dropped from the score rather than guessed. Note that
  the 1:24k release has no coverage at all over the Republic / Curlew / Toroda
  Creek corridor, so ``None`` is the common case exactly where the model matters
  most.
* **One assumed constant.** ``FAULT_CONTRAST_ASSUMED`` — the material available
  to this repo gives the fault predictor's optimum buffer but not its contrast.
  Everything else in this module is published. It is a named module constant so
  that whoever reads OF01-501's structural table can correct it in one place.
* **The Kettle mine case is under-called.** The single training site in the
  published permissive tract sits under Quaternary cover; this implementation
  only reaches cover one 250 m cell out from mapped favourable rock, so deeply
  buried favourable units read as non-permissive. Widening that would need a
  cover-thickness allowance the publication does not give.

Reading the output
------------------
``score_cells_wofe(cells)`` returns one dict per input cell, always — a cell
outside the footprint is present with ``score=None`` and a reason, because "this
model does not apply here" is a result the caller must be able to see. ``score``
is never 0.0 as a stand-in for "no answer"; 0.0 is a claim about the ground.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.spatial.wofe_grid import (
    OF00495_CITATION,
    OF01501_CITATION,
    WOFE_CELL_RESOLUTION_M,
    WOFE_CONTRASTS,
    WOFE_DB,
    contrast_for,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Published parameters. Everything here except FAULT_CONTRAST_ASSUMED comes out
# of OF01-501 via knowledge/lithology/gold.md; see the module docstring.
# ---------------------------------------------------------------------------

#: Optimum fault buffer, metres. Normal faults trending 345°-030°.
FAULT_BUFFER_M = 1700.0

#: Optimum lithologic buffer, metres. Mineralisation extends about this far
#: beyond a mapped contact, so a cell this close to a favourable unit behaves as
#: if it contained it.
LITHO_BUFFER_M = 150.0

#: Optimum placer-association buffer, metres. Not a predictor here — it is the
#: range over which sub-threshold fault proximity breaks ties inside a tract
#: band, chosen because it is a published distance rather than an invented one.
PLACER_BUFFER_M = 4000.0

#: Training sites the model was fitted on.
N_TRAINING_SITES = 50

#: Published score guidance per tract, from the OF01-501 tract table. These are
#: the bands this module emits — not a rescaled posterior, which is not
#: reproducible from this raster (see the module docstring).
TRACT_SCORE_BANDS: Dict[str, Tuple[float, float]] = {
    "favourable": (0.70, 0.95),
    "permissive": (0.35, 0.65),
    "non_permissive": (0.00, 0.30),
}

#: Tract names in descending favourability. Used for the modal/rollup ordering
#: so ties break towards the more favourable reading, which is the safe
#: direction for a prospecting tool: over-flagging costs a field day,
#: under-flagging loses the deposit.
TRACT_ORDER = ("favourable", "permissive", "non_permissive")

#: **The one number in this module that is not published.** OF01-501 gives the
#: structural predictor's optimum buffer (1700 m) but the material available
#: here does not give its contrast, and a weights-of-evidence sum needs one. 1.0
#: is deliberately conservative: on the study's own interpretation scale it is
#: only "moderately predictive", i.e. well below every one of the six
#: lithologic contrasts. Correct it here — nothing else needs to change — if
#: someone reads the structural table in the report itself.
FAULT_CONTRAST_ASSUMED = 1.0

#: Largest published lithologic contrast (Eck). The evidence sum is normalised
#: by max contrast + fault contrast so the within-band position is in [0, 1].
MAX_CONTRAST = max(v["contrast"] for v in WOFE_CONTRASTS.values())
LOGIT_SPAN = MAX_CONTRAST + FAULT_CONTRAST_ASSUMED

#: Relative influence on where a cell lands inside its tract band. A component
#: whose input is unavailable is dropped and the rest renormalised, so a cell
#: with no azimuth data is not penalised for a gap in a *different* dataset —
#: see ``_band_position``.
W_EVIDENCE = 0.75  # the WofE contrast sum itself
W_CLOSENESS = 0.15  # continuous fault proximity, out to PLACER_BUFFER_M
W_TREND = 0.10  # 24k azimuth in the published 345°-030° band

#: A cell is only scored when at least this share of it is inside the OF-00-495
#: footprint. Coarse cells straddling the study-area edge would otherwise be
#: scored off a sliver of data.
MIN_COVERAGE_FRAC = 0.5

#: The model is epithermal gold. Nothing else.
SUPPORTED_MINERALS = frozenset({"gold"})

#: Ring radius, in 250 m cells, that the neighbourhood read must cover.
_RING_CELLS = int(math.ceil(PLACER_BUFFER_M / WOFE_CELL_RESOLUTION_M))

#: Above this column span a windowed read is pointless and reading the whole
#: table is cheaper (395k rows, well under a second).
_WHOLE_TABLE_COL_SPAN = 400


class WofERefusal:
    """Reasons a cell gets no score. Strings are stable — tests assert on them."""

    NO_DB = "of00495.sqlite not built — run scripts/build_of00495.py"
    OUT_OF_FOOTPRINT = (
        "outside the OF-00-495 study area (NE Washington); the published "
        "weights-of-evidence model does not apply here"
    )
    PARTIAL_COVERAGE = (
        "less than half the cell is inside the OF-00-495 study area; scoring it "
        "would extrapolate from a sliver"
    )
    BAD_CELL_ID = "not a parseable fixed-grid cell id"

    @staticmethod
    def unsupported_mineral(mineral: str) -> str:
        return (
            f"target mineral {mineral!r} is out of scope — OF01-501 was fitted on "
            f"50 epithermal *gold* training sites and says nothing about anything "
            f"else"
        )


@dataclass(frozen=True)
class _Row:
    """One 250 m row of ``wofe_cell``, keyed by grid index rather than cell id."""

    geol_unit: Optional[str]
    geol_unit_frac: float
    fault_code: Optional[int]
    fold_code: Optional[int]
    dike_unit: Optional[str]


# ---------------------------------------------------------------------------
# Cell identity plumbing
# ---------------------------------------------------------------------------


def _cell_id_of(cell: Any) -> Optional[str]:
    """Accept a GridCell, a ``model_dump()`` dict, a ScoredCell, or a bare id.

    Callers come from three directions — the orchestrator (GridCell), a run
    record (thinned dicts) and the benchmark (bare ids) — and none of them
    should have to convert first.
    """
    if isinstance(cell, str):
        return cell
    if isinstance(cell, dict):
        cid = cell.get("cell_id")
        return str(cid) if cid else None
    cid = getattr(cell, "cell_id", None)
    return str(cid) if cid else None


def _children_extent(res: int, col: int, row: int) -> Tuple[int, int, int, int, int]:
    """250 m index rectangle covering a cell, plus the expected child count.

    Returns ``(col_lo, col_hi, row_lo, row_hi, expected)``, inclusive. A cell
    finer than 250 m inherits from its single 250 m parent — flagged by the
    caller, because an inherited value was not measured at that scale.
    """
    base = WOFE_CELL_RESOLUTION_M
    if res == base:
        return col, col, row, row, 1
    if res < base:
        factor = base // res
        return col // factor, col // factor, row // factor, row // factor, 1
    k = res // base
    return col * k, col * k + k - 1, row * k, row * k + k - 1, k * k


# ---------------------------------------------------------------------------
# The scorer
# ---------------------------------------------------------------------------


class WofEBaseline:
    """Deterministic OF01-501 scorer over ``of00495.sqlite``.

    Read-only, and stateless between calls apart from a lazily opened SQLite
    connection. A missing database is not an error — every cell comes back
    refused, which is the honest answer on a fresh clone where ``data/derived/``
    does not exist.
    """

    def __init__(
        self,
        wofe_db: Optional[Path] = None,
        geology_db: Optional[Path] = None,
        use_geology_azimuth: bool = True,
    ):
        self.path = Path(wofe_db) if wofe_db else WOFE_DB
        self.geology_path = Path(geology_db) if geology_db else None
        self.use_geology_azimuth = use_geology_azimuth
        self._conn: Optional[sqlite3.Connection] = None
        self._prior_logit: Optional[float] = None
        self._warned = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._connect() is not None

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        if not self.path.exists():
            if not self._warned:
                logger.info(
                    "No OF-00-495 store at %s — the deterministic WofE baseline "
                    "cannot be computed for any cell",
                    self.path,
                )
                self._warned = True
            return None
        try:
            conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            self._conn = conn
            return conn
        except Exception as exc:
            logger.warning("Could not open %s: %s", self.path, exc)
            return None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # -- prior -------------------------------------------------------------

    def prior_logit(self) -> float:
        """Log-odds of the unconditional site density over the footprint.

        Only affects the reported ``posterior``. It cancels out of ``score``,
        which normalises the contrast sum against its own span — worth knowing,
        because it means the score does not depend on how much of the study area
        the raster happens to cover.
        """
        if self._prior_logit is not None:
            return self._prior_logit
        n_cells = self._footprint_cell_count()
        p = N_TRAINING_SITES / float(max(n_cells, N_TRAINING_SITES + 1))
        self._prior_logit = math.log(p / (1.0 - p))
        return self._prior_logit

    def _footprint_cell_count(self) -> int:
        conn = self._connect()
        if conn is None:
            return 1
        try:
            import json

            row = conn.execute("SELECT value FROM meta WHERE key = 'counts'").fetchone()
            if row:
                n = int(json.loads(row["value"]).get("wofe_cell") or 0)
                if n > 0:
                    return n
        except Exception:
            pass
        try:
            return int(conn.execute("SELECT COUNT(*) FROM wofe_cell").fetchone()[0])
        except Exception:
            return 1

    # -- windowed read -----------------------------------------------------

    def _load_window(
        self, col_lo: int, col_hi: int, row_lo: int, row_hi: int
    ) -> Dict[Tuple[int, int], _Row]:
        """Every 250 m row in an index rectangle, keyed by ``(col, row)``.

        Range-scans the primary key rather than doing per-cell lookups: cell ids
        are zero-padded, so lexicographic order on ``cell_id`` *is* ``(col,
        row)`` order and a ``BETWEEN`` on the column bounds is an index scan.
        Rows outside the row bounds are dropped in Python — cheap, and it avoids
        needing a second index this table does not have.
        """
        conn = self._connect()
        if conn is None:
            return {}

        from app.scoring.grid import make_cell_id, parse_cell_id

        out: Dict[Tuple[int, int], _Row] = {}
        cols = "cell_id, geol_unit, geol_unit_frac, fault_code, fold_code, dike_unit"
        if col_hi - col_lo + 1 > _WHOLE_TABLE_COL_SPAN:
            # An AOI this wide is most of NE Washington; scanning everything is
            # both simpler and faster than a range that covers it anyway.
            sql, params = f"SELECT {cols} FROM wofe_cell", ()
        else:
            sql = f"SELECT {cols} FROM wofe_cell WHERE cell_id BETWEEN ? AND ?"
            params = (
                make_cell_id(col_lo, 0, WOFE_CELL_RESOLUTION_M),
                make_cell_id(col_hi, 999999, WOFE_CELL_RESOLUTION_M),
            )
        try:
            rows = conn.execute(sql, params)
        except Exception as exc:
            logger.warning("OF-00-495 window read failed: %s", exc)
            return {}

        for r in rows:
            try:
                _, col, row = parse_cell_id(r["cell_id"])
            except Exception:
                continue
            if row < row_lo or row > row_hi or col < col_lo or col > col_hi:
                continue
            frac = r["geol_unit_frac"]
            out[(col, row)] = _Row(
                geol_unit=r["geol_unit"],
                geol_unit_frac=float(frac) if frac is not None else 1.0,
                fault_code=r["fault_code"],
                fold_code=r["fold_code"],
                dike_unit=r["dike_unit"],
            )
        return out

    # -- scoring -----------------------------------------------------------

    def score_cells(
        self, grid_cells: Iterable[Any], target_mineral: str = "gold"
    ) -> Dict[str, Dict[str, Any]]:
        """Score every cell. One entry per input cell, scored or refused."""
        ids: List[str] = []
        seen: Set[str] = set()
        for c in grid_cells:
            cid = _cell_id_of(c)
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        if not ids:
            return {}

        mineral = (target_mineral or "").strip().lower()
        if mineral not in SUPPORTED_MINERALS:
            reason = WofERefusal.unsupported_mineral(mineral or str(target_mineral))
            return {cid: _refused(cid, reason) for cid in ids}

        if not self.available:
            return {cid: _refused(cid, WofERefusal.NO_DB) for cid in ids}

        from app.scoring.grid import parse_cell_id

        # Decompose every analysis cell to its 250 m extent, and find the window.
        extents: Dict[str, Tuple[int, int, int, int, int, int]] = {}
        out: Dict[str, Dict[str, Any]] = {}
        col_lo = row_lo = 10**9
        col_hi = row_hi = -(10**9)
        for cid in ids:
            try:
                res, col, row = parse_cell_id(cid)
            except Exception:
                out[cid] = _refused(cid, WofERefusal.BAD_CELL_ID)
                continue
            c0, c1, r0, r1, expected = _children_extent(res, col, row)
            extents[cid] = (c0, c1, r0, r1, expected, res)
            col_lo, col_hi = min(col_lo, c0), max(col_hi, c1)
            row_lo, row_hi = min(row_lo, r0), max(row_hi, r1)

        if not extents:
            return out

        window = self._load_window(
            col_lo - _RING_CELLS,
            col_hi + _RING_CELLS,
            row_lo - _RING_CELLS,
            row_hi + _RING_CELLS,
        )
        if not window:
            for cid in extents:
                out[cid] = _refused(cid, WofERefusal.OUT_OF_FOOTPRINT)
            return out

        # Nearest-fault is a linear scan of the window's fault cells per queried
        # cell, so the whole call is O(cells × faults-in-window) — quadratic in
        # AOI area. Measured: 10,000 cells at 250 m over the Republic graben,
        # 1.1 s. That is fine for the analysis grid (capped at MAX_LLM_CELLS=150)
        # and for the benchmark, but scoring the entire 395,605-cell footprint in
        # one call would not finish. Do it in windows if you ever need to.
        fault_cells = [k for k, v in window.items() if v.fault_code is not None]

        # The 1:24k trend test is resolved once per *analysis* cell, not per
        # 250 m child: the fault buffer (1700 m) is wider than most analysis
        # cells, so the answer barely varies inside one, and the geology query
        # is by far the most expensive thing here.
        trend_for = self._trend_lookup(list(extents.keys()))

        scored_children: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for cid, (c0, c1, r0, r1, expected, res) in extents.items():
            trend = trend_for.get(cid)
            children: List[Dict[str, Any]] = []
            for c in range(c0, c1 + 1):
                for r in range(r0, r1 + 1):
                    if (c, r) not in window:
                        continue
                    key = (c, r)
                    # Two analysis cells can share a child only when one is
                    # finer than 250 m; cache anyway, it is free.
                    got = scored_children.get(key)
                    if got is None or got["favourable_trend"] != trend:
                        got = self._score_child(c, r, window, fault_cells, trend)
                        scored_children[key] = got
                    children.append(got)
            out[cid] = _rollup(cid, res, children, expected)
        return out

    def _score_child(
        self,
        col: int,
        row: int,
        window: Dict[Tuple[int, int], _Row],
        fault_cells: Sequence[Tuple[int, int]],
        trend: Optional[bool],
    ) -> Dict[str, Any]:
        """Score one native 250 m cell. This is where the published model lives."""
        me = window[(col, row)]

        # --- lithology, with the published 150 m buffer -------------------
        in_cell_unit = me.geol_unit
        in_cell_fav = in_cell_unit if in_cell_unit in WOFE_CONTRASTS else None
        favourable_frac = me.geol_unit_frac if in_cell_fav else 0.0

        buffer_units = _buffered_favourable(col, row, window)
        candidates = set(buffer_units) | ({in_cell_fav} if in_cell_fav else set())
        wofe_unit = (
            max(candidates, key=lambda u: WOFE_CONTRASTS[u]["contrast"])
            if candidates
            else None
        )
        w_lith = float(contrast_for(wofe_unit) or 0.0)
        # "Buffer only" means there is no favourable rock *in* the cell at all —
        # not merely that a neighbour holds a higher-contrast unit. Conflating
        # the two would report a cell that is 80% Sanpoil flows as if it were
        # barren ground next door to something good.
        buffer_only = in_cell_fav is None and wofe_unit is not None

        # --- structure ---------------------------------------------------
        d_fault = _nearest_fault_m(col, row, fault_cells)
        fault_in_buffer = d_fault is not None and d_fault <= FAULT_BUFFER_M
        w_fault = FAULT_CONTRAST_ASSUMED if fault_in_buffer else 0.0

        # --- tract, posterior, score -------------------------------------
        if w_lith > 0 and fault_in_buffer:
            tract = "favourable"
        elif w_lith > 0:
            tract = "permissive"
        else:
            tract = "non_permissive"

        logit = self.prior_logit() + w_lith + w_fault
        closeness = (
            max(0.0, 1.0 - d_fault / PLACER_BUFFER_M) if d_fault is not None else 0.0
        )
        t = _band_position(
            evidence=(w_lith + w_fault) / LOGIT_SPAN,
            closeness=closeness,
            trend=trend,
        )
        lo, hi = TRACT_SCORE_BANDS[tract]

        return {
            "col": col,
            "row": row,
            "score": round(lo + t * (hi - lo), 4),
            "tract": tract,
            "logit": logit,
            "posterior": 1.0 / (1.0 + math.exp(-logit)),
            "unit": in_cell_unit,
            "unit_frac": me.geol_unit_frac,
            "wofe_unit": wofe_unit,
            "wofe_unit_in_cell": in_cell_fav,
            "contrast": w_lith,
            "favourable_frac": favourable_frac,
            "litho_buffer_only": buffer_only,
            "fault_distance_m": d_fault,
            "fault_within_buffer": fault_in_buffer,
            "fault_code": me.fault_code,
            "fold_code": me.fold_code,
            "dike_unit": me.dike_unit,
            "favourable_trend": trend,
        }

    # -- optional 24k azimuth ---------------------------------------------

    def _trend_lookup(self, cell_ids: Sequence[str]) -> Dict[str, Optional[bool]]:
        """Per-cell OF01-501 trend test from the 1:24k geology, where it exists.

        ``True``/``False`` only where the 24k dataset actually maps the cell;
        absent (⇒ ``None``) where it does not. That distinction is load-bearing:
        the 1:24k release has no coverage at all over the Republic / Curlew /
        Toroda Creek corridor, so treating "no faults found" as "no favourable
        faults" would silently penalise the best ground in the study area.

        Everything here is optional and defensive — this module's core answer
        must not depend on a second derived database being present, and a
        failure inside it must degrade to ``None`` rather than propagate.
        """
        if not self.use_geology_azimuth or not cell_ids:
            return {}
        try:
            import shapely

            from app.scoring.grid import cell_polygon_wgs84, parse_cell_id
            from app.spatial.geology import (
                GeologyStore,
                WOFE_FAULT_BUFFER_KM,
                get_store,
                structures_for_cell,
            )
            from app.spatial.geometry import LocalMetric
        except Exception as exc:  # pragma: no cover - dependency-shaped failure
            logger.info("24k azimuth test unavailable (%s)", exc)
            return {}

        store = GeologyStore(self.geology_path) if self.geology_path else get_store()
        if not store.available:
            return {}

        polys: Dict[str, Any] = {}
        for cid in cell_ids:
            try:
                res, col, row = parse_cell_id(cid)
            except Exception:
                continue
            polys[cid] = cell_polygon_wgs84(col, row, res)
        if not polys:
            return {}

        bounds = [g.bounds for g in polys.values()]
        pad = WOFE_FAULT_BUFFER_KM / 100.0  # ~1.7 km in degrees, deliberately loose
        bbox = (
            min(b[0] for b in bounds) - pad,
            min(b[1] for b in bounds) - pad,
            max(b[2] for b in bounds) + pad,
            max(b[3] for b in bounds) + pad,
        )
        try:
            metric = LocalMetric.for_bbox(bbox)
            window = store.window(bbox, metric)
        except Exception as exc:
            logger.info("24k geology window failed: %s", exc)
            return {}
        if not window.has_units or window.unit_geoms is None:
            return {}

        out: Dict[str, Optional[bool]] = {}
        for cid, g in polys.items():
            try:
                proj = metric.project(g)
                # Is this cell mapped at all? An unmapped cell must report
                # "unknown", never "no favourable faults".
                if float(shapely.distance(proj, window.unit_geoms).min()) > 0.0:
                    continue
                if not window.has_structures:
                    out[cid] = False
                    continue
                s = structures_for_cell(
                    window, proj, max_named=3, buffer_km=WOFE_FAULT_BUFFER_KM
                )
                out[cid] = bool(s.get("favourable_trend"))
            except Exception:
                continue
        return out


# ---------------------------------------------------------------------------
# Aggregation and helpers
# ---------------------------------------------------------------------------


def _rollup(
    cell_id: str, res: int, children: List[Dict[str, Any]], expected: int
) -> Dict[str, Any]:
    """Roll native 250 m child scores up to one analysis cell.

    Equal-area mean, because every child is the same size. ``score_max`` and
    ``tract_fracs`` come with it: a 1 km cell averaging 0.42 might be uniformly
    middling ground or one favourable 250 m square in a barren square kilometre,
    and those are completely different prospects.

    Note that at coarse resolution ``tract`` (the modal child tract) and
    ``score`` (the mean child score) can disagree — a cell split half favourable
    and half non-permissive is reported as favourable but scores in the
    permissive band. ``tract_fracs`` is the authoritative reading; ``tract`` is
    a label for the UI and must not be used to bucket a coarse cell.
    """
    coverage = len(children) / float(expected) if expected else 0.0
    if not children:
        return _refused(cell_id, WofERefusal.OUT_OF_FOOTPRINT, coverage=0.0)
    if coverage < MIN_COVERAGE_FRAC:
        return _refused(
            cell_id, WofERefusal.PARTIAL_COVERAGE, coverage=round(coverage, 3)
        )

    n = len(children)
    scores = [c["score"] for c in children]
    tract_counts: Dict[str, int] = {}
    for c in children:
        tract_counts[c["tract"]] = tract_counts.get(c["tract"], 0) + 1
    # Ties break towards the more favourable tract: over-flagging costs a field
    # day, under-flagging loses the deposit.
    tract = min(
        tract_counts,
        key=lambda t: (-tract_counts[t], TRACT_ORDER.index(t)),
    )

    unit_area: Dict[str, float] = {}
    for c in children:
        if c["unit"]:
            unit_area[c["unit"]] = unit_area.get(c["unit"], 0.0) + (
                c["unit_frac"] or 1.0
            )
    modal_unit = modal_frac = None
    if unit_area:
        modal_unit, share = max(unit_area.items(), key=lambda kv: kv[1])
        modal_frac = round(share / n, 3)

    with_fav = [c for c in children if c["wofe_unit"]]
    best = (
        max(with_fav, key=lambda c: WOFE_CONTRASTS[c["wofe_unit"]]["contrast"])
        if with_fav
        else max(children, key=lambda c: c["score"])
    )
    dists = [c["fault_distance_m"] for c in children if c["fault_distance_m"] is not None]
    trends = [c["favourable_trend"] for c in children if c["favourable_trend"] is not None]

    return {
        "cell_id": cell_id,
        "in_footprint": True,
        "resolution_m": res,
        "wofe_cells": n,
        "coverage_frac": round(coverage, 3),
        "inherited_from_250m": True if res < WOFE_CELL_RESOLUTION_M else None,
        "score": round(sum(scores) / n, 4),
        "score_max": round(max(scores), 4),
        "score_min": round(min(scores), 4),
        "refused": None,
        "tract": tract,
        "tract_fracs": {t: round(v / n, 3) for t, v in sorted(tract_counts.items())},
        "posterior": round(max(c["posterior"] for c in children), 6),
        "logit": round(max(c["logit"] for c in children), 4),
        "unit": modal_unit,
        "unit_frac": modal_frac,
        "wofe_unit": best["wofe_unit"],
        "wofe_unit_in_cell": best["wofe_unit_in_cell"],
        "contrast": round(best["contrast"], 3),
        "favourable_frac": round(
            sum(c["favourable_frac"] for c in children) / n, 3
        ),
        "litho_buffer_only": all(
            c["litho_buffer_only"] for c in with_fav
        )
        if with_fav
        else False,
        "fault_distance_m": round(min(dists), 1) if dists else None,
        "fault_within_buffer": any(c["fault_within_buffer"] for c in children),
        "fault_codes": sorted(
            {int(c["fault_code"]) for c in children if c["fault_code"] is not None}
        )[:6],
        "favourable_trend": (any(trends) if trends else None),
        "evidence": _evidence(best, tract, n, coverage, res),
        "data_sources_used": (
            [OF00495_CITATION, OF01501_CITATION]
            + (["WA_DNR_WGS_Surface_Geology_24k"] if trends else [])
        ),
    }


def _refused(
    cell_id: str, reason: str, coverage: Optional[float] = None
) -> Dict[str, Any]:
    """A cell the model declines to score. ``score`` is None, never 0.0.

    0.0 would be a claim ("this ground is barren"); None is the truth ("this
    model has nothing to say about this ground").
    """
    return {
        "cell_id": cell_id,
        "in_footprint": False,
        "coverage_frac": coverage,
        "score": None,
        "score_max": None,
        "refused": reason,
        "tract": None,
        "evidence": [f"No WofE baseline: {reason}"],
        "data_sources_used": [],
    }


def _band_position(evidence: float, closeness: float, trend: Optional[bool]) -> float:
    """Where inside its tract band a cell lands, in [0, 1].

    Weighted mean of the components that have an input. ``trend`` is ``None``
    when the 1:24k geology does not map the cell; dropping the component and
    renormalising is the only treatment that neither rewards nor punishes a cell
    for a gap in a dataset that has nothing to do with it.
    """
    parts: List[Tuple[float, float]] = [
        (W_EVIDENCE, min(max(evidence, 0.0), 1.0)),
        (W_CLOSENESS, min(max(closeness, 0.0), 1.0)),
    ]
    if trend is not None:
        parts.append((W_TREND, 1.0 if trend else 0.0))
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum if wsum else 0.0


def _buffered_favourable(
    col: int, row: int, window: Dict[Tuple[int, int], _Row]
) -> Set[str]:
    """Favourable units in the eight cells around a 250 m cell.

    This is the published 150 m lithologic buffer as finely as 250 m aggregation
    allows — see the module docstring. Rook and diagonal neighbours are treated
    alike because sub-cell position is not recoverable and pretending otherwise
    would be false precision.
    """
    found: Set[str] = set()
    for c in (col - 1, col, col + 1):
        for r in (row - 1, row, row + 1):
            if c == col and r == row:
                continue
            got = window.get((c, r))
            if got and got.geol_unit in WOFE_CONTRASTS:
                found.add(got.geol_unit)
    return found


def _nearest_fault_m(
    col: int, row: int, fault_cells: Sequence[Tuple[int, int]]
) -> Optional[float]:
    """Distance from a 250 m cell to the nearest cell carrying a fault code.

    Measured in whole 250 m steps between cell footprints, so it is quantised to
    ±125 m — irrelevant against a 1700 m buffer, and the honest resolution of
    the underlying aggregation.

    Returns ``None`` past ``PLACER_BUFFER_M``. The cap matters: the window read
    is expanded around the *union* of the queried cells, so for a scattered
    query it can contain faults tens of kilometres away, and reporting one of
    those as "nearest" would be true but useless — beyond the placer buffer the
    number carries no weight in the score and should not appear in the evidence
    as though it did.
    """
    best: Optional[float] = None
    for c, r in fault_cells:
        d = math.hypot(c - col, r - row) * WOFE_CELL_RESOLUTION_M
        if best is None or d < best:
            best = d
            if best == 0.0:
                break
    if best is None or best > PLACER_BUFFER_M:
        return None
    return best


def _evidence(
    best: Dict[str, Any], tract: str, n_children: int, coverage: float, res: int
) -> List[str]:
    """Human-readable trace of exactly which published terms fired."""
    out: List[str] = []
    wofe_unit = best["wofe_unit"]
    in_cell = best["wofe_unit_in_cell"]
    # At coarse resolution every "the cell" below means the best-evidenced 250 m
    # child, not the analysis cell — say so once rather than hedging every line.
    scale = "cell" if res == WOFE_CELL_RESOLUTION_M else "best 250 m sub-cell"
    if wofe_unit:
        meta = WOFE_CONTRASTS[wofe_unit]
        if best["litho_buffer_only"]:
            where = (
                f"within the {LITHO_BUFFER_M:.0f} m lithologic buffer (adjacent "
                f"250 m cell); no favourable unit in the {scale} itself"
            )
        elif wofe_unit == in_cell:
            where = f"mapped in the {scale} ({best['unit_frac']:.0%} of it)"
        else:
            # A neighbouring higher-contrast unit is driving the score. Say so,
            # and say what the cell itself is made of, or the evidence reads as
            # if the cell were something it is not.
            where = (
                f"in the {LITHO_BUFFER_M:.0f} m buffer; the {scale} itself holds "
                f"{in_cell}"
            )
        out.append(
            f"OF-00-495 unit {wofe_unit} ({meta['formation']}) {where}; OF01-501 "
            f"contrast {meta['contrast']} from {meta['training_sites']} of "
            f"{N_TRAINING_SITES} training sites"
        )
    elif best["unit"]:
        out.append(
            f"OF-00-495 unit {best['unit']} — zero training sites in OF01-501, "
            f"ranked 'Outside'"
        )
    else:
        out.append("No OF-00-495 lithology label in this cell (nodata)")

    d = best["fault_distance_m"]
    if d is None:
        out.append(
            f"No OF-00-495 mapped fault within {PLACER_BUFFER_M:.0f} m (published "
            f"optimum buffer {FAULT_BUFFER_M:.0f} m)"
        )
    elif best["fault_within_buffer"]:
        out.append(
            f"Nearest OF-00-495 mapped fault {d:.0f} m — inside the published "
            f"{FAULT_BUFFER_M:.0f} m optimum buffer (its contrast is ASSUMED "
            f"{FAULT_CONTRAST_ASSUMED}, not published)"
        )
    else:
        out.append(
            f"Nearest OF-00-495 mapped fault {d:.0f} m — outside the published "
            f"{FAULT_BUFFER_M:.0f} m optimum buffer"
        )

    trend = best["favourable_trend"]
    if trend is None:
        out.append(
            "Fault azimuth unknown: the OF-00-495 raster carries no orientation "
            "and the 1:24k geology does not map this cell, so the published "
            "345°-030° trend filter could not be applied"
        )
    elif trend:
        out.append(
            "A 1:24k mapped fault within 1.7 km trends in the OF01-501 "
            "favourable 345°-030° band"
        )
    else:
        out.append(
            "No 1:24k mapped fault within 1.7 km trends in the favourable "
            "345°-030° band"
        )

    lo, hi = TRACT_SCORE_BANDS[tract]
    out.append(
        f"Tract: {tract} (published score guidance {lo:.2f}-{hi:.2f}); posterior "
        f"{best['posterior']:.2e} — reported for transparency only, the published "
        f"posteriors are not reproducible from this raster"
    )
    if res != WOFE_CELL_RESOLUTION_M:
        out.append(
            f"Scored natively on {n_children} × 250 m cell(s) and "
            f"{'inherited by' if res < WOFE_CELL_RESOLUTION_M else 'averaged to'} "
            f"this {res} m cell — the WofE predictors are presence tests and "
            f"saturate if applied at coarse resolution"
        )
    if coverage < 1.0:
        out.append(f"Only {coverage:.0%} of the cell is inside the study area")
    return out


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------

_BASELINE: Optional[WofEBaseline] = None


def get_baseline() -> WofEBaseline:
    global _BASELINE
    if _BASELINE is None:
        _BASELINE = WofEBaseline()
    return _BASELINE


def score_cells_wofe(
    grid_cells: Iterable[Any],
    target_mineral: str = "gold",
    baseline: Optional[WofEBaseline] = None,
) -> Dict[str, Dict[str, Any]]:
    """Deterministic OF01-501 score per cell. See the module docstring for scope.

    ``grid_cells`` may be ``GridCell`` objects, ``model_dump()`` dicts, or bare
    cell-id strings. Every input cell appears in the result; cells the model
    declines to score carry ``score=None`` and a ``refused`` reason.
    """
    return (baseline or get_baseline()).score_cells(grid_cells, target_mineral)


def scored_only(results: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """``cell_id -> score`` for the cells that got one. Refusals are dropped.

    Convenience for rank correlation, where a refused cell is not a zero and
    must never be averaged in as one.
    """
    return {
        cid: float(r["score"])
        for cid, r in results.items()
        if r.get("score") is not None
    }
