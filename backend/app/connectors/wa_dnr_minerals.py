"""
WA DNR / Washington Geological Survey "Mines and Minerals" ArcGIS REST connector.

    ############################################################################
    #  THIS CONNECTOR IS A REFRESH PATH, NOT THE ANALYSIS PATH.                #
    #                                                                          #
    #  The canonical source of WA DNR occurrence data for an analysis run is    #
    #  the static extract  data/reference/wa_occurrences.geojson,  built        #
    #  offline by  scripts/build_reference_extracts.py  from the local          #
    #  geodatabase in data/raw/. Nothing on the request path may call this      #
    #  connector.                                                              #
    #                                                                          #
    #  Why: §28.3 of "steps for raghav 2.0.md" records that the production      #
    #  host gis.dnr.wa.gov was slow during spec verification — one request      #
    #  timed out at three minutes. It was fast on 2026-08-12 (every request     #
    #  measured that day landed in 0.13–0.55 s), but "fast today" is not        #
    #  an availability guarantee, and an analysis run that silently degrades    #
    #  because a third-party WFS blinked is exactly the failure mode this       #
    #  repo already has too much of (CLAUDE.md → Known Gaps #2).               #
    #                                                                          #
    #  Use it for: rebuilding the static extract, and checking the extract      #
    #  against upstream for drift. It has a generous timeout and retries        #
    #  precisely because it runs offline where a 3-minute wait is acceptable.   #
    ############################################################################

Service (production host — see LAYER_* constants for the verification note):
    https://gis.dnr.wa.gov/site1/rest/services/Public_Geology/Mines_and_Minerals/MapServer

Why this beats MRDS for this project (§28.2): the attributes are WA-authored and
machine-readable where MRDS asks the model to infer. `ASSAYS` and `PRODUCTION` are
exactly the assay-primacy distinction that knowledge/historical/gold.md currently
asks the LLM to guess at, and `LOCATION_ACCURACY` replaces a blanket positional
caveat with a per-site one.

Attribute fields on layers 12/13 (verified live 2026-08-12):
    SITE_ID, SITE_NAME, ALTERNATE_NAMES, PRIMARY_COMMODITY, COMMODITIES,
    ORE_MINERALS, GANGUE, LOCATION_DESCRIPTION, LEGAL_DESCRIPTION,
    LOCATION_ACCURACY, COUNTY, LATITUDE, LONGITUDE, MINING_DISTRICT,
    LOCATION_SOURCE, ASSAYS, PRODUCTION, COMMENTS   (+ OBJECTID)

**Most of that has nowhere to live in the Feature ORM.** `models/feature.py` has no
column for ASSAYS, PRODUCTION, MINING_DISTRICT, ORE_MINERALS, GANGUE,
LEGAL_DESCRIPTION, LOCATION_DESCRIPTION, ALTERNATE_NAMES or COMMENTS, and adding
columns needs an Alembic migration this connector cannot ship on its own. So
`normalize()` folds LOCATION_ACCURACY into `source_quality` and PRODUCTION into
`status` (both documented below) and **drops the rest** — see the docstring on
`normalize()` for the exact list. That loss is another reason the static extract,
which keeps every field, is the analysis path: `wa_occurrences.geojson` carries
`assays`, `production` and `accuracy_class` through to the agents' spatial context.
"""
import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature

logger = logging.getLogger(__name__)

BASE_URL = (
    "https://gis.dnr.wa.gov/site1/rest/services/Public_Geology/"
    "Mines_and_Minerals/MapServer"
)

# ---------------------------------------------------------------------------
# Layer ids.
#
# VERIFIED 2026-08-12 against .../MapServer/layers?f=json on the PRODUCTION host
# gis.dnr.wa.gov (service currentVersion 11.5). §28.3 warns that a gis-dev.dnr.wa.gov
# host also answers and may number its layers differently — never take ids from there,
# and re-run the layers?f=json check before trusting these if the service is
# republished. Group layers (0 Coal, 7 IAML, 10 Minerals, 14 Hazardous Minerals,
# 23 Historical Mining Districts) hold no features and are deliberately absent.
#
# Counts are live returnCountOnly values measured the same day, with the count in
# the local geodatabase (see CONTRACT.md) beside them where they differ.
# ---------------------------------------------------------------------------
LAYER_GOLD_SILVER = 12          # "Gold and Silver Locations"    point,   1467 (gdb 1467)
LAYER_METALLIC = 13             # "Metallic Mineral Locations"   point,   1847 (gdb 1847)
LAYER_IAML_SITES = 8            # "IAML Sites"                   point,     98 (gdb 97)
LAYER_IAML_FEATURES = 9         # "IAML Features"                point,    364 (gdb 359)
LAYER_MINING_DISTRICTS = 22     # "Mining Districts"             polygon,   68 (gdb 68)
# Related table, one-to-many from layer 12/13 on SITE_ID (relationship id 8):
TABLE_METALLIC_DOCUMENTS = 73   # "Metallic Minerals Documents"  table, 107739 (gdb 107739)

#: Layers fetched by ``fetch()`` when no explicit selection is given. Both point
#: layers, because a site can legitimately appear in each and the extract dedupes
#: on ``(source_layer, site_id)`` rather than on SITE_ID alone (CONTRACT.md).
DEFAULT_LAYER_IDS: Tuple[int, ...] = (LAYER_GOLD_SILVER, LAYER_METALLIC)

#: Service layer names, for logging and for stamping raw records.
SERVICE_LAYER_NAMES: Dict[int, str] = {
    LAYER_GOLD_SILVER: "Gold and Silver Locations",
    LAYER_METALLIC: "Metallic Mineral Locations",
    LAYER_IAML_SITES: "IAML Sites",
    LAYER_IAML_FEATURES: "IAML Features",
    LAYER_MINING_DISTRICTS: "Mining Districts",
}

#: Geodatabase layer names, so a record fetched live and the same record read out of
#: data/raw/ carry an identical ``source_layer`` and the extract builder does not have
#: to care which door the record came through.
GDB_LAYER_NAMES: Dict[int, str] = {
    LAYER_GOLD_SILVER: "Gold_Silver_Locations",
    LAYER_METALLIC: "Metallic_Mineral_Locations",
    LAYER_IAML_SITES: "IAML_Sites",
    LAYER_IAML_FEATURES: "IAML_Features",
    LAYER_MINING_DISTRICTS: "Mining_Distircts_WA",  # the typo is real, see CONTRACT.md
}

#: ``uid`` prefixes from CONTRACT.md — "gs-181" / "mm-1899".
UID_PREFIXES: Dict[int, str] = {
    LAYER_GOLD_SILVER: "gs",
    LAYER_METALLIC: "mm",
    LAYER_IAML_SITES: "iaml",
    LAYER_IAML_FEATURES: "iamlf",
    LAYER_MINING_DISTRICTS: "dist",
}

# Service maxRecordCount, read off .../MapServer?f=json on 2026-08-12. Requesting more
# than this per page is silently clamped by the server, which is how you get an
# undetected truncation, so PAGE_SIZE stays under it.
SERVICE_MAX_RECORD_COUNT = 2000
PAGE_SIZE = 1000

# 200 pages x 1000 rows = 200k. The largest layer on this service holds 1847 features
# and the largest related table 107,739 rows, so hitting this cap means a paging bug
# (e.g. a server ignoring resultOffset and returning page 1 forever), not a big layer.
MAX_PAGES = 200

# §28.3: the production host timed out at three minutes once during spec verification.
# BaseConnector._get hardcodes a 30 s timeout with no retry, which is right for the
# request path and wrong here, so this module drives httpx itself.
REQUEST_TIMEOUT_S = 180.0
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 2.0

#: LOCATION_ACCURACY -> accuracy class. The eight strings are the complete measured set
#: from the geodatabase (CONTRACT.md); anything else is "unknown" rather than an error,
#: because a new upstream string must not break a refresh.
ACCURACY_CLASSES: Dict[str, str] = {
    "GPS coordinates": "survey",
    "located from orthophoto": "survey",
    "USGS 7.5-minute topographic map": "topo",
    "generally from USGS 7.5-minute topographic map": "topo",
    "coordinates estimated from location description": "derived",
    "coordinates estimated from legal description": "derived",
    "coordinate accuracy highly variable": "variable",
    "mining district centroid": "district_centroid",
}

#: accuracy class -> Feature.source_quality. This is the one place LOCATION_ACCURACY
#: survives the ORM, and source_quality is the column meant for exactly this ("quality
#: score based on source reliability"). The ordering is the point, not the absolute
#: numbers: a GPS fix outranks a topo read outranks a coordinate derived from a legal
#: description. `district_centroid` is scored lowest on purpose — it is a district
#: centre wearing a site's clothes, and CONTRACT.md forbids it as benchmark truth.
ACCURACY_QUALITY: Dict[str, float] = {
    "survey": 0.95,
    "topo": 0.85,
    "derived": 0.60,
    "variable": 0.45,
    "district_centroid": 0.20,
    "unknown": 0.50,
}


def accuracy_class_for(location_accuracy: Optional[str]) -> str:
    """Map a raw LOCATION_ACCURACY string to its accuracy class.

    Returns "unknown" for None, "" and any string not in the measured set.
    """
    if not location_accuracy:
        return "unknown"
    return ACCURACY_CLASSES.get(location_accuracy.strip(), "unknown")


def _yes(value: Optional[str]) -> bool:
    """ASSAYS / PRODUCTION are the strings 'yes' or '' (measured, CONTRACT.md)."""
    return bool(value) and value.strip().lower() in ("yes", "y", "true")


def _split_commodities(commodities: Optional[str]) -> Optional[List[str]]:
    """COMMODITIES is a free-text comma list, e.g. "Gold, silver, copper, iron"."""
    if not commodities:
        return None
    parts = [p.strip() for p in commodities.split(",")]
    parts = [p for p in parts if p]
    return parts or None


class ArcGISServiceError(RuntimeError):
    """The service answered HTTP 200 with an ArcGIS error payload.

    ArcGIS REST reports many failures as ``{"error": {"code": 400, ...}}`` under a 200,
    so ``raise_for_status()`` alone will happily hand you an error document and call it
    data. (The same trap bites usgs_mrds.py, which got a WFS ServiceExceptionReport
    under a 200 for years — see that module's header.)
    """


class WADNRMineralsConnector(BaseConnector):
    """WA DNR Mines and Minerals connector. See the module docstring: refresh path only."""

    BASE_URL = BASE_URL

    def __init__(self, channel_config, layer_ids: Optional[Sequence[int]] = None):
        # layer_ids is optional so that CONNECTOR_REGISTRY's `connector_cls(channel)`
        # call in pipeline/ingest.py keeps working unchanged, while an offline refresh
        # script can ask for, say, just the mining-district polygons.
        super().__init__(channel_config)
        self.layer_ids: Tuple[int, ...] = tuple(layer_ids or DEFAULT_LAYER_IDS)
        # Channel.endpoint is allowed to override the hardcoded host so a redeployed
        # service can be pointed at without a code change.
        self.base_url = (self.endpoint or BASE_URL).rstrip("/")

    # ------------------------------------------------------------------ HTTP

    @staticmethod
    def _bbox_params(bbox: Optional[tuple]) -> Dict[str, Any]:
        """Envelope filter params. Native SRS is EPSG:2927, so inSR must be stated."""
        if not bbox:
            return {}
        min_lon, min_lat, max_lon, max_lat = bbox
        return {
            "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        }

    async def _query(self, layer_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """One /query call, with a generous timeout, retries and honest error surfacing.

        Retries on transport errors, timeouts and 5xx. Does not retry 4xx — a bad
        parameter will be just as bad the second time.
        """
        url = f"{self.base_url}/{layer_id}/query"
        last_exc: Optional[Exception] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                    response = await client.get(url, params=params)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"{response.status_code} from {url}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and "error" in payload:
                    err = payload["error"]
                    raise ArcGISServiceError(
                        f"layer {layer_id}: {err.get('code')} {err.get('message')} "
                        f"{err.get('details')}"
                    )
                return payload
            except httpx.HTTPStatusError as exc:
                # A 4xx means the request itself is wrong; it will be just as wrong on
                # the next attempt, so surface it immediately instead of burning 14 s of
                # backoff. Only 5xx (raised above) falls through to the retry.
                if exc.response is not None and exc.response.status_code < 500:
                    raise
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc

            # ArcGISServiceError is deliberately NOT caught here: the service answered,
            # it just said no, and fetch_layer() needs to see that to decide whether to
            # fall back from f=geojson to f=json.
            if attempt == MAX_ATTEMPTS:
                break
            delay = BACKOFF_BASE_S * (2 ** (attempt - 1))
            logger.warning(
                "WA DNR layer %s query attempt %s/%s failed (%s); retrying in %.0fs",
                layer_id, attempt, MAX_ATTEMPTS, last_exc, delay,
            )
            await asyncio.sleep(delay)

        raise RuntimeError(
            f"WA DNR layer {layer_id} query failed after {MAX_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------ counts

    async def count(self, layer_id: int, bbox: Optional[tuple] = None) -> int:
        """returnCountOnly=true. Used to prove pagination returned everything."""
        params = {"where": "1=1", "returnCountOnly": "true", "f": "json"}
        params.update(self._bbox_params(bbox))
        payload = await self._query(layer_id, params)
        return int(payload.get("count", 0))

    # ------------------------------------------------------------------ fetch

    async def fetch_layer(
        self,
        layer_id: int,
        bbox: Optional[tuple] = None,
        page_size: int = PAGE_SIZE,
        verify_count: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch every feature of one layer as GeoJSON-shaped dicts.

        Paginates with resultOffset/resultRecordCount and stops when the service says
        exceededTransferLimit is absent/false or the page came back short. Each record
        is stamped with ``_layer_id`` / ``_layer_name`` / ``source_layer`` / ``uid`` so
        a caller merging two layers can still tell them apart.

        With ``verify_count`` the total is cross-checked against returnCountOnly and a
        mismatch is logged at ERROR. That is the §32 acceptance criterion ("every point
        in a bounding box is returned") wired in as a self-check rather than left to a
        human to remember.
        """
        expected: Optional[int] = None
        if verify_count:
            expected = await self.count(layer_id, bbox=bbox)
            logger.info(
                "WA DNR layer %s (%s): %s features expected%s",
                layer_id, SERVICE_LAYER_NAMES.get(layer_id, "?"), expected,
                " in bbox" if bbox else " statewide",
            )

        records: List[Dict[str, Any]] = []
        offset = 0
        pages = 0
        use_geojson = True

        while pages < MAX_PAGES:
            params = {
                "where": "1=1",
                "outFields": "*",
                "outSR": 4326,
                "returnGeometry": "true",
                # OBJECTID ordering makes the page window deterministic. The layer
                # metadata reports objectIdField=null (an ArcGIS quirk), but
                # returnIdsOnly reports objectIdFieldName=OBJECTID, and the field is
                # present on every record — verified 2026-08-12.
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "geojson" if use_geojson else "json",
            }
            params.update(self._bbox_params(bbox))

            try:
                payload = await self._query(layer_id, params)
            except (ArcGISServiceError, httpx.HTTPStatusError):
                if not use_geojson:
                    raise
                # f=geojson is advertised in supportedQueryFormats and worked on
                # 2026-08-12, but an older/reconfigured deployment may only do f=json —
                # fall back rather than fail the refresh. Both error shapes are caught
                # because ArcGIS reports an unsupported format either as a 200 with an
                # error payload or as a 4xx, depending on version.
                logger.warning(
                    "WA DNR layer %s rejected f=geojson; falling back to f=json", layer_id
                )
                use_geojson = False
                continue

            if use_geojson:
                page = payload.get("features") or []
                # ArcGIS puts the flag both at the top level and under "properties".
                exceeded = bool(
                    payload.get("exceededTransferLimit")
                    or (payload.get("properties") or {}).get("exceededTransferLimit")
                )
            else:
                page = _esri_json_to_geojson(payload)
                exceeded = bool(payload.get("exceededTransferLimit"))

            for record in page:
                _stamp(record, layer_id)
            records.extend(page)
            pages += 1

            if not page or not exceeded or len(page) < page_size:
                break
            offset += len(page)
        else:
            logger.warning(
                "WA DNR layer %s hit the %s-page safety cap at %s records — pagination "
                "is probably not advancing; the result is TRUNCATED",
                layer_id, MAX_PAGES, len(records),
            )

        logger.info(
            "WA DNR layer %s: fetched %s records in %s page(s)", layer_id, len(records), pages
        )
        if expected is not None and len(records) != expected:
            logger.error(
                "WA DNR layer %s returned %s records but returnCountOnly said %s — "
                "the extract is incomplete, do not publish it",
                layer_id, len(records), expected,
            )
        return records

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Fetch ``self.layer_ids`` (default: both point layers) as GeoJSON-shaped dicts.

        Layers are fetched sequentially, not with asyncio.gather: this host is the one
        §28.3 calls unreliable, and hammering it in parallel is how a slow service
        becomes a failed one.
        """
        all_records: List[Dict[str, Any]] = []
        for layer_id in self.layer_ids:
            all_records.extend(await self.fetch_layer(layer_id, bbox=bbox))
        return all_records

    async def fetch_documents(
        self, site_ids: Iterable[int], chunk_size: int = 200
    ) -> List[Dict[str, Any]]:
        """Rows from the related "Metallic Minerals Documents" table for these SITE_IDs.

        Returns raw attribute dicts (the table has no geometry): TITLE, AUTHOR,
        DOCUMENT_DATE, DOCUMENT_TYPE, DOCUMENT_DESC, COMMENTS, HYPERLINK, SITE_ID.
        The table holds 107,739 rows, so it is only ever queried by SITE_ID — the
        WHERE clause is chunked to keep the URL a sane length.
        """
        ids = sorted({int(s) for s in site_ids if s is not None})
        rows: List[Dict[str, Any]] = []

        for start in range(0, len(ids), chunk_size):
            chunk = ids[start:start + chunk_size]
            where = "SITE_ID IN ({})".format(",".join(str(i) for i in chunk))
            offset = 0
            pages = 0
            while pages < MAX_PAGES:
                payload = await self._query(
                    TABLE_METALLIC_DOCUMENTS,
                    {
                        "where": where,
                        "outFields": "*",
                        "returnGeometry": "false",
                        "orderByFields": "SITE_ID",
                        "resultOffset": offset,
                        "resultRecordCount": PAGE_SIZE,
                        "f": "json",
                    },
                )
                page = [f.get("attributes", {}) for f in (payload.get("features") or [])]
                rows.extend(page)
                pages += 1
                if not page or len(page) < PAGE_SIZE:
                    break
                offset += len(page)

        logger.info("WA DNR documents: %s rows for %s site ids", len(rows), len(ids))
        return rows

    # -------------------------------------------------------------- normalize

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """Map WA DNR point records to Feature ORM instances.

        Mapping actually applied:
            SITE_ID + layer     -> source_record_id ("gs-181" / "mm-1899", CONTRACT.md uid)
            SITE_NAME           -> name
            PRIMARY_COMMODITY   -> commodity_primary
            COMMODITIES         -> commodity_secondary (comma-split)
            PRODUCTION          -> status ("historic" when yes, else "occurrence")
            LOCATION_ACCURACY   -> source_quality, via ACCURACY_CLASSES/ACCURACY_QUALITY
            geometry            -> Point, SRID 4326
            OBJECTID            -> raw_record_ref, as a URL that re-fetches the full row

        **Dropped, because models/feature.py has no column for them:** ASSAYS,
        MINING_DISTRICT, ORE_MINERALS, GANGUE, ALTERNATE_NAMES, LEGAL_DESCRIPTION,
        LOCATION_DESCRIPTION, LOCATION_SOURCE, COUNTY, COMMENTS, and the raw
        LOCATION_ACCURACY string. That is a real loss of the very fields §28.2 says
        make this source worth having — ASSAYS especially, which the assay-primacy
        rule in knowledge/historical/gold.md wants as a hard input. They reach the
        agents through data/reference/wa_occurrences.geojson instead, which keeps all
        of them. Widening Feature would need an Alembic migration and a decision about
        whether the DB path (prod-only) is worth investing in at all.
        """
        features: List[Feature] = []

        for record in raw_records:
            props = record.get("properties") or {}
            geom = record.get("geometry")
            if not geom or geom.get("type") != "Point":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2 or coords[0] is None or coords[1] is None:
                continue
            lon, lat = coords[0], coords[1]

            layer_id = record.get("_layer_id")
            accuracy = accuracy_class_for(props.get("LOCATION_ACCURACY"))
            object_id = props.get("OBJECTID")

            features.append(
                Feature(
                    source_channel=self.channel_config.name,
                    source_record_id=record.get("uid") or str(props.get("SITE_ID", "")),
                    raw_record_ref=(
                        f"{self.base_url}/{layer_id}/query"
                        f"?where=OBJECTID%3D{object_id}&outFields=*&f=json"
                        if object_id is not None and layer_id is not None else None
                    ),
                    feature_type="mineral_occurrence",
                    name=props.get("SITE_NAME"),
                    commodity_primary=props.get("PRIMARY_COMMODITY"),
                    commodity_secondary=_split_commodities(props.get("COMMODITIES")),
                    # WA DNR records no "currently operating" flag, so documented
                    # production is the strongest claim available: historic producer
                    # vs a site that is only an occurrence.
                    status="historic" if _yes(props.get("PRODUCTION")) else "occurrence",
                    geometry=from_shape(Point(lon, lat), srid=4326),
                    source_quality=ACCURACY_QUALITY.get(accuracy, ACCURACY_QUALITY["unknown"]),
                )
            )

        return features


def _stamp(record: Dict[str, Any], layer_id: int) -> None:
    """Tag a raw record with its provenance, in place.

    ``source_layer`` uses the geodatabase spelling so a live record and the same record
    read from data/raw/ are indistinguishable downstream; ``uid`` is CONTRACT.md's
    globally unique "<prefix>-<site_id>".
    """
    props = record.setdefault("properties", {})
    site_id = props.get("SITE_ID")
    record["_layer_id"] = layer_id
    record["_layer_name"] = SERVICE_LAYER_NAMES.get(layer_id)
    record["source_layer"] = GDB_LAYER_NAMES.get(layer_id)
    prefix = UID_PREFIXES.get(layer_id)
    if prefix and site_id is not None:
        record["uid"] = f"{prefix}-{site_id}"


def _esri_json_to_geojson(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert an Esri JSON (f=json) query response to GeoJSON-shaped feature dicts.

    Only the geometry kinds this service actually serves on the layers above are
    handled: points, and polygon rings. Anything else is skipped with a warning
    rather than guessed at.

    Note the two paths are not byte-identical for polygons: f=geojson returns a
    single-ring district as ``Polygon``, this fallback always emits ``MultiPolygon``.
    Both are valid GeoJSON and shapely reads either, but do not diff the outputs and
    expect them to match.
    """
    out: List[Dict[str, Any]] = []
    geometry_type = payload.get("geometryType")

    for feature in payload.get("features") or []:
        attributes = feature.get("attributes") or {}
        esri_geom = feature.get("geometry")
        geometry: Optional[Dict[str, Any]] = None

        if esri_geom is None:
            geometry = None
        elif geometry_type == "esriGeometryPoint" or ("x" in esri_geom and "y" in esri_geom):
            if esri_geom.get("x") is None or esri_geom.get("y") is None:
                continue
            geometry = {"type": "Point", "coordinates": [esri_geom["x"], esri_geom["y"]]}
        elif "rings" in esri_geom:
            # Esri rings do not distinguish outer rings from holes by winding in a way
            # worth trusting here; every ring becomes its own polygon. Good enough for
            # the district polygons, and honest about what it is.
            geometry = {
                "type": "MultiPolygon",
                "coordinates": [[ring] for ring in esri_geom["rings"]],
            }
        else:
            logger.warning(
                "WA DNR f=json fallback: unhandled geometry (geometryType=%s, keys=%s)",
                geometry_type, sorted(esri_geom),
            )
            continue

        out.append({"type": "Feature", "properties": attributes, "geometry": geometry})

    return out
