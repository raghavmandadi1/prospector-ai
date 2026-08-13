"""
USGS Mineral Resources Data System (MRDS) Connector

Fetches mineral deposit records via the USGS MRDS WFS service.
Endpoint: https://mrdata.usgs.gov/services/mrds

This is the same dataset Gaia GPS renders as its "Mines and Mineral Resources"
overlay (§28.1 of "steps for raghav 2.0.md") — public domain, no subscription.

WHAT WAS WRONG HERE (all three verified live against the service on 2026-08-12):

1. **`typeName=mrds` does not exist.** The service publishes `ms:mrds-high` and
   `ms:mrds-low` (identical row counts — they are scale-rendering variants of one
   table). The old request got back, under an HTTP **200**, a
   `<ServiceExceptionReport>` reading "TYPENAME 'mrds' doesn't exist in this
   server", which `BaseConnector._get()` then fed to `response.json()`. So
   `fetch()` did not silently truncate — it raised a JSON decode error on every
   call. The 1000-record cap was the *second* bug, hiding behind the first.
2. **The service does not support JSON output at all.** `outputFormat` accepts only
   GML (`application/gml+xml; version=3.2` and the `text/xml; subtype=gml/*`
   variants); `application/json`, `geojson` and `json` are all rejected with
   "not a permitted output format for layer 'mrds-high'". So this module parses GML
   into GeoJSON-shaped dicts, which keeps `normalize()` (unchanged) working on the
   `{"properties": ..., "geometry": {...}}` shape it already expects.
3. **No pagination, `maxFeatures=1000`.** Washington alone holds **16,499** MRDS
   records (`resultType=hits` over WA_BOUNDS, 2026-08-12), so the old ceiling
   discarded ~94% of the state without a word. Now paged with WFS 2.0.0
   `startIndex`/`count`, with a hard cap that logs a WARNING if it is ever reached.

TWO TRAPS THIS SERVICE SETS, both handled below:

- **Axis order flips with WFS version.** 1.0.0 returns `<gml:coordinates>` as
  *lon,lat*; 1.1.0 and 2.0.0 return `<gml:pos>` as *lat lon*, because they honour the
  EPSG authority axis order — and 1.1.0 does it even when you ask for
  `srsName=EPSG:4326`. Getting this wrong puts every Washington mine in Somalia.
  `_parse_pos()` keys off the srsName form and then sanity-checks with the one fact
  that cannot be argued with: |latitude| never exceeds 90.
- **`bbox` axis order.** A bare `bbox=minx,miny,maxx,maxy` is read lon/lat and works;
  the same numbers in lat/lon order return 0 features (an empty Indian Ocean box) with
  no error at all. This module always appends the explicit `urn:ogc:def:crs:OGC:1.3:CRS84`
  suffix so the lon/lat reading is stated rather than assumed.

KNOWN LIMITATION — the WFS attribute set is much thinner than MRDS proper. The only
fields served are `dep_id, site_name, dev_stat, fips_code, huc_code, quad_code, url,
code_list`. There is **no `commod1`/`commod2`/`commod3` and no `dep_type`**, so
`normalize()` (deliberately left as it was) always writes `commodity_primary`,
`commodity_secondary` and `deposit_type` as NULL. Commodities are present only as
`code_list` — USGS commodity codes, e.g. `" CU AU"` — and mapping those to the
vocabulary the scoring engine wants is a decision, not a detail, so it is left for
whoever owns that vocabulary. Note also that CLAUDE.md → Known Gaps #5 records this
WFS returning 403 to tiled bbox requests; no 403 occurred during this verification,
but it is a rate-limiting behaviour that can return, hence the retry/backoff below.
"""
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree

import httpx
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.connectors.base_connector import BaseConnector
from app.models.feature import Feature

logger = logging.getLogger(__name__)

# WFS 2.0.0: advertises ImplementsResultPaging=TRUE, and uses count/startIndex
# (1.1.0 spells the page size maxFeatures). Verified paging live 2026-08-12: three
# consecutive count=2 pages returned six distinct dep_ids, no overlap, no gaps.
WFS_VERSION = "2.0.0"

# The real type name. `mrds-low` has the same row count and is the small-scale
# rendering variant; `mrds-high` is what a detail query should use.
TYPE_NAME = "ms:mrds-high"

# The server does not clamp: count=5000 really returns 5000. 1000 keeps each response
# under ~1 MB and each request at ~1.5 s, which is a kinder shape for a public service.
PAGE_SIZE = 1000

# Absolute ceiling across all pages. MRDS is ~300k records worldwide and ~16.5k in
# Washington, so an unbounded bbox could legitimately be large — this exists to stop a
# runaway loop, and unlike the old maxFeatures=1000 it is LOUD when it bites.
MAX_RECORDS = 100_000

REQUEST_TIMEOUT_S = 120.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0

# The bbox CRS urn that makes lon,lat ordering explicit. See the module docstring.
BBOX_CRS_URN = "urn:ogc:def:crs:OGC:1.3:CRS84"

GML_NS = "http://www.opengis.net/gml/3.2"
WFS_NS = "http://www.opengis.net/wfs/2.0"

# srsName forms that mean "EPSG authority axis order", i.e. latitude first.
_LAT_FIRST_SRS = re.compile(r"^urn:(?:x-)?ogc:def:crs:EPSG:", re.IGNORECASE)


class WFSServiceError(RuntimeError):
    """The WFS returned an exception document.

    MapServer reports errors as `<ServiceExceptionReport>` (WFS 1.x) or
    `<ows:ExceptionReport>` (2.0.0), sometimes under an HTTP 200. Silently returning
    [] for those is what let bug (1) in the module docstring live for so long.
    """


def _parse_pos(text: str, srs_name: Optional[str]) -> Optional[Tuple[float, float]]:
    """Parse a gml:pos / gml:coordinates pair into (lon, lat).

    ``srs_name`` decides the declared axis order: the `urn:ogc:def:crs:EPSG::4326`
    form is latitude-first per the EPSG authority, the short `EPSG:4326` form used by
    WFS 1.0.0 `gml:coordinates` is longitude-first. Because a declared order can still
    be wrong, the result is checked against the only hard bound available — latitude
    cannot exceed ±90 — and swapped, with a warning, if the check fails.
    """
    parts = [p for p in re.split(r"[\s,]+", text.strip()) if p]
    if len(parts) < 2:
        return None
    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError:
        return None

    if srs_name and _LAT_FIRST_SRS.match(srs_name):
        lat, lon = a, b
    else:
        lon, lat = a, b

    if abs(lat) > 90.0 >= abs(lon):
        # The declared order was wrong; the values themselves settle it.
        logger.warning(
            "MRDS: axis order for srsName=%r looks inverted (%s) — swapping", srs_name, text.strip()
        )
        lon, lat = lat, lon

    if abs(lat) > 90.0 or abs(lon) > 180.0:
        logger.warning("MRDS: dropping out-of-range position %r (srsName=%r)", text.strip(), srs_name)
        return None
    return lon, lat


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _exception_message(body: str) -> Optional[str]:
    """Return the text of a WFS/OWS exception document, or None if this isn't one.

    Worth the trouble because the service's own message is far more useful than the
    status code: "TYPENAME 'mrds' doesn't exist in this server" names the bug, where
    "400 Bad Request" sends you reading tcpdump. WFS 1.x wraps these in an HTTP 200 and
    2.0.0 in a 4xx, so both call sites need it.
    """
    if "ExceptionReport" not in body and "ServiceExceptionReport" not in body:
        return None
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    if _strip_ns(root.tag) not in ("ServiceExceptionReport", "ExceptionReport"):
        return None
    return " ".join(t.strip() for t in root.itertext() if t and t.strip())[:500]


def _gml_to_geojson_features(xml_text: str) -> List[Dict[str, Any]]:
    """Convert a WFS 2.0.0 GML FeatureCollection into GeoJSON-shaped feature dicts.

    Returns the same shape `normalize()` has always consumed:
    ``{"properties": {...}, "geometry": {"type": "Point", "coordinates": [lon, lat]}}``.
    Every `ms:*` child element that is not the geometry becomes a property, with the
    namespace stripped, so `dep_id`, `site_name` etc. keep the names normalize() uses.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise WFSServiceError(f"MRDS returned unparseable XML: {exc}") from exc

    if _strip_ns(root.tag) in ("ServiceExceptionReport", "ExceptionReport"):
        message = " ".join(t.strip() for t in root.itertext() if t and t.strip())
        raise WFSServiceError(f"MRDS WFS exception: {message[:500]}")

    features: List[Dict[str, Any]] = []
    for member in root.findall(f"{{{WFS_NS}}}member"):
        for feature_el in list(member):
            props: Dict[str, Any] = {}
            geometry: Optional[Dict[str, Any]] = None

            for child in feature_el:
                name = _strip_ns(child.tag)
                if name == "boundedBy":
                    # An envelope duplicating the point; the geometry element is
                    # authoritative, so ignore it rather than risk reading the wrong one.
                    continue
                if name == "geometry":
                    point = child.find(f".//{{{GML_NS}}}Point")
                    if point is None:
                        continue
                    pos = point.find(f"{{{GML_NS}}}pos")
                    if pos is None or not (pos.text or "").strip():
                        continue
                    srs = point.get("srsName") or pos.get("srsName")
                    lon_lat = _parse_pos(pos.text, srs)
                    if lon_lat:
                        geometry = {"type": "Point", "coordinates": [lon_lat[0], lon_lat[1]]}
                    continue
                text = (child.text or "").strip()
                props[name] = text or None

            if geometry is None:
                continue
            features.append({"type": "Feature", "properties": props, "geometry": geometry})

    return features


class USGSMRDSConnector(BaseConnector):
    BASE_URL = "https://mrdata.usgs.gov/services/mrds"

    def _base_params(self, bbox: Optional[tuple]) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "service": "WFS",
            "version": WFS_VERSION,
            "request": "GetFeature",
            "typeNames": TYPE_NAME,
        }
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            # The CRS urn is not optional politeness: without it the axis order is
            # implied, and the implied reading has already been wrong once here.
            params["bbox"] = (
                f"{min_lon},{min_lat},{max_lon},{max_lat},{BBOX_CRS_URN}"
            )
        return params

    async def _get_xml(self, params: Dict[str, Any]) -> str:
        """GET the WFS and return the raw body.

        BaseConnector._get() cannot be used: it calls response.json(), and this service
        only ever speaks XML. Retries transport errors, timeouts and 5xx; a 4xx is
        surfaced immediately because it will not fix itself.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                    response = await client.get(self.BASE_URL, params=params)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"{response.status_code} from MRDS WFS",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                # WFS 1.x wraps its errors in an HTTP 200, so a successful status is not
                # evidence of a successful query. Check here, once, so that every caller
                # of _get_xml gets an exception rather than an error document to parse.
                detail = _exception_message(response.text)
                if detail:
                    raise WFSServiceError(f"MRDS WFS exception (HTTP 200): {detail}")
                return response.text
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code < 500:
                    # Includes the 403 on tiled bbox requests noted in CLAUDE.md →
                    # Known Gaps #5. Surfaced, not swallowed, so the caller can tell a
                    # blocked request from an empty area. If the body explains itself,
                    # raise that instead of the bare status line.
                    detail = _exception_message(exc.response.text)
                    if detail:
                        raise WFSServiceError(
                            f"MRDS WFS {exc.response.status_code}: {detail}"
                        ) from exc
                    raise
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc

            if attempt == MAX_ATTEMPTS:
                break
            delay = BACKOFF_BASE_S * (2 ** (attempt - 1))
            logger.warning(
                "MRDS WFS attempt %s/%s failed (%s); retrying in %.0fs",
                attempt, MAX_ATTEMPTS, last_exc, delay,
            )
            await asyncio.sleep(delay)

        raise RuntimeError(f"MRDS WFS failed after {MAX_ATTEMPTS} attempts: {last_exc}") from last_exc

    async def count(self, bbox: Optional[tuple] = None) -> Optional[int]:
        """`resultType=hits` — the number of records the bbox actually holds.

        Returns None if the service reports numberMatched="unknown" rather than a
        number, which it does for some request shapes. Used to prove the paging loop
        got everything; never used to size the loop.
        """
        params = self._base_params(bbox)
        params["resultType"] = "hits"
        body = await self._get_xml(params)
        match = re.search(r'numberMatched="(\d+)"', body)
        if match:
            return int(match.group(1))
        detail = _exception_message(body)
        if detail:
            raise WFSServiceError(f"MRDS WFS exception on resultType=hits: {detail}")
        return None

    async def fetch(self, bbox: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Fetch MRDS records via WFS GetFeature, paged with startIndex.

        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84

        Returns GeoJSON-shaped dicts (see _gml_to_geojson_features) so that
        normalize() is unchanged. Pages until a short page arrives; stops at
        MAX_RECORDS with a WARNING, so a truncated result is never silent again.
        """
        expected: Optional[int] = None
        try:
            expected = await self.count(bbox=bbox)
        except Exception as exc:  # noqa: BLE001 - the count is a check, not the job
            # A failed hits query must not abort a fetch that would otherwise work.
            logger.warning("MRDS resultType=hits failed (%s); fetching without a cross-check", exc)
        if expected is not None:
            logger.info("MRDS: %s records expected%s", expected, " in bbox" if bbox else " worldwide")

        records: List[Dict[str, Any]] = []
        start_index = 0
        pages = 0
        truncated = False

        while True:
            params = self._base_params(bbox)
            params["count"] = PAGE_SIZE
            params["startIndex"] = start_index
            page = _gml_to_geojson_features(await self._get_xml(params))
            pages += 1
            records.extend(page)

            if len(page) < PAGE_SIZE:
                break
            if len(records) >= MAX_RECORDS:
                truncated = True
                break
            start_index += len(page)

        if truncated:
            logger.warning(
                "MRDS: stopped at the %s-record safety cap after %s pages — the result "
                "is TRUNCATED. Narrow the bbox or raise MAX_RECORDS deliberately.",
                MAX_RECORDS, pages,
            )
        logger.info("MRDS: fetched %s records in %s page(s)", len(records), pages)

        if expected is not None and not truncated and len(records) != expected:
            logger.warning(
                "MRDS returned %s records but resultType=hits said %s — paging may have "
                "dropped or duplicated records",
                len(records), expected,
            )
        return records

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[Feature]:
        """
        Map MRDS GeoJSON features to Feature ORM objects.

        MRDS field mapping:
        - dep_id → source_record_id
        - site_name → name
        - commod1 → commodity_primary
        - commod2, commod3 → commodity_secondary
        - dev_stat → status
        - dep_type → deposit_type
        - geometry.coordinates → geometry (Point)

        Unchanged from before the paging fix, and therefore still carrying the WFS
        attribute gap flagged in the module docstring: this service serves no
        `commod*` or `dep_type` fields, so those three columns come out NULL.
        `code_list` (e.g. " CU AU") holds the commodity information instead.
        """
        features = []
        for record in raw_records:
            props = record.get("properties", {})
            geom = record.get("geometry")

            if not geom or geom.get("type") != "Point":
                continue

            lon, lat = geom["coordinates"][:2]
            shapely_point = Point(lon, lat)

            secondary = [
                c for c in [props.get("commod2"), props.get("commod3")] if c
            ]

            feature = Feature(
                source_channel=self.channel_config.name,
                source_record_id=str(props.get("dep_id", "")),
                feature_type="deposit",
                name=props.get("site_name"),
                commodity_primary=props.get("commod1"),
                commodity_secondary=secondary or None,
                deposit_type=props.get("dep_type"),
                status=props.get("dev_stat"),
                geometry=from_shape(shapely_point, srid=4326),
                source_quality=0.8,  # USGS MRDS is a high-quality authoritative source
            )
            features.append(feature)

        return features
