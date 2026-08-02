import type maplibregl from 'maplibre-gl'

/**
 * Basemap definitions — USGS The National Map.
 *
 * Public-domain cached raster tiles: no API key, no account, no usage tier.
 * Web Mercator, 256 px, and the ArcGIS tile path order is `{z}/{y}/{x}` — NOT
 * `{z}/{x}/{y}`. MapLibre does plain string substitution, so a transposed
 * template renders as a scrambled mess rather than failing usefully.
 *
 * Zoom limits are load-bearing, and they are NOT what the service metadata
 * advertises. All four report `maxScale: 9027.98` (zoom 16), but the seeded
 * caches differ. Measured 2026-08-01 by probing z3–16 at three points (Monte
 * Cristo, Republic, central Cascades):
 *
 *   USGSTopo              z3–16 complete              → maxzoom 16
 *   USGSImageryTopo       z3–16 complete              → maxzoom 16
 *   USGSImageryOnly       z3–16 complete              → maxzoom 16
 *   USGSShadedReliefOnly  z3–8 and z12–13 ONLY        → see below
 *
 * Two consequences:
 *
 * 1. z17+ 404s everywhere, so `maxzoom: 16` is mandatory on the three complete
 *    services. Without it the basemap goes blank exactly when you zoom in to
 *    inspect a cell.
 * 2. Shaded relief has a hole at z9–11 and nothing above z13. Deriving its
 *    limits from `maxScale` gives a blank map across most of the useful range.
 *    It is therefore drawn *over* USGSTopo, active only from z12: at z12–13 it
 *    uses native tiles, above that MapLibre overzooms z13 (soft, but hillshade
 *    is smooth so it reads fine), and below z12 the topo underneath shows
 *    through. The result is never blank, and it is greyscale at every zoom
 *    where you would actually be reading a score ramp.
 */

const USGS_BASE = 'https://basemap.nationalmap.gov/arcgis/rest/services'
const USGS_ATTRIBUTION = 'USGS The National Map'

export interface BasemapDef {
  id: string
  label: string
  /** Shown in the layer panel under the label */
  hint: string
  source: maplibregl.RasterSourceSpecification
  /** Lowest zoom at which this layer draws. Below it, `under` shows through. */
  minzoom?: number
  /** Basemap id to keep visible beneath this one (patchy-cache fallback). */
  under?: string
}

function usgs(service: string, maxzoom: number): maplibregl.RasterSourceSpecification {
  return {
    type: 'raster',
    tiles: [`${USGS_BASE}/${service}/MapServer/tile/{z}/{y}/{x}`],
    tileSize: 256,
    maxzoom,
    attribution: USGS_ATTRIBUTION,
  }
}

export const BASEMAPS: BasemapDef[] = [
  {
    id: 'usgs-topo',
    label: 'USGS Topo',
    hint: 'Contours, place names, trails',
    source: usgs('USGSTopo', 16),
  },
  {
    id: 'usgs-relief',
    label: 'Shaded Relief',
    hint: 'Greyscale terrain — best under results',
    source: usgs('USGSShadedReliefOnly', 13),
    // The cache has a hole at z9–11; below z12 the topo underneath shows through.
    minzoom: 12,
    under: 'usgs-topo',
  },
  {
    id: 'usgs-imagery-topo',
    label: 'Imagery + Topo',
    hint: 'Aerial with topo linework',
    source: usgs('USGSImageryTopo', 16),
  },
  {
    id: 'usgs-imagery',
    label: 'Imagery',
    hint: 'Plain aerial',
    source: usgs('USGSImageryOnly', 16),
  },
  {
    id: 'osm',
    label: 'OpenStreetMap',
    // Kept as a fallback so the app still works if the USGS services are down.
    hint: 'Fallback — little detail in the Cascades',
    source: {
      type: 'raster',
      tiles: [
        'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
        'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
        'https://c.tile.openstreetmap.org/{z}/{x}/{y}.png',
      ],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© OpenStreetMap contributors',
    },
  },
]

/**
 * Shaded relief when results are on screen.
 *
 * USGSTopo is a *coloured* map — green forest, blue water, brown contours — and
 * the score ramp is red → orange → yellow. A yellow "low" cell over green
 * forest reads as a different colour from the same cell over a white snowfield,
 * so the eye cannot compare them. Greyscale relief carries all the terrain
 * information (ridges, drainages, cirques) without competing for hue.
 */
export const DEFAULT_BASEMAP = 'usgs-relief'

/**
 * All basemaps live in one style, toggled by visibility.
 *
 * Rebuilding the style with `map.setStyle()` wipes every custom source and
 * layer — the results choropleth, the AOI, and the draw control all vanish —
 * and re-adding them from a `styledata` handler is a reliable source of
 * teardown bugs. Registering everything up front and flipping
 * `visibility` has neither problem.
 */
/**
 * Font endpoint for symbol layers.
 *
 * A raster-only style has no `glyphs`, and MapLibre then rejects every layer
 * using `text-field` with "use of text-field requires a style glyphs property"
 * — silently, at addLayer time, so the toponym and wilderness labels simply
 * never appear. This is MapLibre's own keyless demo font server; if it is
 * unreachable the labels drop out and the rest of the map is unaffected.
 */
const GLYPHS = 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf'

/** The one face the endpoint above actually serves. */
export const LABEL_FONT = ['Noto Sans Regular']

export function buildStyle(active: string): maplibregl.StyleSpecification {
  const visible = new Set(visibleBasemapIds(active))
  // A basemap that underlays another must be listed first, or it would paint
  // over the layer it is supposed to sit beneath.
  const ordered = [...BASEMAPS].sort(
    (a, b) => (a.under ? 1 : 0) - (b.under ? 1 : 0)
  )
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: Object.fromEntries(BASEMAPS.map((b) => [b.id, b.source])),
    layers: ordered.map((b) => ({
      id: `basemap-${b.id}`,
      type: 'raster' as const,
      source: b.id,
      ...(b.minzoom !== undefined ? { minzoom: b.minzoom } : {}),
      layout: {
        visibility: (visible.has(b.id) ? 'visible' : 'none') as 'visible' | 'none',
      },
    })),
  }
}

/** Which basemap layers should be visible for a given selection. */
export function visibleBasemapIds(active: string): string[] {
  const def = BASEMAPS.find((b) => b.id === active)
  return def?.under ? [def.under, def.id] : [active]
}

/**
 * PLSS township / range / section — BLM CadNSDI.
 *
 * Mining claims are located and described in PLSS terms: township, range,
 * section, quarter-quarter. Every historical claim record, GLO patent and BLM
 * serial register page uses this grid, so without it on the map a scored cell
 * cannot be connected to any claim document.
 *
 * The service is NOT tile-cached (`exportTilesAllowed: false`), so it cannot be
 * an XYZ source. It does expose WMS, which MapLibre can drive with bbox
 * templating. Layer ids verified live against the MapServer metadata:
 * 1 Township, 2 Section, 3 Intersected.
 */
const BLM_WMS =
  'https://gis.blm.gov/arcgis/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer/WMSServer'

export const PLSS_SOURCE: maplibregl.RasterSourceSpecification = {
  type: 'raster',
  tiles: [
    `${BLM_WMS}?service=WMS&request=GetMap&layers=2,3&styles=&format=image/png` +
      '&transparent=true&version=1.3.0&crs=EPSG:3857&width=256&height=256' +
      '&bbox={bbox-epsg-3857}',
  ],
  tileSize: 256,
  attribution: 'BLM CadNSDI PLSS',
}

/**
 * Section geometry only draws below 1:500,000 and Intersected below 1:200,000
 * in the service's own scale rules, so the layer auto-hides when zoomed out
 * rather than appearing broken.
 */
export const PLSS_MINZOOM = 11
