"""
Static reference overlays for the map.

Everything here reads files on disk — a GeoJSON extract or the GNIS TSV — so it
works identically under `DEV_MODE=true` and `false`. That matters: `/features`
404s in dev mode, and these are the layers that turn "a colored square" into "a
place I could go".

Datasets are built once by scripts (see `scripts/build_gnis_extract.py`) rather
than queried live: no runtime dependency on a federal service, no rate limits,
and — the reason that matters for Workstream A — deterministic across runs.
"""
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reference", tags=["reference"])

REFERENCE_DIR = DATA_DIR / "reference"

#: Tier → colour hint for the map legend. Kept server-side so the lexicon and
#: the legend cannot drift apart.
TIER_COLORS = {
    1: "#dc2626",  # direct workings
    2: "#ea580c",  # mining culture
    3: "#ca8a04",  # ore and gangue
    4: "#65a30d",  # alteration / gossan
}


@router.get("/layers")
async def available_layers() -> Dict[str, Any]:
    """Which reference overlays this install actually has on disk.

    The frontend hides a layer's toggle when it is missing rather than offering
    a control that yields an empty map.
    """
    from app.toponyms.matcher import GNIS_PATH, load_lexicon

    lexicon = load_lexicon()
    return {
        "wilderness": (REFERENCE_DIR / "wa_wilderness.geojson").exists(),
        "occurrences": (REFERENCE_DIR / "wa_occurrences.geojson").exists(),
        "toponyms": GNIS_PATH.exists() and lexicon is not None,
        "lexicon_version": lexicon.version if lexicon else None,
    }


@router.get("/wilderness")
async def wilderness():
    """Congressionally designated wilderness in Washington (USFS EDW).

    **Advisory only.** Designated wilderness is generally withdrawn from mineral
    entry and a lot of the highest-prospectivity ground in scope sits inside the
    Alpine Lakes and Henry M. Jackson areas — so a 0.9 cell inside a boundary is
    geologically interesting and practically inaccessible, and the map should
    say so at a glance. Boundaries shift and valid existing rights are
    complicated; this is a display convenience, not a determination that ground
    is open to location.
    """
    path = REFERENCE_DIR / "wa_wilderness.geojson"
    if not path.exists():
        raise HTTPException(404, "wa_wilderness.geojson not built")
    return FileResponse(path, media_type="application/geo+json")


@router.get("/occurrences")
async def occurrences():
    """Known mineral occurrences (MRDS / WA DNR extract), if built."""
    path = REFERENCE_DIR / "wa_occurrences.geojson"
    if not path.exists():
        raise HTTPException(
            404,
            "wa_occurrences.geojson not built — run scripts/build_occurrences.py",
        )
    return FileResponse(path, media_type="application/geo+json")


@lru_cache(maxsize=1)
def _toponym_features() -> List[Dict[str, Any]]:
    """All lexicon-matching WA place names as GeoJSON point features.

    Built once per process — the GNIS extract is static, and the matcher is
    deterministic by design.
    """
    from app.toponyms.matcher import load_gnis, load_lexicon, match_names

    lexicon = load_lexicon()
    names = load_gnis()
    if lexicon is None or not names:
        return []

    anti = lexicon.anti_signal_tier
    feats: List[Dict[str, Any]] = []
    for n, tier, term in match_names(names, lexicon):
        if tier == anti:
            continue  # suppressed names are logged, not drawn
        feats.append({
            "type": "Feature",
            "id": n.feature_id,
            "geometry": {"type": "Point", "coordinates": [n.lon, n.lat]},
            "properties": {
                "name": n.name,
                "feature_class": n.feature_class,
                "tier": tier,
                "tier_name": lexicon.tiers[tier].get("name", str(tier)),
                "matched_term": term,
                "color": TIER_COLORS.get(tier, "#6b7280"),
                "map_name": n.map_name,
                # Streams are located at their mouth; carrying the headwaters
                # lets the map draw the creek rather than a misleading dot.
                "source_lat": n.source_lat,
                "source_lon": n.source_lon,
            },
        })
    logger.info("Toponym overlay: %d matching names", len(feats))
    return feats


@router.get("/toponyms")
async def toponyms(
    bbox: Optional[str] = None,
    max_tier: int = 4,
) -> Dict[str, Any]:
    """Mining-flavoured place names, for drawing while an AOI is being sketched.

    Visible *before* any analysis runs — seeing "Bonanza Creek" and "Miners
    Ridge" on the map at draw time is useful independent of any scoring, and is
    by far the cheapest deliverable in this workstream.
    """
    feats = _toponym_features()
    feats = [f for f in feats if f["properties"]["tier"] <= max_tier]

    if bbox:
        try:
            west, south, east, north = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be west,south,east,north")
        feats = [
            f
            for f in feats
            if west <= f["geometry"]["coordinates"][0] <= east
            and south <= f["geometry"]["coordinates"][1] <= north
        ]

    from app.toponyms.matcher import GNIS_CITATION, load_lexicon

    lexicon = load_lexicon()
    return {
        "type": "FeatureCollection",
        "properties": {
            "source": GNIS_CITATION,
            "lexicon_version": lexicon.version if lexicon else None,
            "note": (
                "Deterministic regex match against a curated lexicon. A "
                "toponym is corroborative evidence, never primary — see "
                "knowledge/toponyms/gold_wa.yaml."
            ),
        },
        "features": feats,
    }
