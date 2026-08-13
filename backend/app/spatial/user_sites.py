"""
Matthew's own field pins, and the role rule that keeps them out of the model.

Imported pins (`scripts/import_field_pins.py` → `data/user_sites/*.geojson`)
carry an explicit ``role``, and the whole point of this module is that the role
is enforced structurally rather than remembered:

* ``display``  — draw on the map, nothing else. The default, and always safe.
* ``truth``    — benchmark ground truth. Safe **only** while the model never
                 sees it.
* ``evidence`` — fed to the agents as spatial context. Safe **only** while it is
                 excluded from the benchmark.

A pin cannot be both. If an agent is told "Matthew marked this spot" and the
benchmark then asks "did the model rank Matthew's spot highly", the answer is
yes by construction and measures nothing (steps-2.0 §30, §30.1; the same failure
mode as toponyms in 1.0 §23).

Two consequences you need to know before calling anything here:

1. **``load_user_sites()`` with no arguments returns evidence pins only.** A
   caller that forgets to filter gets the safe answer, not every pin. Asking for
   ``truth`` is possible but you have to name it, and the only legitimate callers
   are the benchmark and the map.
2. **An unrecognised role is downgraded to ``display``**, not rejected. A typo in
   a hand-edited file must not silently leak a pin into a prompt.

Synchronous on purpose. The house style is async for I/O, but these are a
handful of small local files read once per run, and the closest existing
analogue — ``app.toponyms.matcher`` — is synchronous for the same reason. There
is also no cache: pins change whenever Matthew re-imports, and a
process-lifetime cache would serve a stale "My Sites" layer until restart.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

USER_SITES_DIR = DATA_DIR / "user_sites"

#: The three roles, in the order they are reported. Mutually exclusive per pin.
ROLES: Tuple[str, ...] = ("display", "truth", "evidence")
ALL_ROLES: Tuple[str, ...] = ROLES

#: The only role an agent prompt may ever contain. `build_local_context` filters
#: to this; so does `load_user_sites()` when called with no arguments.
MODEL_VISIBLE_ROLES: Tuple[str, ...] = ("evidence",)

DEFAULT_ROLE = "display"

PROVENANCES: Tuple[str, ...] = (
    "field_visit",
    "literature",
    "inference",
    "hearsay",
    "unknown",
)
DEFAULT_PROVENANCE = "unknown"

#: Only `field_visit` pins are defensible as evidence — they are observations
#: that exist in no database, rather than something read off a map (§30.1).
FIELD_PROVENANCE = "field_visit"

POSITION_CONFIDENCES: Tuple[str, ...] = ("gps", "map_estimate", "rough")
#: Weakest claim wins by default: a KML/GPX file does not record how the pin was
#: placed, so anything stronger than "rough" would be an invention.
DEFAULT_POSITION_CONFIDENCE = "rough"

#: Citation form for `data_sources_used` when a pin contributes to an evidence
#: string. Deliberately not a public dataset name — it is one person's field
#: notebook and should read that way in the output.
USER_SITES_CITATION = "Operator_field_observations"


def _coerce_enum(
    value: Any,
    allowed: Tuple[str, ...],
    default: str,
    field: str,
    where: str,
) -> str:
    """Coerce one enum field, logging anything unrecognised.

    Never raises: a malformed pin must cost us that pin, not the run. The
    *import script* is where an unknown value is a hard error — by the time a
    file is on disk the only safe move at runtime is to degrade.
    """
    if isinstance(value, str):
        v = value.strip().lower()
        if v in allowed:
            return v
        if v:
            logger.warning(
                "user_sites: %s has unrecognised %s=%r — treating as %r",
                where,
                field,
                value,
                default,
            )
    elif value is not None:
        logger.warning(
            "user_sites: %s has non-string %s=%r — treating as %r",
            where,
            field,
            value,
            default,
        )
    return default


def _normalize_feature(
    feat: Any, source_file: str, index: int
) -> Optional[Dict[str, Any]]:
    """One GeoJSON Feature → a normalized pin dict, or None if unusable.

    Returned keys are the `data/user_sites` schema plus `lon`/`lat`, matching the
    ``{"lon": ..., "lat": ..., "name": ...}`` convention the toponym matcher
    already uses for occurrences.
    """
    where = f"{source_file}#{index}"
    if not isinstance(feat, dict):
        logger.warning("user_sites: %s is not an object — skipped", where)
        return None

    geom = feat.get("geometry") or {}
    props = feat.get("properties")
    if not isinstance(props, dict):
        props = {}

    if not isinstance(geom, dict) or geom.get("type") != "Point":
        logger.warning(
            "user_sites: %s is %r, not Point — skipped",
            where,
            (geom or {}).get("type") if isinstance(geom, dict) else type(geom).__name__,
        )
        return None

    coords = geom.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        logger.warning("user_sites: %s has no usable coordinates — skipped", where)
        return None
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        logger.warning("user_sites: %s coordinates are not numeric — skipped", where)
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        logger.warning(
            "user_sites: %s coordinates out of range (%s, %s) — skipped",
            where,
            lon,
            lat,
        )
        return None

    pin_id = props.get("pin_id")
    if not isinstance(pin_id, str) or not pin_id.strip():
        pin_id = f"{Path(source_file).stem}-{index}"
        logger.warning("user_sites: %s has no pin_id — using %r", where, pin_id)

    name = props.get("name")
    name = name.strip() if isinstance(name, str) else ""

    nearest_km = props.get("nearest_db_km")
    try:
        nearest_km = None if nearest_km is None else float(nearest_km)
    except (TypeError, ValueError):
        nearest_km = None

    nearest_name = props.get("nearest_db_name")
    if not isinstance(nearest_name, str) or not nearest_name.strip():
        nearest_name = None

    date = props.get("date")
    if not isinstance(date, str) or not date.strip():
        date = None

    return {
        "pin_id": pin_id.strip(),
        "name": name,
        "role": _coerce_enum(props.get("role"), ROLES, DEFAULT_ROLE, "role", where),
        "provenance": _coerce_enum(
            props.get("provenance"),
            PROVENANCES,
            DEFAULT_PROVENANCE,
            "provenance",
            where,
        ),
        "visited": bool(props.get("visited")),
        "observed": str(props.get("observed") or ""),
        "position_confidence": _coerce_enum(
            props.get("position_confidence"),
            POSITION_CONFIDENCES,
            DEFAULT_POSITION_CONFIDENCE,
            "position_confidence",
            where,
        ),
        "date": date,
        "source_note": str(props.get("source_note") or ""),
        # `folder` and `symbol` are additive to the CONTRACT.md schema. They are
        # the source format's own categorisation — the KML layer name Matthew
        # organised his map with, and the GPX waypoint icon he chose — and they
        # are the only bulk-annotation handles an export gives us. Empty string
        # when the format has no such concept, which is why they are also the
        # two fields that cannot be identical between a KML and a GPX export of
        # the same points.
        "folder": str(props.get("folder") or ""),
        "symbol": str(props.get("symbol") or ""),
        "source_file": str(props.get("source_file") or source_file),
        "nearest_db_km": nearest_km,
        "nearest_db_name": nearest_name,
        "potentially_new": bool(props.get("potentially_new")),
        "lon": lon,
        "lat": lat,
    }


def _iter_files() -> List[Path]:
    """Every user-site file, in a stable order (runs must be reproducible)."""
    if not USER_SITES_DIR.exists():
        return []
    return sorted(USER_SITES_DIR.glob("*.geojson"))


def _load_all() -> List[Dict[str, Any]]:
    """Every pin on disk, normalized, unfiltered. Never raises."""
    pins: List[Dict[str, Any]] = []
    for path in _iter_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A hand-edited file with a trailing comma must not take down a run.
            logger.warning("user_sites: could not read %s (%s) — skipped", path, exc)
            continue

        if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
            feats = doc.get("features")
        elif isinstance(doc, dict) and doc.get("type") == "Feature":
            feats = [doc]
        elif isinstance(doc, list):
            feats = doc
        else:
            logger.warning(
                "user_sites: %s is not a FeatureCollection — skipped", path.name
            )
            continue

        if not isinstance(feats, list):
            logger.warning("user_sites: %s has no feature list — skipped", path.name)
            continue

        kept = 0
        for i, feat in enumerate(feats):
            pin = _normalize_feature(feat, path.name, i)
            if pin is not None:
                pins.append(pin)
                kept += 1
        logger.info("user_sites: %s → %d/%d pins", path.name, kept, len(feats))
    return pins


def load_user_sites(roles: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Normalized user field pins, filtered by role.

    **``roles=None`` (the default) means evidence pins only** — not "all pins".
    That asymmetry is the invariant this module exists to enforce: a
    ``role: "truth"`` pin is benchmark ground truth, and showing it to a model
    makes the benchmark tautological (steps-2.0 §30, CONTRACT invariant 1). A
    caller that forgets to filter therefore gets the safe answer rather than the
    complete one.

    To get everything — legitimate for the map overlay and for the benchmark,
    nowhere else — pass ``roles=ALL_ROLES`` explicitly, which makes the decision
    visible at the call site and in review.

    Unrecognised role names in ``roles`` are dropped with a warning; a pin whose
    own role is unrecognised is treated as ``display``, so it can never satisfy
    an ``evidence`` request.
    """
    if roles is None:
        wanted = set(MODEL_VISIBLE_ROLES)
    else:
        wanted = {str(r).strip().lower() for r in roles}
        unknown = wanted - set(ROLES)
        if unknown:
            logger.warning(
                "user_sites: ignoring unknown requested role(s) %s — known roles are %s",
                sorted(unknown),
                list(ROLES),
            )
            wanted -= unknown

    if not wanted:
        return []
    return [p for p in _load_all() if p["role"] in wanted]


def role_counts() -> Dict[str, int]:
    """Pin count per role, for the run record's ``roles_active``.

    All three roles are always present, zero included: the run record should say
    "truth: 0" rather than leave a reader wondering whether the key was omitted
    because there were none or because nothing checked.
    """
    counts = {role: 0 for role in ROLES}
    for pin in _load_all():
        counts[pin["role"]] = counts.get(pin["role"], 0) + 1
    return counts


def sites_geojson() -> Dict[str, Any]:
    """All pins as a FeatureCollection for the "My Sites" map overlay.

    Every role is included — **the map is not the model.** Seeing your own pins
    while drawing an AOI is the whole point, and a ``truth`` pin drawn on screen
    tells the benchmark nothing. Each feature carries ``role`` so the UI can
    style it, which is also how a reviewer spots a pin that has been promoted.
    """
    pins = load_user_sites(roles=ALL_ROLES)
    counts = {role: 0 for role in ROLES}
    for p in pins:
        counts[p["role"]] = counts.get(p["role"], 0) + 1

    feats: List[Dict[str, Any]] = []
    for p in pins:
        props = {k: v for k, v in p.items() if k not in ("lon", "lat")}
        feats.append(
            {
                "type": "Feature",
                # pin_id is only unique within its file; the map needs a key
                # that survives two imports with the same pin name.
                "id": f"{p['source_file']}:{p['pin_id']}",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": props,
            }
        )

    return {
        "type": "FeatureCollection",
        "properties": {
            "source": USER_SITES_CITATION,
            "count": len(feats),
            "counts_by_role": counts,
            "files": [p.name for p in _iter_files()],
            "note": (
                "Operator-supplied field pins. role=display is map-only; "
                "role=evidence is fed to the agents; role=truth is benchmark "
                "ground truth and is never shown to a model."
            ),
        },
        "features": feats,
    }
