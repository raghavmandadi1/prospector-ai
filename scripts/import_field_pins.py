#!/usr/bin/env python3
"""
Import field pins from Google My Maps or Gaia GPS into data/user_sites/.

    # First pass: convert, everything lands as role=display (always safe)
    python scripts/import_field_pins.py MyMap.kmz --name lennox

    # Get a spreadsheet listing every pin, fill in provenance/role/observed
    python scripts/import_field_pins.py MyMap.kmz --emit-template pins.csv

    # Second pass: apply the annotations
    python scripts/import_field_pins.py MyMap.kmz --name lennox --annotations pins.csv

Output is `data/user_sites/<name>.geojson`, the normalized schema in
CONTRACT.md, gitignored, drawn as the "My Sites" map layer.

Three things this script is careful about, in order of how badly they would hurt:

**`role` defaults to `display` and is never promoted silently.** A `truth` pin is
benchmark ground truth; a model that sees it makes the benchmark tautological
(steps-2.0 §30). An `evidence` pin is fed to the agents. Both promotions must be
a deliberate per-pin decision, so they come only from `--role` or the
annotations CSV, and every one of them is printed as a warning at the end of the
run. See `app.spatial.user_sites` for the runtime half of the same rule.

**Provenance is the whole feature, not a nicety.** The useful split is not "my
pins vs public data", it is *"I read about this" vs "I stood here"* (§30.1). A
`field_visit` pin is a real observation that exists in no database and is
legitimate evidence; a `hearsay` or `inference` pin is a belief, and feeding it
to a model that then agrees with it has told you nothing. Nothing downstream can
be decided without that split, which is why the CSV path exists — tens of pins
is a spreadsheet job, not a prompt-per-pin job.

**Nothing is dropped quietly.** Tracks, routes, lines and polygons are counted
and reported rather than ignored, because "the import worked" while half a KMZ
vanished is exactly the failure this script is here to prevent.

No GDAL: KML and GPX are parsed with xml.etree, KMZ with zipfile. That is not a
hardship — both formats are simple — and it keeps the script runnable on a
laptop with nothing installed but the repo's own venv.

Re-running on unchanged inputs produces a byte-identical file, so `git status`
(or a diff against the previous export) means something. That is also why there
is no `built_at` in the output.
"""
import argparse
import csv
import json
import math
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

try:
    from app.config import DATA_DIR
    from app.spatial.user_sites import (
        DEFAULT_POSITION_CONFIDENCE,
        DEFAULT_PROVENANCE,
        DEFAULT_ROLE,
        FIELD_PROVENANCE,
        POSITION_CONFIDENCES,
        PROVENANCES,
        ROLES,
        USER_SITES_DIR,
    )
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    raise SystemExit(
        f"cannot import the backend package ({exc}).\n"
        "Run this with the repo's venv, e.g. .venv/bin/python scripts/import_field_pins.py"
    )

OCCURRENCES_PATH = DATA_DIR / "reference" / "wa_occurrences.geojson"

#: §30.3 — a field-visit pin further than this from every recorded occurrence is
#: an undocumented working located by someone who went there, which is the most
#: interesting kind of record in the system. 200 m is the spec's figure; it is
#: also roughly the positional slop on a `LOCATION_ACCURACY` of
#: "coordinates estimated from location description", so tightening it would
#: mostly manufacture novelty out of database error.
NEW_SITE_THRESHOLD_KM = 0.2

#: Columns of the annotations CSV. `source_description` is written by
#: --emit-template as read-only context (the text already in the export) and is
#: ignored on read.
ANNOTATION_COLUMNS = [
    "pin_id",
    "name",
    "folder",
    "role",
    "provenance",
    "visited",
    "observed",
    "position_confidence",
    "date",
    "source_note",
]
TEMPLATE_COLUMNS = ANNOTATION_COLUMNS + ["source_description"]

#: Property order in the output file. Fixed so a re-import diffs cleanly.
PROPERTY_ORDER = [
    "pin_id",
    "name",
    "role",
    "provenance",
    "visited",
    "observed",
    "position_confidence",
    "date",
    "source_note",
    "folder",
    "symbol",
    "source_file",
    "nearest_db_km",
    "nearest_db_name",
    "potentially_new",
]

TRUE_WORDS = {"y", "yes", "true", "t", "1"}
FALSE_WORDS = {"n", "no", "false", "f", "0", ""}


# --- geometry --------------------------------------------------------------

try:  # pyproj is a hard dependency of the backend; the fallback is for safety.
    from pyproj import Geod

    _GEOD: Optional["Geod"] = Geod(ellps="WGS84")
except Exception:  # pragma: no cover
    _GEOD = None


def distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance on WGS84, in kilometres.

    Degrees are not a distance. At 48°N a degree of longitude is 74.6 km and a
    degree of latitude is 111.2 km, so comparing raw coordinate deltas against a
    200 m threshold is wrong by a third in one axis — and wrong in the direction
    that invents new discoveries. The fallback is the same equirectangular
    approximation `toponyms.matcher._km_between` uses (well under 1% at AOI
    scale); the geodesic is preferred because it costs nothing here.
    """
    if _GEOD is not None:
        return _GEOD.inv(lon1, lat1, lon2, lat2)[2] / 1000.0
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * math.cos(mean_lat) * 111.32
    dy = (lat2 - lat1) * 110.57
    return math.hypot(dx, dy)


# --- parsing ---------------------------------------------------------------


@dataclass
class RawPin:
    """One point as it came out of the source file, before annotation."""

    name: str
    lon: float
    lat: float
    description: str = ""
    folder: str = ""
    symbol: str = ""
    date: Optional[str] = None
    source_file: str = ""
    #: Values the source file already carried (a re-import of a normalized
    #: GeoJSON). Applied over the CLI defaults, under the annotations CSV.
    carried: Dict[str, Any] = field(default_factory=dict)


def _local(tag: Any) -> str:
    """Local name of an XML tag, namespace and all.

    KML files in the wild declare 2.0, 2.1 or 2.2, sometimes with a `gx:`
    extension namespace mixed in, and Gaia's GPX is 1.1 while older exports are
    1.0. Matching on the local name handles every one of them; matching on a
    hardcoded namespace URI handles whichever one you tested against.
    """
    t = str(tag)
    return t.rpartition("}")[2]


def _child(el: ET.Element, name: str) -> Optional[ET.Element]:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _child_text(el: ET.Element, name: str) -> str:
    """Text of a direct child, flattened.

    `itertext` rather than `.text` because KML descriptions routinely contain
    HTML — Google My Maps writes a whole `<table>` in there — and `.text` would
    return only the fragment before the first tag.
    """
    c = _child(el, name)
    if c is None:
        return ""
    return " ".join("".join(c.itertext()).split())


def _descendants(el: ET.Element, name: str) -> List[ET.Element]:
    return [e for e in el.iter() if _local(e.tag) == name]


def _iso_date(value: str, warnings: List[str], where: str) -> Optional[str]:
    """`2025-09-14T17:02:00Z` → `2025-09-14`; anything else → None + a warning."""
    v = (value or "").strip()
    if not v:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", v)
    if m:
        return m.group(1)
    warnings.append(f"{where}: unparseable date {v!r} — left null")
    return None


def _parse_coordinates(text: str) -> Optional[Tuple[float, float]]:
    """`lon,lat[,alt]` → (lon, lat). Whitespace-tolerant."""
    parts = [p for p in re.split(r"[,\s]+", (text or "").strip()) if p]
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def parse_kml(text: str, source_file: str) -> Tuple[List[RawPin], Counter, List[str]]:
    """Placemarks from a KML document, walking every Folder and Document.

    Google My Maps puts each map layer in its own `<Folder>`, and multi-layer
    maps can nest a second `<Document>`; both are walked and the enclosing layer
    name is recorded as `folder`. The *outermost* Document's name is the map
    title rather than a layer, so it is deliberately not used.
    """
    pins: List[RawPin] = []
    skipped: Counter = Counter()
    warnings: List[str] = []

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SystemExit(f"{source_file}: not parseable as XML ({exc})")

    def placemark(el: ET.Element, folder: str) -> None:
        name = _child_text(el, "name")
        desc = _child_text(el, "description")
        when = _descendants(el, "when")
        date = _iso_date(
            when[0].text if when and when[0].text else "",
            warnings,
            f"{source_file}:{name or 'unnamed'}",
        )

        points = _descendants(el, "Point")
        for kind in ("LineString", "Polygon", "Track"):
            n = len(_descendants(el, kind))
            if n:
                skipped[kind] += n

        if not points:
            if not any(_descendants(el, k) for k in ("LineString", "Polygon", "Track")):
                skipped["no geometry"] += 1
            return

        for pt in points:
            coords = _child(pt, "coordinates")
            lonlat = _parse_coordinates(
                "".join(coords.itertext()) if coords is not None else ""
            )
            if lonlat is None:
                skipped["unparseable Point"] += 1
                continue
            pins.append(
                RawPin(
                    name=name,
                    lon=lonlat[0],
                    lat=lonlat[1],
                    description=desc,
                    folder=folder,
                    date=date,
                    source_file=source_file,
                )
            )

    def walk(el: ET.Element, folder: str, at_root: bool) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "Placemark":
                placemark(child, folder)
            elif tag == "Folder":
                walk(child, _child_text(child, "name") or folder, False)
            elif tag == "Document":
                # The root Document names the map, not a layer.
                inner = folder if at_root else (_child_text(child, "name") or folder)
                walk(child, inner, False)
            elif tag in ("name", "description", "Style", "StyleMap", "Schema"):
                continue
            else:
                walk(child, folder, at_root)

    walk(root, "", True)
    return pins, skipped, warnings


def parse_kmz(path: Path) -> Tuple[List[RawPin], Counter, List[str]]:
    """A KMZ is a zipped KML — sometimes several, so parse them all."""
    pins: List[RawPin] = []
    skipped: Counter = Counter()
    warnings: List[str] = []
    with zipfile.ZipFile(path) as zf:
        members = [n for n in sorted(zf.namelist()) if n.lower().endswith(".kml")]
        if not members:
            raise SystemExit(f"{path.name}: no .kml inside the archive")
        for member in members:
            text = zf.read(member).decode("utf-8-sig", errors="replace")
            p, s, w = parse_kml(text, path.name)
            pins.extend(p)
            skipped.update(s)
            warnings.extend(w)
    return pins, skipped, warnings


def parse_gpx(text: str, source_file: str) -> Tuple[List[RawPin], Counter, List[str]]:
    """Waypoints from a GPX file. Tracks and routes are counted, not imported.

    A track is where you walked, not a thing you found. Importing them as pins
    would put a hundred meaningless points on the map and, worse, into any
    `evidence` promotion applied in bulk.
    """
    pins: List[RawPin] = []
    skipped: Counter = Counter()
    warnings: List[str] = []

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SystemExit(f"{source_file}: not parseable as XML ({exc})")

    for kind, label in (("trk", "track"), ("rte", "route")):
        n = len(_descendants(root, kind))
        if n:
            skipped[label] += n

    for wpt in _descendants(root, "wpt"):
        try:
            lat = float(wpt.attrib["lat"])
            lon = float(wpt.attrib["lon"])
        except (KeyError, ValueError):
            skipped["wpt without lat/lon"] += 1
            continue
        name = _child_text(wpt, "name")
        desc = _child_text(wpt, "desc") or _child_text(wpt, "cmt")
        pins.append(
            RawPin(
                name=name,
                lon=lon,
                lat=lat,
                description=desc,
                symbol=_child_text(wpt, "sym"),
                date=_iso_date(
                    _child_text(wpt, "time"),
                    warnings,
                    f"{source_file}:{name or 'unnamed'}",
                ),
                source_file=source_file,
            )
        )
    return pins, skipped, warnings


#: Keys a normalized (or partially annotated) GeoJSON may carry through a
#: re-import. `role` is included on purpose: re-importing a file that was
#: already promoted must not silently demote it either, and the promotion
#: warning at the end reports it regardless of where it came from.
CARRIED_KEYS = (
    "role",
    "provenance",
    "visited",
    "observed",
    "position_confidence",
    "date",
    "source_note",
    "pin_id",
)


def parse_geojson(
    obj: Any, source_file: str
) -> Tuple[List[RawPin], Counter, List[str]]:
    """Accept a FeatureCollection (or a bare Feature/list) of Points."""
    pins: List[RawPin] = []
    skipped: Counter = Counter()
    warnings: List[str] = []

    if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
        feats = obj.get("features") or []
    elif isinstance(obj, dict) and obj.get("type") == "Feature":
        feats = [obj]
    elif isinstance(obj, list):
        feats = obj
    else:
        raise SystemExit(f"{source_file}: not a GeoJSON FeatureCollection")

    for feat in feats:
        if not isinstance(feat, dict):
            skipped["non-object feature"] += 1
            continue
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        gtype = geom.get("type") if isinstance(geom, dict) else None
        if gtype != "Point":
            skipped[str(gtype)] += 1
            continue
        lonlat = geom.get("coordinates")
        if not isinstance(lonlat, (list, tuple)) or len(lonlat) < 2:
            skipped["unparseable Point"] += 1
            continue
        try:
            lon, lat = float(lonlat[0]), float(lonlat[1])
        except (TypeError, ValueError):
            skipped["unparseable Point"] += 1
            continue

        carried = {k: props[k] for k in CARRIED_KEYS if k in props and props[k] not in (None, "")}
        pins.append(
            RawPin(
                name=str(props.get("name") or props.get("Name") or props.get("title") or ""),
                lon=lon,
                lat=lat,
                description=str(
                    props.get("description") or props.get("desc") or props.get("notes") or ""
                ),
                folder=str(props.get("folder") or ""),
                symbol=str(props.get("symbol") or props.get("sym") or ""),
                date=None,
                source_file=source_file,
                carried=carried,
            )
        )
    return pins, skipped, warnings


def parse_input(path: Path) -> Tuple[List[RawPin], Counter, List[str]]:
    suffix = path.suffix.lower()
    if suffix == ".kmz":
        return parse_kmz(path)
    if suffix == ".kml":
        return parse_kml(path.read_text(encoding="utf-8-sig", errors="replace"), path.name)
    if suffix == ".gpx":
        return parse_gpx(path.read_text(encoding="utf-8-sig", errors="replace"), path.name)
    if suffix in (".geojson", ".json"):
        return parse_geojson(json.loads(path.read_text(encoding="utf-8-sig")), path.name)
    raise SystemExit(
        f"{path.name}: unsupported extension {suffix!r} — expected "
        ".kml, .kmz, .gpx or .geojson"
    )


# --- normalization ---------------------------------------------------------


def slugify(name: str, maxlen: int = 48) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    s = re.sub(r"-{2,}", "-", s)
    return s[:maxlen].strip("-")


def assign_pin_ids(pins: Sequence[RawPin]) -> List[str]:
    """Stable slug per pin, with a numeric suffix on collision.

    Stable in the sense that matters: the same input file always yields the same
    ids, so a re-import overwrites rather than renames, and a cell that cites a
    pin still cites the same pin next week. Unnamed pins fall back to their
    position in the document, which is stable for the same file and nothing more
    — name your pins.
    """
    used: set = set()
    out: List[str] = []
    for i, pin in enumerate(pins):
        carried = pin.carried.get("pin_id")
        base = str(carried) if carried else (slugify(pin.name) or f"pin-{i + 1}")
        pid = base
        n = 1
        while pid in used:
            n += 1
            pid = f"{base}-{n}"
        used.add(pid)
        out.append(pid)
    return out


def _fail(message: str) -> NoReturn:
    """Refuse to continue, loudly.

    Used for every unrecognised enum value. A silent fallback here is how a typo
    in a spreadsheet turns forty `field_visit` pins into forty `unknown` ones,
    or worse, how a mistyped role gets quietly resolved into one that reaches
    the model.
    """
    raise SystemExit(f"error: {message}")


def _check_enum(value: str, allowed: Sequence[str], field_name: str, where: str) -> str:
    v = (value or "").strip().lower()
    if v not in allowed:
        _fail(
            f"{where}: {field_name}={value!r} is not one of {list(allowed)}. "
            "Refusing to guess — an unknown role or provenance is exactly the "
            "kind of thing that must not be resolved silently."
        )
    return v


def _parse_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    v = str(value or "").strip().lower()
    if v in TRUE_WORDS:
        return True
    if v in FALSE_WORDS:
        return False
    _fail(f"{where}: visited={value!r} is not a yes/no value")


def read_annotations(path: Path) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    """The annotations CSV, indexed by pin_id and by normalized name.

    A blank cell means "leave the imported value alone", not "clear it". That is
    what makes an unedited template a no-op and lets Matthew fill in ten rows out
    of forty without wiping the rest.
    """
    by_id: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            _fail(f"{path.name}: empty CSV")
        unknown = [
            c
            for c in reader.fieldnames
            if c and c.strip() not in TEMPLATE_COLUMNS
        ]
        if unknown:
            print(f"  note: ignoring unknown CSV column(s): {', '.join(unknown)}")
        for row in reader:
            clean = {
                (k or "").strip(): (v or "").strip()
                for k, v in row.items()
                if k and (v or "").strip()
            }
            if not clean:
                continue
            pid = clean.get("pin_id", "")
            nm = _norm_name(clean.get("name", ""))
            if pid:
                by_id[pid] = clean
            if nm:
                by_name[nm] = clean
    return by_id, by_name


def _norm_name(name: str) -> str:
    return " ".join((name or "").split()).lower()


def normalize(
    pins: Sequence[RawPin],
    defaults: Dict[str, Any],
    annotations: Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """RawPins → contract-schema property dicts, plus per-pin warnings.

    Precedence, weakest first: CLI defaults, values carried by the input file,
    then the annotations CSV. Anything unrecognised at any level is a hard error
    rather than a fallback — see `_check_enum`.
    """
    by_id, by_name = annotations
    used_rows: set = set()
    pin_ids = assign_pin_ids(pins)
    out: List[Dict[str, Any]] = []
    notes: List[str] = []

    for pin, pid in zip(pins, pin_ids):
        where = f"{pin.source_file}:{pin.name or pid}"

        row: Dict[str, str] = {}
        if pid in by_id:
            row = by_id[pid]
            used_rows.add(id(row))
        elif _norm_name(pin.name) in by_name:
            row = by_name[_norm_name(pin.name)]
            used_rows.add(id(row))

        def pick(key: str, default: Any) -> Any:
            if key in row:
                return row[key]
            if key in pin.carried:
                return pin.carried[key]
            return default

        role = _check_enum(str(pick("role", defaults["role"])), ROLES, "role", where)
        provenance = _check_enum(
            str(pick("provenance", defaults["provenance"])),
            PROVENANCES,
            "provenance",
            where,
        )
        position_confidence = _check_enum(
            str(pick("position_confidence", defaults["position_confidence"])),
            POSITION_CONFIDENCES,
            "position_confidence",
            where,
        )

        visited_default = defaults["visited"]
        if visited_default is None:
            # Not stated on the CLI: a field_visit pin was by definition visited,
            # anything else we do not know and must not assert.
            visited_default = provenance == FIELD_PROVENANCE
        visited = _parse_bool(pick("visited", visited_default), where)

        date_raw = pick("date", pin.date)
        date = _iso_date(str(date_raw), notes, where) if date_raw else None

        out.append(
            {
                "pin_id": pid,
                "name": pin.name,
                "role": role,
                "provenance": provenance,
                "visited": visited,
                # The description in the export is untyped user text, so it lands
                # in source_note. Promoting it to `observed` would assert it is a
                # field observation, which only Matthew can say — the template
                # carries it in a `source_description` column for copy-paste.
                "observed": str(pick("observed", "")),
                "position_confidence": position_confidence,
                "date": date,
                "source_note": str(pick("source_note", pin.description)),
                "folder": pin.folder,
                "symbol": pin.symbol,
                "source_file": pin.source_file,
                # Filled in by flag_new_sites().
                "nearest_db_km": None,
                "nearest_db_name": None,
                "potentially_new": False,
                "_lon": round(pin.lon, 7),
                "_lat": round(pin.lat, 7),
            }
        )

    unused = [
        r.get("name") or r.get("pin_id") or "?"
        for r in list(by_id.values()) + list(by_name.values())
        if id(r) not in used_rows
    ]
    for name in sorted(set(unused)):
        notes.append(f"annotation row {name!r} matched no imported pin")
    return out, notes


# --- "not in any database" flagging (§30.3) ---------------------------------


def load_occurrences(path: Path) -> List[Tuple[float, float, str]]:
    """(lon, lat, name) for every point in a wa_occurrences-shaped file."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: List[Tuple[float, float, str]] = []
    for feat in doc.get("features") or []:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        props = feat.get("properties") or {}
        try:
            out.append(
                (
                    float(coords[0]),
                    float(coords[1]),
                    str(props.get("name") or props.get("site_name") or "unnamed"),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def flag_new_sites(
    props: List[Dict[str, Any]], occurrences: Optional[List[Tuple[float, float, str]]]
) -> None:
    """Set nearest_db_km / nearest_db_name / potentially_new in place.

    When there is no occurrence extract on disk the distance fields stay null —
    "we did not look" and "there is nothing within 200 m" are different claims
    and must not share a representation. `potentially_new` then stays False,
    which is the non-claim; the run summary says loudly that it could not be
    evaluated, because a False that means "unknown" is the kind of thing that
    gets read as fact three months later.
    """
    if not occurrences:
        return
    for p in props:
        lon, lat = p["_lon"], p["_lat"]
        km, name = min(
            ((distance_km(lon, lat, olon, olat), oname) for olon, olat, oname in occurrences),
            key=lambda t: t[0],
        )
        # Compare on the unrounded distance; store rounded to the metre. A
        # threshold test against a rounded value flips at the boundary.
        p["nearest_db_km"] = round(km, 3)
        p["nearest_db_name"] = name
        p["potentially_new"] = km > NEW_SITE_THRESHOLD_KM and p["provenance"] == FIELD_PROVENANCE


# --- output ----------------------------------------------------------------


def build_feature_collection(
    props: List[Dict[str, Any]], source_files: List[str]
) -> Dict[str, Any]:
    counts_by_role = Counter(p["role"] for p in props)
    counts_by_prov = Counter(p["provenance"] for p in props)
    feats = []
    for p in props:
        lon, lat = p["_lon"], p["_lat"]
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {k: p[k] for k in PROPERTY_ORDER},
            }
        )
    return {
        "type": "FeatureCollection",
        # No `built_at`: a timestamp would make every re-import a diff and
        # destroy the one cheap check that the import is deterministic.
        "properties": {
            "source": "operator_field_pins",
            "source_files": source_files,
            "count": len(feats),
            "counts_by_role": {r: counts_by_role.get(r, 0) for r in ROLES},
            "counts_by_provenance": {p: counts_by_prov.get(p, 0) for p in PROVENANCES},
            "new_site_threshold_km": NEW_SITE_THRESHOLD_KM,
            "note": (
                "Normalized by scripts/import_field_pins.py. role=display is "
                "map-only; role=evidence reaches the agents; role=truth is "
                "benchmark ground truth and must never reach a model."
            ),
        },
        "features": feats,
    }


def write_template(path: Path, props: List[Dict[str, Any]], raw: Sequence[RawPin]) -> None:
    """The spreadsheet Matthew actually fills in.

    Annotation columns are left blank rather than pre-filled with the defaults,
    so handing the file back unedited changes nothing. `source_description`
    carries the text already in the export as a starting point for `observed`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TEMPLATE_COLUMNS)
        w.writeheader()
        for p, r in zip(props, raw):
            w.writerow(
                {
                    "pin_id": p["pin_id"],
                    "name": p["name"],
                    "folder": p["folder"],
                    "role": "",
                    "provenance": "",
                    "visited": "",
                    "observed": "",
                    "position_confidence": "",
                    "date": "",
                    "source_note": "",
                    "source_description": r.description,
                }
            )


def print_summary(
    props: List[Dict[str, Any]],
    skipped: Counter,
    notes: List[str],
    occurrences_path: Optional[Path],
    occurrence_count: Optional[int],
) -> None:
    by_role = Counter(p["role"] for p in props)
    by_prov = Counter(p["provenance"] for p in props)

    print(f"  pins imported: {len(props)}")
    if skipped:
        print(
            "  geometry not imported: "
            + ", ".join(f"{n}× {k}" for k, n in sorted(skipped.items()))
        )
    print(
        "  by role:       "
        + ", ".join(f"{r}={by_role.get(r, 0)}" for r in ROLES)
    )
    print(
        "  by provenance: "
        + ", ".join(f"{p}={by_prov.get(p, 0)}" for p in PROVENANCES if by_prov.get(p))
    )

    if occurrence_count is None:
        field_pins = by_prov.get(FIELD_PROVENANCE, 0)
        print(
            f"  NOT EVALUATED: no occurrence extract at {occurrences_path} — "
            f"nearest_db_km/nearest_db_name are null and potentially_new could not "
            f"be judged for {field_pins} field_visit pin(s). Build the extract and "
            f"re-import; do not read potentially_new=false as 'already recorded'."
        )
    else:
        new = [p for p in props if p["potentially_new"]]
        print(
            f"  potentially new: {len(new)} field_visit pin(s) further than "
            f"{NEW_SITE_THRESHOLD_KM * 1000:.0f} m from any of "
            f"{occurrence_count} recorded occurrences"
        )
        for p in new:
            print(
                f"    + {p['pin_id']}  {p['name']!r}  "
                f"nearest {p['nearest_db_name']!r} at {p['nearest_db_km']} km"
            )

    promoted = [p for p in props if p["role"] != "display"]
    truth = [p for p in promoted if p["role"] == "truth"]
    evidence = [p for p in promoted if p["role"] == "evidence"]
    if evidence:
        print(
            f"  WARNING: {len(evidence)} pin(s) promoted to role=evidence — these "
            "WILL be put in agent prompts and must be excluded from benchmark "
            "ground truth:"
        )
        for p in evidence:
            print(f"    ! {p['pin_id']}  {p['name']!r}  provenance={p['provenance']}")
        non_field = [p for p in evidence if p["provenance"] != FIELD_PROVENANCE]
        if non_field:
            print(
                f"  WARNING: {len(non_field)} of those are not provenance=field_visit. "
                "Only 'I stood here' is defensible as evidence (§30.1); "
                "literature/inference/hearsay pins fed to the model make it agree "
                "with you and teach you nothing."
            )
    if truth:
        print(
            f"  WARNING: {len(truth)} pin(s) promoted to role=truth — benchmark "
            "ground truth. These must never appear in an agent prompt:"
        )
        for p in truth:
            print(f"    ! {p['pin_id']}  {p['name']!r}")
    if not promoted:
        print("  all pins are role=display (map only) — nothing reaches the model")

    for n in notes:
        print(f"  note: {n}")


# --- CLI -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("inputs", nargs="+", type=Path, help=".kml, .kmz, .gpx or .geojson")
    p.add_argument(
        "--name",
        help="output basename under data/user_sites/ (default: slug of the first input)",
    )
    p.add_argument("--out", type=Path, help="explicit output path, overrides --name")
    p.add_argument(
        "--role",
        default=DEFAULT_ROLE,
        choices=ROLES,
        help="default role for every pin (default: %(default)s — always safe)",
    )
    p.add_argument(
        "--provenance",
        default=DEFAULT_PROVENANCE,
        choices=PROVENANCES,
        help="default provenance (default: %(default)s)",
    )
    p.add_argument(
        "--position-confidence",
        default=DEFAULT_POSITION_CONFIDENCE,
        choices=POSITION_CONFIDENCES,
        help="default positional confidence (default: %(default)s)",
    )
    p.add_argument(
        "--visited",
        choices=["yes", "no"],
        help="default `visited` (default: yes for field_visit pins, no otherwise)",
    )
    p.add_argument(
        "--annotations",
        type=Path,
        help="CSV of per-pin values, keyed on pin_id or name; blank cells keep the default",
    )
    p.add_argument(
        "--emit-template",
        type=Path,
        metavar="CSV",
        help="write a blank annotations CSV listing every imported pin",
    )
    p.add_argument(
        "--occurrences",
        type=Path,
        default=OCCURRENCES_PATH,
        help="occurrence extract used for the 'not in any database' check",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    raw: List[RawPin] = []
    skipped: Counter = Counter()
    notes: List[str] = []
    for path in args.inputs:
        if not path.exists():
            _fail(f"{path} does not exist")
        pins, s, w = parse_input(path)
        print(f"{path.name}: {len(pins)} point(s)")
        raw.extend(pins)
        skipped.update(s)
        notes.extend(w)

    if not raw:
        _fail("no point features found in any input — nothing to write")

    annotations: Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]] = ({}, {})
    if args.annotations:
        if not args.annotations.exists():
            _fail(f"{args.annotations} does not exist")
        annotations = read_annotations(args.annotations)

    defaults = {
        "role": args.role,
        "provenance": args.provenance,
        "position_confidence": args.position_confidence,
        "visited": None if args.visited is None else args.visited == "yes",
    }
    props, norm_notes = normalize(raw, defaults, annotations)
    notes.extend(norm_notes)

    occurrences = None
    occurrence_count = None
    if args.occurrences and Path(args.occurrences).exists():
        occurrences = load_occurrences(Path(args.occurrences))
        occurrence_count = len(occurrences)
        flag_new_sites(props, occurrences)

    if args.emit_template:
        write_template(args.emit_template, props, raw)
        print(f"  template: {args.emit_template} ({len(props)} rows)")

    out_path = args.out
    if out_path is None:
        name = args.name or slugify(args.inputs[0].stem) or "field_pins"
        out_path = USER_SITES_DIR / f"{name}.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fc = build_feature_collection(props, [p.name for p in args.inputs])
    out_path.write_text(
        json.dumps(fc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print_summary(props, skipped, notes, Path(args.occurrences) if args.occurrences else None, occurrence_count)
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
