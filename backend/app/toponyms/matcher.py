"""
Toponymic evidence: mining-related place names as historical signal.

Place names in an AOI carry information. "Gold Creek", "Bonanza Basin",
"Tunnel Gulch" were largely assigned by the people who worked the ground, and
they persist on maps long after the workings were abandoned or forgotten.

Three properties of this module matter more than its cleverness:

**Matching is deterministic.** Regex against a curated, versioned lexicon. No
LLM decides what counts as a match — it only interprets what the matcher found.
That keeps the feature reproducible run to run, which the benchmark requires.

**Streams are attributed along their length, not at their mouth.** GNIS places
a stream's primary coordinate at its confluence, which can be kilometres from
the workings that named it — the single most likely source of silent error in
this whole feature. The WA GNIS extract carries `source_lat_dec` /
`source_long_dec` (the headwaters) for every one of its 6,389 streams, so a
stream is attributed to cells along the mouth→source segment.

**Density is normalised.** Accessible valleys carry many times the named-feature
density of remote high country, so a raw count of mining-flavoured names per
cell measures road access, not mineralization. Hits are reported as a ratio
against all named features nearby.
"""
import csv
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

LEXICON_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "toponyms"
GNIS_PATH = DATA_DIR / "reference" / "gnis_wa.tsv"

#: Citation form for data_sources_used, matching the convention in
#: .claude/skills/wa-historical-geology-source.md.
GNIS_CITATION = "USGS_GNIS_DomesticNames_WA"


@dataclass
class ToponymName:
    """One GNIS record, reduced to what the matcher needs."""

    feature_id: str
    name: str
    feature_class: str
    lat: float
    lon: float
    #: Headwaters for streams; None for point features.
    source_lat: Optional[float] = None
    source_lon: Optional[float] = None
    map_name: str = ""

    def segment(self) -> List[Tuple[float, float]]:
        """Sample points representing the feature's extent, as (lon, lat).

        A point feature is one sample. A stream is sampled along the straight
        line from mouth to source — a crude flowline, but crude and along the
        creek beats exact and at the wrong end of it.
        """
        if self.source_lat is None or self.source_lon is None:
            return [(self.lon, self.lat)]
        dx = self.source_lon - self.lon
        dy = self.source_lat - self.lat
        length_deg = math.hypot(dx, dy)
        if length_deg < 1e-6:
            return [(self.lon, self.lat)]
        # One sample per ~500 m, capped so a long river cannot dominate the run
        steps = max(2, min(60, int(length_deg * 111_000 / 500)))
        return [
            (self.lon + dx * i / steps, self.lat + dy * i / steps)
            for i in range(steps + 1)
        ]


@dataclass
class ToponymHit:
    """A lexicon match on a named feature."""

    name: str
    feature_class: str
    tier: int
    tier_name: str
    matched_term: str
    lat: float
    lon: float
    #: Distance from the cell centre to the nearest sample of the feature, km.
    distance_km: float = 0.0
    #: Set by corroborate(): km to the nearest recorded occurrence, or None.
    nearest_occurrence_km: Optional[float] = None
    nearest_occurrence_name: Optional[str] = None
    corroboration: str = "unknown"  # corroborated | uncorroborated | unknown

    def evidence_string(self) -> str:
        """A sentence a human can act on.

        Deliberately verbose: "historical mining activity suggested" sends
        nobody to the archives, and this is meant to.
        """
        bits = [
            f'Toponym: "{self.name}" (GNIS {self.feature_class}, Tier {self.tier} '
            f"{self.tier_name.replace('_', ' ')}; matched \"{self.matched_term}\") "
            f"{self.distance_km:.1f} km from cell centre."
        ]
        if self.corroboration == "corroborated":
            bits.append(
                f"Recorded occurrence {self.nearest_occurrence_name!r} "
                f"{self.nearest_occurrence_km:.1f} km away — corroborated."
            )
        elif self.corroboration == "uncorroborated":
            bits.append("No recorded occurrence within 5 km — uncorroborated.")
        else:
            bits.append("Occurrence data unavailable — corroboration unknown.")
        return " ".join(bits)


@dataclass
class Lexicon:
    version: str
    mineral: str
    tiers: Dict[int, Dict[str, Any]]
    matching: Dict[str, Any]
    corroboration: Dict[str, Any]
    caps: Dict[str, Any]
    raw_text: str = ""
    _patterns: Dict[int, List[Tuple[str, "re.Pattern"]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        boundary = self.matching.get("word_boundary", True)
        for tier, spec in self.tiers.items():
            pats = []
            for term in spec.get("terms", []):
                escaped = re.escape(term)
                pattern = rf"\b{escaped}\b" if boundary else escaped
                pats.append((term, re.compile(pattern, re.IGNORECASE)))
            # Longest terms first: "Gold Bar" (anti-signal) must be tested
            # before "Gold" (tier 2) so the more specific rule wins.
            pats.sort(key=lambda t: -len(t[0]))
            self._patterns[tier] = pats

    @property
    def anti_signal_tier(self) -> int:
        for tier, spec in self.tiers.items():
            if spec.get("name") == "anti_signal":
                return tier
        return 5

    def classify(self, name: str, feature_class: str) -> Optional[Tuple[int, str]]:
        """(tier, matched_term) for a name, or None if it is not a match.

        The anti-signal tier is tested first and suppresses the name outright —
        without it the matcher flags every Goldmyer and Mill Creek in the
        Cascades with full confidence.
        """
        if feature_class in set(self.matching.get("excluded_classes", [])):
            return None

        anti = self.anti_signal_tier
        for term, pattern in self._patterns.get(anti, []):
            if pattern.search(name):
                return anti, term

        landform_classes = set(self.matching.get("landform_classes", []))
        for tier in sorted(t for t in self.tiers if t != anti):
            spec = self.tiers[tier]
            if spec.get("landform_only") and feature_class not in landform_classes:
                continue
            for term, pattern in self._patterns[tier]:
                if pattern.search(name):
                    return tier, term
        return None


def load_lexicon(path: Optional[Path] = None, mineral: str = "gold") -> Optional[Lexicon]:
    """Load the versioned lexicon YAML. Returns None when absent."""
    import yaml

    p = Path(path) if path else LEXICON_DIR / f"{mineral.lower()}_wa.yaml"
    if not p.exists():
        logger.info("No toponym lexicon at %s — toponymic evidence disabled", p)
        return None
    text = p.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    return Lexicon(
        version=doc.get("version", "unknown"),
        mineral=doc.get("mineral", mineral),
        tiers={int(k): v for k, v in doc.get("tiers", {}).items()},
        matching=doc.get("matching", {}),
        corroboration=doc.get("corroboration", {}),
        caps=doc.get("caps", {}),
        raw_text=text,
    )


def load_gnis(path: Optional[Path] = None) -> List[ToponymName]:
    """Load the static GNIS Washington extract.

    A static file rather than the live service: no runtime dependency, no rate
    limit, works offline, and — the reason that matters here — deterministic
    across runs, which the benchmark requires.
    """
    p = Path(path) if path else GNIS_PATH
    if not p.exists():
        logger.info("No GNIS extract at %s — toponymic evidence disabled", p)
        return []

    names: List[ToponymName] = []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if lat == 0.0 and lon == 0.0:
                continue

            def _opt(key: str) -> Optional[float]:
                v = row.get(key) or ""
                try:
                    f = float(v)
                except ValueError:
                    return None
                return None if f == 0.0 else f

            names.append(
                ToponymName(
                    feature_id=row.get("feature_id", ""),
                    name=row.get("name", ""),
                    feature_class=row.get("feature_class", ""),
                    lat=lat,
                    lon=lon,
                    source_lat=_opt("source_lat"),
                    source_lon=_opt("source_lon"),
                    map_name=row.get("map_name", ""),
                )
            )
    logger.info("Loaded %d GNIS names from %s", len(names), p)
    return names


# --- geometry helpers ------------------------------------------------------


def _km_between(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Equirectangular approximation — accurate to well under 1% at AOI scale.

    Delegates to ``app.spatial.geometry`` so there is exactly one definition of
    "how far apart are these two points" in the codebase. It used to carry its own
    copy of the constants, rounded slightly differently (110.57 vs 110.574 km per
    degree of latitude), which made toponym corroboration distances and occurrence
    distances disagree by ~36 ppm. Harmless at that size, but two modules
    disagreeing about distance is not a property worth keeping.
    """
    from app.spatial.geometry import km_between

    return km_between(lon1, lat1, lon2, lat2)


def match_names(
    names: Iterable[ToponymName], lexicon: Lexicon
) -> List[Tuple[ToponymName, int, str]]:
    """Every lexicon match in ``names``, including anti-signal hits.

    Anti-signal matches are returned rather than dropped so the caller can log
    them — that log is how the anti-signal list gets evaluated and grown.
    """
    out = []
    for n in names:
        hit = lexicon.classify(n.name, n.feature_class)
        if hit:
            out.append((n, hit[0], hit[1]))
    return out


def toponyms_for_cells(
    cells: Sequence[Dict[str, Any]],
    names: Iterable[ToponymName],
    lexicon: Lexicon,
    occurrences: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Per-cell toponym evidence, keyed by cell_id.

    Each entry carries the scoring hits, the suppressed anti-signal matches, and
    a density figure — hits as a share of all named features in the cell — so
    that a valley full of names does not outscore a remote basin purely on
    having more labels on the map.
    """
    anti = lexicon.anti_signal_tier
    matched = match_names(names, lexicon)
    all_names = list(names)

    # Cell centres and a radius that covers the cell plus a margin
    centres: List[Tuple[str, float, float, float]] = []
    for c in cells:
        bbox = c.get("bbox") or [0, 0, 0, 0]
        clon = (bbox[0] + bbox[2]) / 2
        clat = (bbox[1] + bbox[3]) / 2
        half_km = max(
            _km_between(bbox[0], clat, bbox[2], clat) / 2,
            _km_between(clon, bbox[1], clon, bbox[3]) / 2,
        )
        centres.append((c.get("cell_id", ""), clon, clat, half_km))

    out: Dict[str, Dict[str, Any]] = {}
    for cell_id, clon, clat, half_km in centres:
        # A name counts for a cell if any sample of it falls within the cell's
        # half-diagonal plus a small margin.
        reach = half_km * 1.5

        hits: List[ToponymHit] = []
        suppressed: List[str] = []
        for n, tier, term in matched:
            d = min(
                _km_between(clon, clat, slon, slat) for slon, slat in n.segment()
            )
            if d > reach:
                continue
            if tier == anti:
                suppressed.append(f'{n.name} (matched anti-signal "{term}")')
                continue
            hits.append(
                ToponymHit(
                    name=n.name,
                    feature_class=n.feature_class,
                    tier=tier,
                    tier_name=lexicon.tiers[tier].get("name", str(tier)),
                    matched_term=term,
                    lat=n.lat,
                    lon=n.lon,
                    distance_km=round(d, 2),
                )
            )

        nearby_total = sum(
            1
            for n in all_names
            if any(
                _km_between(clon, clat, slon, slat) <= reach
                for slon, slat in n.segment()
            )
        )

        if occurrences is not None:
            _corroborate(hits, occurrences, lexicon)

        if not hits and not suppressed:
            continue

        hits.sort(key=lambda h: (h.tier, h.distance_km))
        out[cell_id] = {
            "hits": hits,
            "suppressed": suppressed,
            "named_features_nearby": nearby_total,
            # The density figure §22 asks for: hits as a share of all names
            # nearby, so access-rich valleys do not win on label count alone.
            "hit_density": round(len(hits) / nearby_total, 4) if nearby_total else 0.0,
            "lexicon_version": lexicon.version,
        }
    return out


def _corroborate(
    hits: List[ToponymHit],
    occurrences: Sequence[Dict[str, Any]],
    lexicon: Lexicon,
) -> None:
    """Annotate hits with distance to the nearest recorded mineral occurrence.

    The highest-value step in this whole feature and it is pure geometry — no
    LLM, no web, no ambiguity. "Gold Creek" with an MRDS record 400 m away is
    corroborated; "Gold Creek" with nothing within 5 km is a lead for a human,
    not a score.
    """
    near_km = float(lexicon.corroboration.get("corroborated_within_km", 1.5))
    far_km = float(lexicon.corroboration.get("uncorroborated_beyond_km", 5.0))

    pts = [
        (o.get("lon"), o.get("lat"), o.get("name") or "unnamed occurrence")
        for o in occurrences
        if o.get("lon") is not None and o.get("lat") is not None
    ]
    for h in hits:
        if not pts:
            h.corroboration = "unknown"
            continue
        d, nm = min(
            ((_km_between(h.lon, h.lat, lo, la), nm) for lo, la, nm in pts),
            key=lambda t: t[0],
        )
        h.nearest_occurrence_km = round(d, 2)
        h.nearest_occurrence_name = nm
        if d <= near_km:
            h.corroboration = "corroborated"
        elif d >= far_km:
            h.corroboration = "uncorroborated"
        else:
            h.corroboration = "unknown"


def score_cap_for(hits: Sequence[ToponymHit], lexicon: Lexicon) -> float:
    """Highest score toponymic evidence alone may justify for a cell.

    Toponyms sit below district proximity in the assay-primacy hierarchy of
    knowledge/historical/gold.md, so these caps are deliberately low. A cell
    with nothing but a suggestive name may never reach the `high` tier on that
    basis alone.
    """
    if not hits:
        return 0.0
    caps = lexicon.caps
    strong = [h for h in hits if h.tier in (1, 2)]
    weak = [h for h in hits if h.tier in (3, 4)]

    if any(h.corroboration == "corroborated" for h in hits):
        # The occurrence already scored this cell; the name adds legibility.
        return 0.0

    cap = 0.0
    if strong:
        cap = float(caps.get("uncorroborated_tier_1_2", 0.45))
    elif weak:
        cap = float(caps.get("uncorroborated_tier_3_4", 0.30))

    distinct = len({h.name for h in strong})
    if distinct >= int(caps.get("cluster_min_names", 3)):
        cap += float(caps.get("cluster_bonus", 0.10))
    return round(min(cap, 0.6), 4)
