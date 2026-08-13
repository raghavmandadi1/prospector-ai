"""
USGS OF-00-495 geologic grids for NE Washington, on the analysis ladder.

``scripts/build_of00495.py`` reads the four ArcInfo grids in
``data/raw/of00-495/`` — geologic map units at 50 m, folds at 50 m, faults at
100 m, dikes at 200 m — and aggregates them onto 250 m cells of the fixed
EPSG:5070 grid, in ``data/derived/of00495.sqlite``. Because 250 m is a rung of
``RESOLUTION_LADDER`` and the ladder nests as a quadtree, any analysis cell at
250 m or coarser is an exact union of rows in that table.

**Why this dataset rather than the statewide 24k geology.** The 24k geodatabase
covers all of Washington and is better in almost every way, except the one that
matters here: its unit labels are quad-local (``Evs(t)``, ``Ev(p)``) and none of
the OF01-501 weights-of-evidence codes appear in any of its 2,186 distinct
values. OF-00-495 was compiled specifically as the input to that WofE study, and
its value-attribute table carries the standardised labels — ``Evsf``, ``Evst``,
``Eck``, ``Evkct``, ``Evkf``, ``Eco`` — that the published contrasts are keyed
to. It is the only dataset on disk that can attach a *measured* predictive weight
to a cell instead of a model's opinion of one.

**Scope, and why it is enforced rather than noted.** The study area is the six
1:100,000 quadrangles between 117°–120°W and 48°–49°N: Colville, Chewelah,
Republic, Nespelem, Omak, Oroville. The contrasts were fitted on 50 epithermal
gold training sites *there*. They do not describe orogenic gold at Blewett or
Monte Cristo, and they say nothing at all about ground west of the Cascade crest.
Lookups outside the footprint return nothing rather than an extrapolated number,
because a confident value from an out-of-scope model is worse than a blank.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.config import DERIVED_DIR

logger = logging.getLogger(__name__)

WOFE_DB = DERIVED_DIR / "of00495.sqlite"

#: Citation strings for `data_sources_used`.
OF00495_CITATION = "USGS_OF00_495"
OF01501_CITATION = "USGS_OF01_501"

#: Resolution the derived table is keyed at. A rung of RESOLUTION_LADDER, so
#: coarser analysis cells decompose into a whole number of these.
WOFE_CELL_RESOLUTION_M = 250

#: Published weights-of-evidence contrasts, Boleneus et al. 2001 (USGS
#: OF01-501), 50 epithermal gold training sites over a 222 × 277 km area of NE
#: Washington. These six units are the *only* ones that showed positive spatial
#: correlation with gold; the study tested 150+ units and every other one had
#: zero training sites and ranked "Outside".
#:
#: A contrast above 2.0 is a studentized value at roughly the 98% confidence
#: level, so these are not soft priors — they are measurements. Note that Evsf
#: hosts 30 of the 50 sites (the dominant host by far) while Eck has the highest
#: contrast because it concentrates 4 sites into 26 km²; "most important" and
#: "most predictive per unit area" are different questions.
WOFE_CONTRASTS: Dict[str, Dict[str, Any]] = {
    "Eck": {
        "contrast": 4.55,
        "training_sites": 4,
        "area_km2": 26.3,
        "formation": "Klondike Mountain Fm — volcaniclastic rocks",
    },
    "Evkct": {
        "contrast": 3.62,
        "training_sites": 5,
        "area_km2": 43.1,
        "formation": "Klondike Mountain Fm — conglomerate & tuffs",
    },
    "Evst": {
        "contrast": 3.42,
        "training_sites": 5,
        "area_km2": 50.6,
        "formation": "Sanpoil Volcanics — tuffs",
    },
    "Evsf": {
        "contrast": 3.21,
        "training_sites": 30,
        "area_km2": 302.5,
        "formation": "Sanpoil Volcanics — flows",
    },
    "Evkf": {
        "contrast": 2.56,
        "training_sites": 3,
        "area_km2": 45.3,
        "formation": "Klondike Mountain Fm — flows",
    },
    "Eco": {
        "contrast": 1.96,
        "training_sites": 2,
        "area_km2": 35.2,
        "formation": "O'Brien Creek Fm — metasediments",
    },
}

#: Fault-type codes, from Appendix B-2 of the report ("Description of Value item
#: in newafaul.vat"). The `.e00` value-attribute table itself carries only VALUE
#: and COUNT with empty labels, so these look opaque if you only read the raster —
#: but the printed report defines every one of them, and the distinction is not
#: cosmetic. The OF01-501 structural predictor is specifically **normal** faults:
#: a thrust is Mesozoic contraction that pre-dates the Eocene ore event, and a
#: low-angle normal fault is a core-complex detachment — regional-scale plumbing,
#: not a steep vein conduit. Reporting "fault code 7" and "fault code 43" as
#: interchangeable would throw that away.
FAULT_CODES: Dict[int, str] = {
    0: "unknown fault type",
    1: "fault, unknown offset",
    2: "fault, unknown offset (approximate location)",
    3: "fault, unknown offset (concealed)",
    4: "fault, unknown offset (queried)",
    7: "thrust fault",
    8: "thrust fault (approximate location)",
    9: "thrust fault (concealed)",
    10: "thrust fault (queried)",
    31: "low-angle normal fault",
    33: "low-angle normal fault (concealed)",
    43: "normal fault",
    44: "normal fault (concealed)",
    45: "normal fault (concealed)",
}

#: Fold-axis codes, Appendix B-1 ("Description of Value item in newafold.vat").
FOLD_CODES: Dict[int, str] = {
    1: "anticline",
    2: "anticline (approximate location)",
    3: "anticline (concealed)",
    7: "overturned anticline",
    8: "overturned anticline (approximate location)",
    9: "overturned anticline (concealed)",
    13: "syncline",
    15: "syncline (concealed)",
    19: "overturned syncline",
    20: "overturned syncline (approximate location)",
    21: "overturned syncline (concealed)",
    31: "monocline, anticlinal bend",
    32: "monocline, anticlinal bend (approximate location)",
    33: "monocline, anticlinal bend (concealed)",
}

#: Fault codes that are the OF01-501 predictor class. Normal faults only —
#: the study measured trend 345°-030° on *normal* faults against 50 epithermal
#: training sites. Low-angle normal (31/33) is deliberately excluded: same
#: extension, different plumbing.
PREDICTOR_FAULT_CODES = frozenset({43, 44, 45})


def describe_fault(code: Optional[int]) -> Optional[str]:
    """Human-readable fault type for an OF-00-495 code (Appendix B-2)."""
    if code is None:
        return None
    return FAULT_CODES.get(int(code))


def describe_fold(code: Optional[int]) -> Optional[str]:
    """Human-readable fold type for an OF-00-495 code (Appendix B-1)."""
    if code is None:
        return None
    return FOLD_CODES.get(int(code))


#: Posterior-probability tract thresholds from the same study, with the share of
#: the study area and the training sites each tract captured. The headline
#: result: 4.6% of the landscape contains 49 of 50 known deposits.
WOFE_TRACTS = (
    {"tract": "favourable", "min_posterior": 0.024, "area_pct": 4.6, "sites": 49},
    {"tract": "permissive", "min_posterior": 0.000167, "area_pct": 3.4, "sites": 1},
    {"tract": "non_permissive", "min_posterior": 0.0, "area_pct": 92.0, "sites": 0},
)


def contrast_for(unit: Optional[str]) -> Optional[float]:
    """Published contrast for a standardised OF-00-495 unit label.

    Returns ``None`` for a unit the study found no correlation with — which is
    an answer, not a gap. Callers must not treat ``None`` as 0.0 and then
    average it into something; the study's finding is that 92% of NE Washington
    is non-permissive, and that deserves to be said explicitly.
    """
    if not unit:
        return None
    entry = WOFE_CONTRASTS.get(unit)
    return entry["contrast"] if entry else None


@dataclass
class WofeCell:
    """One 250 m cell of the OF-00-495 aggregation."""

    cell_id: str
    geol_unit: Optional[str]
    geol_unit_frac: Optional[float]
    fault_code: Optional[int]
    fold_code: Optional[int]
    dike_unit: Optional[str]


class WofeGridStore:
    """Read-only view over ``of00495.sqlite``. Absent database ⇒ empty answers."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else WOFE_DB
        self._conn: Optional[sqlite3.Connection] = None
        self._warned = False

    @property
    def available(self) -> bool:
        return self._connect() is not None

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        if not self.path.exists():
            if not self._warned:
                logger.info(
                    "No OF-00-495 store at %s — no published WofE contrast will "
                    "be attached to any cell (build it with "
                    "scripts/build_of00495.py)",
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
            logger.warning("Could not open OF-00-495 store %s: %s", self.path, exc)
            return None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # -- lookup ------------------------------------------------------------

    def facts_for_cells(
        self, cell_ids: Sequence[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Aggregate OF-00-495 facts for analysis cells of any ladder resolution.

        A cell coarser than 250 m is decomposed into its 250 m descendants and
        their labels are combined by area share; a 125 m cell inherits from its
        250 m parent, flagged so the caller knows it is inherited rather than
        measured at that scale. Cells outside the study footprint are simply
        absent from the result.
        """
        conn = self._connect()
        if conn is None or not cell_ids:
            return {}

        from app.scoring.grid import (
            RESOLUTION_LADDER,
            make_cell_id,
            parent_cell_id,
            parse_cell_id,
        )

        # analysis cell_id -> the 250 m ids that tile it (or its parent's id)
        wanted: Dict[str, List[str]] = {}
        inherited: set = set()
        for cid in cell_ids:
            try:
                res, col, row = parse_cell_id(cid)
            except Exception:
                continue
            if res == WOFE_CELL_RESOLUTION_M:
                wanted[cid] = [cid]
            elif res < WOFE_CELL_RESOLUTION_M:
                try:
                    wanted[cid] = [parent_cell_id(cid, WOFE_CELL_RESOLUTION_M)]
                    inherited.add(cid)
                except Exception:
                    continue
            else:
                k = res // WOFE_CELL_RESOLUTION_M
                base_col, base_row = col * k, row * k
                wanted[cid] = [
                    make_cell_id(base_col + dc, base_row + dr, WOFE_CELL_RESOLUTION_M)
                    for dc in range(k)
                    for dr in range(k)
                ]

        needed = sorted({c for ids in wanted.values() for c in ids})
        rows = self._fetch(conn, needed)
        if not rows:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for cid, child_ids in wanted.items():
            children = [rows[c] for c in child_ids if c in rows]
            if not children:
                continue
            agg = _aggregate(children)
            if cid in inherited:
                agg["inherited_from_250m"] = True
            out[cid] = agg
        return out

    def _fetch(
        self, conn: sqlite3.Connection, cell_ids: Sequence[str]
    ) -> Dict[str, WofeCell]:
        """Batched SELECT. Chunked to stay clear of SQLite's variable limit."""
        found: Dict[str, WofeCell] = {}
        chunk = 800
        for i in range(0, len(cell_ids), chunk):
            batch = cell_ids[i : i + chunk]
            placeholders = ",".join("?" * len(batch))
            try:
                for row in conn.execute(
                    f"""SELECT cell_id, geol_unit, geol_unit_frac, fault_code,
                               fold_code, dike_unit
                          FROM wofe_cell WHERE cell_id IN ({placeholders})""",
                    tuple(batch),
                ):
                    found[row["cell_id"]] = WofeCell(
                        cell_id=row["cell_id"],
                        geol_unit=row["geol_unit"],
                        geol_unit_frac=row["geol_unit_frac"],
                        fault_code=row["fault_code"],
                        fold_code=row["fold_code"],
                        dike_unit=row["dike_unit"],
                    )
            except Exception as exc:
                logger.warning("OF-00-495 lookup failed: %s", exc)
                return found
        return found


def _aggregate(children: Sequence[WofeCell]) -> Dict[str, Any]:
    """Combine 250 m rows into one analysis cell's worth of facts.

    Unit shares are summed over children weighted by each child's own coverage
    fraction, so a coarse cell reports the mix it actually contains. The
    favourable-unit share is called out separately: for the WofE model the
    question is not "what is the modal rock" but "how much of this cell is
    permissive ground", and a cell that is 30% Sanpoil flows is a target even
    when 70% of it is glacial till.
    """
    shares: Dict[str, float] = {}
    total = 0.0
    for c in children:
        if not c.geol_unit:
            continue
        w = c.geol_unit_frac if c.geol_unit_frac is not None else 1.0
        shares[c.geol_unit] = shares.get(c.geol_unit, 0.0) + float(w)
        total += float(w)

    out: Dict[str, Any] = {"source_cells": len(children)}
    if total > 0:
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])
        out["units"] = [
            {"unit": u, "frac": round(v / total, 3), "contrast": contrast_for(u)}
            for u, v in ranked[:4]
        ]
        modal_unit, modal_share = ranked[0]
        out["unit"] = modal_unit
        out["unit_frac"] = round(modal_share / total, 3)
        out["contrast"] = contrast_for(modal_unit)

        favourable = {
            u: v for u, v in shares.items() if u in WOFE_CONTRASTS
        }
        if favourable:
            best = max(favourable.items(), key=lambda kv: WOFE_CONTRASTS[kv[0]]["contrast"])
            out["favourable_unit"] = best[0]
            out["favourable_contrast"] = WOFE_CONTRASTS[best[0]]["contrast"]
            out["favourable_frac"] = round(sum(favourable.values()) / total, 3)
            out["formation"] = WOFE_CONTRASTS[best[0]]["formation"]

    faults = sorted({int(c.fault_code) for c in children if c.fault_code is not None})
    if faults:
        out["fault_codes"] = faults[:6]
        out["fault_types"] = [
            describe_fault(f) or f"code {f}" for f in faults[:6]
        ]
        # Whether any of them is the class the OF01-501 trend rule was fitted on.
        # A cell whose only structure is a thrust is not a match for that rule.
        out["has_predictor_fault"] = any(f in PREDICTOR_FAULT_CODES for f in faults)
    folds = sorted({int(c.fold_code) for c in children if c.fold_code is not None})
    if folds:
        out["fold_codes"] = folds[:6]
        out["fold_types"] = [describe_fold(f) or f"code {f}" for f in folds[:6]]
    dikes = [c.dike_unit for c in children if c.dike_unit]
    if dikes:
        out["dike_units"] = sorted(set(dikes))[:4]
    return out


_STORE: Optional[WofeGridStore] = None


def get_store() -> WofeGridStore:
    global _STORE
    if _STORE is None:
        _STORE = WofeGridStore()
    return _STORE
