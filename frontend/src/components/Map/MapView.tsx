import { useEffect, useRef, useCallback, useState } from 'react'
import maplibregl from 'maplibre-gl'
import MapboxDraw from '@mapbox/mapbox-gl-draw'
import area from '@turf/area'
import { useAppStore } from '../../store'
import type { OverlayId } from '../../store'
import { NOVELTY, NOVELTY_ORDER } from '../../types'
import type { NoveltyClass, ScoredCell } from '../../types'
import {
  BASEMAPS,
  LABEL_FONT,
  PLSS_MINZOOM,
  PLSS_SOURCE,
  buildStyle,
  visibleBasemapIds,
} from './basemaps'
import { DRAW_STYLES } from './drawStyles'
import { parseCoordinate } from './coords'
import CoordinateReadout from './CoordinateReadout'
import LayerPanel from './LayerPanel'
import { API_BASE } from '../../api/client'

// mapbox-gl-draw CSS (inline the essential styles so we don't need a separate import)
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css'

const TILESERVER_URL = import.meta.env.VITE_TILESERVER_URL as string | undefined

// Minimum AOI area in square meters (~25 km²)
const MIN_AOI_AREA_M2 = 25_000_000

// Above this many cells the white cell outlines stop reading as a grid and
// start reading as grey haze over the fill. At 125 m display resolution that
// happens well before the display cap.
const OUTLINE_MAX_CELLS = 2000

const DEFAULT_VIEW = { lng: -120.5, lat: 47.5, zoom: 7 }

/**
 * MapLibre's `ExpressionSpecification` is a deeply recursive tuple union that
 * TypeScript cannot infer from an array literal, so every expression in this
 * file has to be cast. These two helpers keep the cast in one place instead of
 * sprinkling `as unknown as` down the file.
 */
const expr = (e: unknown[]): maplibregl.ExpressionSpecification =>
  e as unknown as maplibregl.ExpressionSpecification
const filt = (e: unknown[]): maplibregl.FilterSpecification =>
  e as unknown as maplibregl.FilterSpecification

// Tier color scale
const TIER_COLORS: Record<string, string> = {
  high: '#ef4444',      // red-500
  medium: '#f97316',    // orange-500
  low: '#eab308',       // yellow-500
  negligible: '#6b7280', // gray-500
}

function shadingValue(mode: 'relative' | 'absolute') {
  return mode === 'relative'
    ? ['coalesce', ['get', 'relative_score'], ['get', 'score']]
    : ['get', 'score']
}

// Continuous prospectivity ramp. In 'relative' mode the value is the
// AOI-relative score (best spots in THIS area); in 'absolute' mode it's the
// raw composite. Same ramp, different driving value.
function fillColorExpression(mode: 'relative' | 'absolute'): maplibregl.ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    shadingValue(mode),
    0.0, TIER_COLORS.negligible,
    0.35, TIER_COLORS.low,
    0.65, TIER_COLORS.medium,
    0.9, TIER_COLORS.high,
    1.0, '#b91c1c', // red-700
  ] as unknown as maplibregl.ExpressionSpecification
}

/**
 * Low-value cells fade back so the hotspots read at a glance, then the whole
 * ramp is scaled by the user's opacity slider.
 *
 * The slider matters more than it looks: at a fixed 0.75 the highest-scoring
 * cells — the ones you most want to interrogate — are exactly the ones whose
 * ground you cannot see. Being able to fade results in and out over fixed
 * terrain is the main way to judge whether a score makes geological sense.
 */
function fillOpacityExpression(
  mode: 'relative' | 'absolute',
  scale: number
): maplibregl.ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    shadingValue(mode),
    0.0, 0.2 * scale,
    1.0, 1.0 * scale,
  ] as unknown as maplibregl.ExpressionSpecification
}

export default function MapView() {
  const mapContainerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const drawRef = useRef<MapboxDraw | null>(null)
  const [cursor, setCursor] = useState<{ lng: number; lat: number } | null>(null)
  // Layer effects must not fire before `load` has added their layers. Without
  // this gate the initial pass early-returns on !isStyleLoaded() and — since
  // the store values it depends on never change afterwards — never runs again,
  // so overlays restored from localStorage silently stay hidden.
  const [styleReady, setStyleReady] = useState(false)
  const [jumpTo, setJumpTo] = useState('')
  const [jumpError, setJumpError] = useState(false)

  const {
    analysisResults, setSelectedCell,
    aoi, setAoi,
    isDrawing, setIsDrawing,
    setAoiAreaKm2,
    shadingMode,
    basemap,
    resultsOpacity, resultsVisible, noveltyOutlines,
    overlays,
    resolutionM,
    setMapView,
  } = useAppStore()

  const handleDrawCreate = useCallback((e: { features: GeoJSON.Feature[] }) => {
    const feature = e.features[0]
    if (!feature) return

    const areaM2 = area(feature as GeoJSON.Feature)
    const areaKm2 = areaM2 / 1_000_000

    if (areaM2 < MIN_AOI_AREA_M2) {
      // Too small — remove the polygon and warn
      if (drawRef.current) {
        drawRef.current.deleteAll()
      }
      setAoi(null)
      setAoiAreaKm2(null)
      alert(
        `Area too small: ${areaKm2.toFixed(1)} km².\n` +
        `Minimum area is 25 km² for meaningful analysis.`
      )
      return
    }

    setAoi(feature)
    setAoiAreaKm2(areaKm2)
    setIsDrawing(false)
  }, [setAoi, setAoiAreaKm2, setIsDrawing])

  const handleDrawUpdate = useCallback((e: { features: GeoJSON.Feature[] }) => {
    const feature = e.features[0]
    if (!feature) return

    const areaM2 = area(feature as GeoJSON.Feature)
    const areaKm2 = areaM2 / 1_000_000

    if (areaM2 < MIN_AOI_AREA_M2) {
      setAoi(null)
      setAoiAreaKm2(null)
      return
    }

    setAoi(feature)
    setAoiAreaKm2(areaKm2)
  }, [setAoi, setAoiAreaKm2])

  const handleDrawDelete = useCallback(() => {
    setAoi(null)
    setAoiAreaKm2(null)
  }, [setAoi, setAoiAreaKm2])

  // Initialize map
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return

    const saved = useAppStore.getState().mapView ?? DEFAULT_VIEW
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      // Every basemap is registered up front and switched by visibility.
      // setStyle() would wipe the results layer, the AOI and the draw control.
      style: buildStyle(useAppStore.getState().basemap),
      center: [saved.lng, saved.lat],
      zoom: saved.zoom,
    })

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
    // Imperial: mining ground, claim dimensions and the historical literature
    // are all in feet and miles.
    map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-right')

    // Initialize draw control
    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: {
        polygon: true,
        trash: true,
      },
      defaultMode: 'simple_select',
      // Upstream's default theme is rejected by MapLibre's validator, which
      // leaves the in-progress AOI edge invisible. See drawStyles.ts.
      styles: DRAW_STYLES as never,
    })

    // MapboxDraw works with MapLibre via the mapbox-gl compatibility
    map.addControl(draw as unknown as maplibregl.IControl, 'top-left')
    drawRef.current = draw

    map.on('draw.create', handleDrawCreate)
    map.on('draw.update', handleDrawUpdate)
    map.on('draw.delete', handleDrawDelete)

    map.on('mousemove', (e) => setCursor({ lng: e.lngLat.lng, lat: e.lngLat.lat }))
    map.on('mouseout', () => setCursor(null))
    map.on('moveend', () => {
      const c = map.getCenter()
      setMapView({ lng: c.lng, lat: c.lat, zoom: map.getZoom() })
    })

    map.on('load', () => {
      // --- Features layer from Martin tileserver (only if configured) ---
      if (TILESERVER_URL) {
        map.addSource('features-tiles', {
          type: 'vector',
          tiles: [`${TILESERVER_URL}/features/{z}/{x}/{y}`],
          minzoom: 6,
          maxzoom: 14,
        })

        map.addLayer({
          id: 'features-points',
          type: 'circle',
          source: 'features-tiles',
          'source-layer': 'features',
          paint: {
            'circle-radius': 5,
            'circle-color': '#60a5fa',
            'circle-opacity': 0.8,
            'circle-stroke-color': '#1e40af',
            'circle-stroke-width': 1,
          },
        })
      }

      addOverlayLayers(map)

      // --- Results grid layer (filled on analysis complete) ---
      map.addSource('results-grid', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })

      map.addLayer({
        id: 'results-cells',
        type: 'fill',
        source: 'results-grid',
        paint: {
          'fill-color': fillColorExpression('relative'),
          'fill-opacity': fillOpacityExpression('relative', 0.6),
        },
      })

      map.addLayer({
        id: 'results-cells-outline',
        type: 'line',
        source: 'results-grid',
        paint: {
          'line-color': '#ffffff',
          'line-width': 0.5,
          'line-opacity': 0.3,
        },
      })

      addNoveltyLayers(map)

      addLabelLayers(map)

      // Click handler for evidence drawer
      map.on('click', 'results-cells', (e) => {
        const feature = e.features?.[0]
        if (!feature) return
        const props = feature.properties as ScoredCell
        setSelectedCell({
          ...props,
          geometry: feature.geometry as GeoJSON.Geometry,
          evidence: JSON.parse((props.evidence as unknown as string) || '[]'),
          data_sources_used: JSON.parse(
            (props.data_sources_used as unknown as string) || '[]'
          ),
        })
      })

      map.on('mouseenter', 'results-cells', () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', 'results-cells', () => {
        map.getCanvas().style.cursor = ''
      })

      // Popups for the reference layers. Each layer names its own renderer:
      // the generic key/value dump is fine for a toponym or a field pin, but
      // useless for an occurrence record, where which fields are present is
      // itself the information.
      for (const [layerId, render] of Object.entries(POPUP_RENDERERS)) {
        map.on('click', layerId, (e) => {
          const f = e.features?.[0]
          if (!f) return
          // The district fill spans whole valleys and sits under the results
          // grid, so a click aimed at a scored cell would otherwise pop a
          // district too. Results win wherever they are drawn.
          if (
            BLOCKED_BY_RESULTS.has(layerId) &&
            map.getLayer('results-cells') &&
            map.queryRenderedFeatures(e.point, { layers: ['results-cells'] }).length > 0
          ) {
            return
          }
          new maplibregl.Popup({ closeButton: true, maxWidth: '320px' })
            .setLngLat(e.lngLat)
            .setHTML(render(f.properties as Record<string, unknown>))
            .addTo(map)
        })
      }

      setStyleReady(true)
    })

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
      drawRef.current = null
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Basemap switching — visibility only, so nothing else is torn down
  useEffect(() => {
    const map = mapRef.current
    if (!map || !styleReady) return
    const visible = new Set(visibleBasemapIds(basemap))
    for (const b of BASEMAPS) {
      const id = `basemap-${b.id}`
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, 'visibility', visible.has(b.id) ? 'visible' : 'none')
      }
    }
  }, [basemap, styleReady])

  // Update results grid layer when analysis results change
  useEffect(() => {
    const map = mapRef.current
    if (!map || !styleReady) return

    const source = map.getSource('results-grid') as maplibregl.GeoJSONSource
    if (!source) return

    const features: GeoJSON.Feature[] = analysisResults.map((cell) => ({
      type: 'Feature',
      id: cell.cell_id,
      geometry: cell.geometry,
      properties: {
        cell_id: cell.cell_id,
        score: cell.score,
        confidence: cell.confidence,
        relative_score: cell.relative_score ?? cell.score,
        percentile: cell.percentile ?? null,
        parent_cell_id: cell.parent_cell_id ?? null,
        tier: cell.tier ?? scoreTier(cell.score),
        // Absent on older runs and wherever the occurrence extract was never
        // built. null (not a string) so ['get','novelty'] matches no filter and
        // the cell simply gets no outline — unknown must never read as 'lead'.
        novelty: cell.novelty ?? null,
        nearest_occurrence_km: cell.nearest_occurrence_km ?? null,
        evidence: JSON.stringify(cell.evidence),
        data_sources_used: JSON.stringify(cell.data_sources_used),
      },
    }))

    source.setData({ type: 'FeatureCollection', features })

    // Thin (then drop) the outline as the grid gets dense — past a couple of
    // thousand cells the strokes dominate the fill they are meant to delimit.
    const n = features.length
    if (map.getLayer('results-cells-outline')) {
      map.setPaintProperty(
        'results-cells-outline',
        'line-opacity',
        n > OUTLINE_MAX_CELLS ? 0 : n > 600 ? 0.15 : 0.3
      )
      map.setPaintProperty('results-cells-outline', 'line-width', n > 600 ? 0.3 : 0.5)
    }

    // Novelty outlines are thinned on a dense grid but never dropped: unlike
    // the cell grid, which is only decoration once you can see the fill, this
    // is the only channel telling a novel hotspot from a re-discovery.
    for (const cls of NOVELTY_ORDER) {
      const id = NOVELTY_LAYERS[cls]
      if (!map.getLayer(id)) continue
      const w = NOVELTY[cls].width
      map.setPaintProperty(id, 'line-width', n > 600 ? w * 0.6 : w)
    }
  }, [analysisResults, styleReady])

  // Re-shade when the user toggles relative/absolute or moves the opacity slider
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.getLayer('results-cells')) return
    const scale = resultsVisible ? resultsOpacity : 0
    map.setPaintProperty('results-cells', 'fill-color', fillColorExpression(shadingMode))
    map.setPaintProperty(
      'results-cells',
      'fill-opacity',
      fillOpacityExpression(shadingMode, scale)
    )
    map.setLayoutProperty(
      'results-cells-outline',
      'visibility',
      resultsVisible ? 'visible' : 'none'
    )
    // Novelty rides on the results layer: hiding results hides its flags too,
    // otherwise you get outlines around cells whose scores are not on screen.
    const noveltyOn = resultsVisible && noveltyOutlines
    for (const cls of NOVELTY_ORDER) {
      const id = NOVELTY_LAYERS[cls]
      if (!map.getLayer(id)) continue
      map.setLayoutProperty(id, 'visibility', noveltyOn ? 'visible' : 'none')
    }
  }, [shadingMode, resultsOpacity, resultsVisible, noveltyOutlines, styleReady])

  // Overlay visibility + lazy data loading
  useEffect(() => {
    const map = mapRef.current
    if (!map || !styleReady) return
    for (const [id, on] of Object.entries(overlays) as [OverlayId, boolean][]) {
      for (const layerId of OVERLAY_LAYERS[id] ?? []) {
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(layerId, 'visibility', on ? 'visible' : 'none')
        }
      }
      if (on) void loadOverlayData(map, id)
    }
  }, [overlays, styleReady])

  // Reload cached coverage when a run finishes — it just gained cells
  useEffect(() => {
    const map = mapRef.current
    if (!map || !overlays.coverage) return
    void loadOverlayData(map, 'coverage', true)
  }, [analysisResults, overlays.coverage])

  // Keep the draw layer in sync with the store AOI:
  // - aoi cleared (run deleted) → remove the polygon from the map
  // - aoi set from run history → draw that run's polygon
  useEffect(() => {
    const draw = drawRef.current
    if (!draw) return
    const drawn = draw.getAll().features
    if (!aoi) {
      if (drawn.length > 0) draw.deleteAll()
      return
    }
    const alreadyDrawn = drawn.some((f) => f.id === aoi.id)
    if (!alreadyDrawn) {
      draw.deleteAll()
      draw.add(aoi as GeoJSON.Feature)
    }
  }, [aoi])

  // When isDrawing toggled from the panel, activate draw mode
  useEffect(() => {
    if (isDrawing && drawRef.current) {
      drawRef.current.deleteAll()
      drawRef.current.changeMode('draw_polygon')
    }
  }, [isDrawing])

  const handleJump = (e: React.FormEvent) => {
    e.preventDefault()
    const parsed = parseCoordinate(jumpTo)
    if (!parsed || !mapRef.current) {
      setJumpError(true)
      return
    }
    setJumpError(false)
    mapRef.current.flyTo({ center: [parsed.lng, parsed.lat], zoom: 13 })
  }

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainerRef} className="w-full h-full" />

      <LayerPanel />

      {/* Jump-to box — coordinates get transcribed out of USGS reports and old
          literature constantly, in whichever format the source used. */}
      <form
        onSubmit={handleJump}
        className="absolute top-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1"
      >
        <input
          value={jumpTo}
          onChange={(e) => {
            setJumpTo(e.target.value)
            setJumpError(false)
          }}
          placeholder={`47.6512, -121.5488  or  47°39'04"N 121°32'56"W`}
          className={`bg-gray-800/95 text-white text-xs px-3 py-1.5 rounded-l border w-72 placeholder:text-gray-500 ${
            jumpError ? 'border-red-500' : 'border-gray-600'
          }`}
          aria-label="Jump to coordinates"
        />
        <button
          type="submit"
          className="bg-gray-800/95 hover:bg-gray-700 text-white text-xs px-2.5 py-1.5 rounded-r border border-l-0 border-gray-600"
        >
          Go
        </button>
      </form>

      {/* AOI area indicator */}
      {aoi && (
        <div className="absolute top-12 left-1/2 -translate-x-1/2 bg-gray-800/90 text-white text-xs px-3 py-1.5 rounded-full border border-gray-600 pointer-events-none">
          AOI: {useAppStore.getState().aoiAreaKm2?.toFixed(1) ?? '?'} km²
        </div>
      )}

      {cursor && (
        <CoordinateReadout lng={cursor.lng} lat={cursor.lat} resolutionM={resolutionM} />
      )}
    </div>
  )
}

// --- Novelty outlines ------------------------------------------------------

/**
 * One line layer per novelty class, because `line-dasharray` is **not**
 * data-driven in MapLibre — it accepts zoom expressions only, so the dash
 * pattern cannot be a `['get', 'novelty']` match and each class needs its own
 * filtered layer.
 *
 * Ids do not follow the `<source>-<type>` convention as literally as
 * `results-cells-outline`; `results-novelty-<class>` reads better and stays
 * greppable against NOVELTY in types/index.ts.
 */
const NOVELTY_LAYERS: Record<NoveltyClass, string> = {
  lead: 'results-novelty-lead',
  extends: 'results-novelty-extends',
  confirms: 'results-novelty-confirms',
}

/** Legend border-style → map dash pattern. NoveltyMeta carries the CSS name so
 *  the legend swatch needs no second opinion about what the map drew. */
const DASH_FOR_STYLE: Record<'solid' | 'dashed' | 'dotted', [number, number] | null> = {
  solid: null,
  dashed: [3, 2],
  dotted: [1, 2],
}

/**
 * Novelty is drawn as an outline on top of the score fill, never as a change to
 * the fill ramp. They answer different questions — "how good does this look"
 * and "has anyone been here" — and collapsing them into one colour channel
 * would make a confirmed re-discovery indistinguishable from a fresh lead,
 * which is the entire problem this layer exists to fix.
 *
 * Cells whose `novelty` is null (older run, or no occurrence extract on disk)
 * match none of the three filters and get no outline at all.
 */
function addNoveltyLayers(map: maplibregl.Map) {
  for (const cls of NOVELTY_ORDER) {
    const meta = NOVELTY[cls]
    const dash = DASH_FOR_STYLE[meta.borderStyle]
    map.addLayer({
      id: NOVELTY_LAYERS[cls],
      type: 'line',
      source: 'results-grid',
      filter: filt(['==', ['get', 'novelty'], cls]),
      paint: {
        'line-color': meta.color,
        'line-width': meta.width,
        'line-opacity': 0.9,
        ...(dash ? { 'line-dasharray': dash } : {}),
      },
    })
  }
}

// --- Overlay wiring --------------------------------------------------------

const OVERLAY_LAYERS: Record<OverlayId, string[]> = {
  coverage: ['coverage-fill'],
  wilderness: ['wilderness-fill', 'wilderness-outline', 'wilderness-label'],
  plss: ['plss-raster'],
  districts: ['districts-fill', 'districts-outline', 'districts-label'],
  occurrences: ['occurrence-halo', 'occurrence-points', 'occurrence-labels'],
  iaml: ['iaml-points', 'iaml-labels'],
  user_sites: ['user-sites-new-ring', 'user-sites-points', 'user-sites-labels'],
  toponyms: ['toponym-lines', 'toponym-points', 'toponym-labels'],
}

/**
 * Overlay → endpoint, exhaustive by type: adding an OverlayId without a URL is
 * now a compile error. This used to be a ternary chain whose final `else`
 * fetched `/reference/occurrences`, so any id it did not recognise silently
 * pulled the occurrence layer and pushed it into the wrong source.
 */
const OVERLAY_URL: Record<OverlayId, string | null> = {
  plss: null, // raster WMS — nothing to fetch
  coverage: null, // bbox-dependent, handled separately in loadOverlayData
  wilderness: `${API_BASE}/reference/wilderness`,
  toponyms: `${API_BASE}/reference/toponyms`,
  occurrences: `${API_BASE}/reference/occurrences`,
  districts: `${API_BASE}/reference/districts`,
  iaml: `${API_BASE}/reference/iaml`,
  user_sites: `${API_BASE}/reference/user-sites`,
}

/** Overlay → GeoJSON source id. Identity except for `user_sites`, whose layer
 *  and source ids use the hyphenated spelling. */
const OVERLAY_SOURCE: Record<OverlayId, string | null> = {
  plss: null,
  coverage: 'coverage',
  wilderness: 'wilderness',
  toponyms: 'toponyms',
  occurrences: 'occurrences',
  districts: 'districts',
  iaml: 'iaml',
  user_sites: 'user-sites',
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

// --- Occurrence styling ----------------------------------------------------

/**
 * Evidence weight of an occurrence record: 2 = documented production,
 * 1 = assays on record, 0 = a bare recorded occurrence.
 *
 * WA DNR hands us this as two flags (PRODUCTION / ASSAYS), which is the same
 * distinction `knowledge/historical/gold.md` calls assay primacy — a machine
 * readable version of something the knowledge file currently asks the LLM to
 * infer. Drawing all three at one size would throw it away.
 */
const OCC_WEIGHT: unknown[] = [
  'case',
  ['==', ['get', 'production'], true], 2,
  ['==', ['get', 'assays'], true], 1,
  0,
]

/**
 * Assumed positional uncertainty per `accuracy_class`, in km. These are stated
 * assumptions, not published figures — WA DNR's LOCATION_ACCURACY field gives
 * words, not metres. What matters is the ordering and the order of magnitude:
 *
 *   survey            GPS or orthophoto                       0.02 km
 *   topo              plotted on a 7.5' quad                  0.15 km
 *   derived           back-computed from a location or legal
 *                     description (a ¼ section is ~400 m)     0.40 km
 *   variable          "coordinate accuracy highly variable" —
 *                     DNR states no bound at all              1.0 km
 *   district_centroid a district CENTRE, not a site           3.0 km
 *   unknown           unrecognised string                     1.0 km
 *
 * This is not an edge case: measured over the 3314 features in
 * data/reference/wa_occurrences.geojson, 2187 are `variable`, 505 `topo`,
 * 494 `derived`, 85 `survey` and 43 `district_centroid`. Two thirds of the
 * layer is a point DNR itself will not vouch for, and drawing those as crisp
 * dots would be the single most misleading thing this layer could do — so the
 * uncertainty gets drawn: see `occurrence-halo`.
 */
const ACCURACY_KM: unknown[] = [
  'match', ['get', 'accuracy_class'],
  'survey', 0.02,
  'topo', 0.15,
  'derived', 0.4,
  'variable', 1.0,
  'district_centroid', 3.0,
  1.0, // default — 'unknown' or a class we have not seen
]

/**
 * Pixels per kilometre at latitude 47.5° (mid-Washington):
 * metres/px = 156543.03 · cos(47.5°) / 2^z = 105_760 / 2^z, so px/km = 2^z / 105.76.
 *
 * Interpolating between these two stops with `['exponential', 2]` reproduces a
 * constant ground size exactly. MapLibre clamps outside the stop range, which
 * is the cap we want — a 3 km halo at z17 would be 1200 px of blue.
 */
const PX_PER_KM_Z9 = 4.84
const PX_PER_KM_Z14 = 154.9

// --- Layer construction ----------------------------------------------------

/** Fill/line/raster layers, added below the results grid. */
function addOverlayLayers(map: maplibregl.Map) {
  // Cached coverage — everything scored to date, across every run.
  map.addSource('coverage', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'coverage-fill',
    type: 'fill',
    source: 'coverage',
    layout: { visibility: 'none' },
    paint: {
      // Absolute score only. Relative shading has no common denominator across
      // AOIs, so a tier here would contradict the map it is drawn on.
      'fill-color': [
        'interpolate', ['linear'], ['get', 'score'],
        0.0, '#1e3a8a', 0.5, '#7c3aed', 1.0, '#f0abfc',
      ] as unknown as maplibregl.ExpressionSpecification,
      // Deliberately fainter than a live run so the two never read as the same
      // layer at a glance.
      'fill-opacity': 0.35,
    },
  })

  map.addSource('wilderness', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'wilderness-fill',
    type: 'fill',
    source: 'wilderness',
    layout: { visibility: 'none' },
    paint: { 'fill-color': '#22c55e', 'fill-opacity': 0.1 },
  })
  map.addLayer({
    id: 'wilderness-outline',
    type: 'line',
    source: 'wilderness',
    layout: { visibility: 'none' },
    paint: { 'line-color': '#16a34a', 'line-width': 1.5, 'line-dasharray': [3, 2] },
  })

  // WA DNR mining districts. Big polygons, so they sit under the results grid
  // and stay faint — this is context for "which camp am I in", not a signal.
  map.addSource('districts', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'districts-fill',
    type: 'fill',
    source: 'districts',
    layout: { visibility: 'none' },
    paint: {
      'fill-color': '#b45309', // amber-700 — mining brown, clear of the score ramp
      // A district with recorded production reads stronger than one that is
      // merely named. Prod_Amnt is free text in the DNR table, so the test is
      // "coerces to a non-empty value", not a number comparison.
      //
      // Measured 2026-08-12 against data/reference/wa_mining_districts.geojson:
      // production_amount is empty on all 68 districts, so today every district
      // draws at 0.06 and every popup says "no production recorded". The
      // encoding is kept because it is correct and will light up if that extract
      // gains the DNR Prod_Amnt column — it is not silently broken.
      'fill-opacity': expr(['case', ['to-boolean', ['get', 'production_amount']], 0.14, 0.06]),
    },
  })
  map.addLayer({
    id: 'districts-outline',
    type: 'line',
    source: 'districts',
    layout: { visibility: 'none' },
    paint: {
      'line-color': '#92400e',
      'line-width': 1.2,
      'line-opacity': 0.8,
      'line-dasharray': [4, 2],
    },
  })

  map.addSource('plss', PLSS_SOURCE)
  map.addLayer({
    id: 'plss-raster',
    type: 'raster',
    source: 'plss',
    minzoom: PLSS_MINZOOM,
    layout: { visibility: 'none' },
    paint: { 'raster-opacity': 0.7 },
  })

  map.addSource('toponyms', { type: 'geojson', data: EMPTY })
  map.addSource('toponym-extents', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'toponym-lines',
    type: 'line',
    source: 'toponym-extents',
    layout: { visibility: 'none' },
    paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-opacity': 0.5 },
  })

  map.addSource('occurrences', { type: 'geojson', data: EMPTY })

  /**
   * Positional-uncertainty halo, drawn as ground area rather than a fixed dot.
   *
   * It lives *under* the results grid on purpose: it is terrain context, and a
   * 1 km translucent disc painted over the scores would hide the thing you are
   * trying to read. The dot itself goes above (see addLabelLayers), so a site
   * stays clickable at any results opacity.
   *
   * minzoom 9 because statewide there are ~3300 of these and at z7 they merge
   * into one blue wash that says nothing.
   */
  map.addLayer({
    id: 'occurrence-halo',
    type: 'circle',
    source: 'occurrences',
    minzoom: 9,
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': expr([
        'interpolate', ['exponential', 2], ['zoom'],
        9, ['*', PX_PER_KM_Z9, ACCURACY_KM],
        14, ['*', PX_PER_KM_Z14, ACCURACY_KM],
      ]),
      'circle-color': '#1d4ed8',
      'circle-opacity': 0.1,
      'circle-stroke-color': '#1d4ed8',
      'circle-stroke-width': 0.5,
      'circle-stroke-opacity': 0.3,
    },
  })

  map.addSource('iaml', { type: 'geojson', data: EMPTY })
  map.addSource('user-sites', { type: 'geojson', data: EMPTY })
}

/** Point and label layers, added above the results grid so names stay legible. */
function addLabelLayers(map: maplibregl.Map) {
  map.addLayer({
    id: 'wilderness-label',
    type: 'symbol',
    source: 'wilderness',
    layout: {
      visibility: 'none',
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': 11,
      'text-allow-overlap': false,
    },
    paint: { 'text-color': '#166534', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 },
  })

  // District names read from z6 — a district is the unit the historical
  // literature is written in ("the Republic district"), so it is worth seeing
  // before you have zoomed in far enough to draw an AOI.
  map.addLayer({
    id: 'districts-label',
    type: 'symbol',
    source: 'districts',
    minzoom: 6,
    layout: {
      visibility: 'none',
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': 12,
      'text-transform': 'uppercase',
      'text-letter-spacing': 0.08,
      'text-allow-overlap': false,
    },
    paint: { 'text-color': '#7c2d12', 'text-halo-color': '#ffffff', 'text-halo-width': 1.8 },
  })

  /**
   * The occurrence dot. Radius and fill carry evidence weight (production >
   * assays > bare record); the stroke carries positional accuracy, and
   * `district_centroid` is drawn hollow because it is not a site at all — it is
   * a district centre wearing a site's coordinates, the exact failure mode
   * benchmarks/labels.yaml warns about.
   */
  map.addLayer({
    id: 'occurrence-points',
    type: 'circle',
    source: 'occurrences',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': expr([
        'interpolate', ['linear'], ['zoom'],
        7, ['+', 2.0, ['*', 0.7, OCC_WEIGHT]],
        12, ['+', 3.6, ['*', 1.5, OCC_WEIGHT]],
        16, ['+', 5.0, ['*', 2.0, OCC_WEIGHT]],
      ]),
      'circle-color': expr([
        'case',
        ['==', ['get', 'production'], true], '#1d4ed8', // blue-700 — produced
        ['==', ['get', 'assays'], true], '#3b82f6',     // blue-500 — assayed
        '#93c5fd',                                      // blue-300 — bare record
      ]),
      // Crisp = we know where it is. Washy = we do not.
      'circle-opacity': expr([
        'match', ['get', 'accuracy_class'],
        'survey', 0.95,
        'topo', 0.9,
        'derived', 0.7,
        'district_centroid', 0.0, // hollow ring
        0.45,
      ]),
      'circle-stroke-color': expr([
        'case',
        ['==', ['get', 'accuracy_class'], 'district_centroid'], '#1e3a8a',
        '#ffffff',
      ]),
      'circle-stroke-width': expr([
        'match', ['get', 'accuracy_class'],
        'survey', 1.4,
        'topo', 1.2,
        'derived', 1.0,
        'district_centroid', 1.6,
        0.8,
      ]),
      'circle-stroke-opacity': expr([
        'match', ['get', 'accuracy_class'],
        'survey', 1.0,
        'topo', 0.95,
        'derived', 0.7,
        'district_centroid', 0.9,
        0.4,
      ]),
    },
  })

  map.addLayer({
    id: 'occurrence-labels',
    type: 'symbol',
    source: 'occurrences',
    minzoom: 11,
    layout: {
      visibility: 'none',
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': 11,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-allow-overlap': false,
    },
    paint: { 'text-color': '#0c4a6e', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 },
  })

  /**
   * IAML — inactive and abandoned mine lands. This is where adits and shafts
   * are, i.e. physical holes you could stand in front of, so a recorded hazard
   * gets a yellow rim: it is the only attribute on this map with a safety
   * consequence.
   *
   * `hazard` is DNR free text, measured in data/reference/wa_iaml.geojson as
   * 'yes' (46 sites), 'unknown' (49), 'no' (2) and '' (all 358 features). Only
   * 'yes' earns the rim — a `to-boolean` test would flag 'no' and 'unknown' too,
   * which is worse than useless. **A white rim is therefore not a statement that
   * a site is safe**: half the sites were never assessed, and the popup shows
   * the raw value.
   */
  map.addLayer({
    id: 'iaml-points',
    type: 'circle',
    source: 'iaml',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': expr([
        'interpolate', ['linear'], ['zoom'],
        9, ['case', ['==', ['get', 'kind'], 'site'], 3.5, 2.5],
        14, ['case', ['==', ['get', 'kind'], 'site'], 7.0, 5.0],
      ]),
      'circle-color': expr([
        'case', ['==', ['get', 'kind'], 'site'], '#a21caf', '#d946ef',
      ]),
      'circle-stroke-color': expr([
        'case', ['==', ['get', 'hazard'], 'yes'], '#fde047', '#ffffff',
      ]),
      'circle-stroke-width': expr([
        'case', ['==', ['get', 'hazard'], 'yes'], 1.8, 1.0,
      ]),
      'circle-opacity': 0.9,
    },
  })

  map.addLayer({
    id: 'iaml-labels',
    type: 'symbol',
    source: 'iaml',
    minzoom: 12,
    layout: {
      visibility: 'none',
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': 10,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-allow-overlap': false,
    },
    paint: { 'text-color': '#701a75', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 },
  })

  /**
   * "Not in any database" ring — steps-2.0 §30.3. A field-visit pin more than
   * ~200 m from every DNR/MRDS record is an undocumented working located by
   * someone who went there, and it is the most interesting record in the
   * system. It gets the same cyan as a novelty `lead` cell, deliberately: in
   * this app cyan means "nothing recorded here".
   */
  map.addLayer({
    id: 'user-sites-new-ring',
    type: 'circle',
    source: 'user-sites',
    filter: filt(['==', ['get', 'potentially_new'], true]),
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': expr(['interpolate', ['linear'], ['zoom'], 9, 7, 14, 13]),
      'circle-opacity': 0,
      'circle-stroke-color': NOVELTY.lead.color,
      'circle-stroke-width': 2,
      'circle-stroke-opacity': 0.9,
    },
  })

  /**
   * The user's own pins. White core with a coloured rim so they never read as
   * one of the data layers.
   *
   * Radius encodes provenance, because §30.1's whole point is that "I stood
   * here" and "I read about this" are different evidence. Rim colour encodes
   * `role`: blue = evidence (this pin is in the agent prompts, same blue as the
   * occurrence layer), near-black = truth (benchmark ground truth — drawn here,
   * but `build_local_context` filters it out of every prompt), grey = display.
   */
  map.addLayer({
    id: 'user-sites-points',
    type: 'circle',
    source: 'user-sites',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': expr([
        'interpolate', ['linear'], ['zoom'],
        9, ['match', ['get', 'provenance'], 'field_visit', 4.5, 'literature', 3.2, 2.4],
        14, ['match', ['get', 'provenance'], 'field_visit', 9.0, 'literature', 6.0, 4.5],
      ]),
      'circle-color': '#ffffff',
      'circle-opacity': expr(['case', ['==', ['get', 'visited'], true], 1.0, 0.75]),
      'circle-stroke-color': expr([
        'match', ['get', 'role'],
        'evidence', '#1d4ed8',
        'truth', '#111827',
        '#525252', // display
      ]),
      'circle-stroke-width': expr([
        'match', ['get', 'provenance'],
        'field_visit', 2.2,
        'literature', 1.4,
        0.9, // inference / hearsay / unknown — deliberately faint
      ]),
      'circle-stroke-opacity': expr([
        'match', ['get', 'provenance'],
        'field_visit', 1.0,
        'literature', 0.85,
        0.5,
      ]),
    },
  })

  map.addLayer({
    id: 'user-sites-labels',
    type: 'symbol',
    source: 'user-sites',
    minzoom: 11,
    layout: {
      visibility: 'none',
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': 11,
      'text-offset': [0, 1.2],
      'text-anchor': 'top',
      'text-allow-overlap': false,
    },
    paint: { 'text-color': '#111827', 'text-halo-color': '#ffffff', 'text-halo-width': 2 },
  })

  map.addLayer({
    id: 'toponym-points',
    type: 'circle',
    source: 'toponyms',
    layout: { visibility: 'none' },
    paint: {
      // Tier 1 (direct workings) draws largest; tier 4 (gossan colour) smallest
      'circle-radius': [
        'interpolate', ['linear'], ['get', 'tier'], 1, 6, 4, 3.5,
      ] as unknown as maplibregl.ExpressionSpecification,
      'circle-color': ['get', 'color'],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1,
    },
  })

  map.addLayer({
    id: 'toponym-labels',
    type: 'symbol',
    source: 'toponyms',
    minzoom: 10,
    layout: {
      visibility: 'none',
      'text-field': ['get', 'name'],
      'text-font': LABEL_FONT,
      'text-size': 11,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-allow-overlap': false,
    },
    paint: { 'text-color': '#111827', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 },
  })
}

const loaded = new Set<string>()

async function loadOverlayData(map: maplibregl.Map, id: OverlayId, force = false) {
  if (id === 'plss') return // raster WMS, nothing to fetch
  if (loaded.has(id) && !force) return
  loaded.add(id)

  try {
    if (id === 'coverage') {
      const b = map.getBounds()
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',')
      const r = await fetch(`${API_BASE}/cache/cells?bbox=${bbox}`)
      const fc = await r.json()
      ;(map.getSource('coverage') as maplibregl.GeoJSONSource)?.setData(fc)
      return
    }

    const url = OVERLAY_URL[id]
    const sourceId = OVERLAY_SOURCE[id]
    if (!url || !sourceId) return

    const r = await fetch(url)
    // 404 is the normal answer for a reference file that was never built — the
    // LayerPanel greys the toggle out from /reference/layers, but a stale
    // localStorage pref can still ask for one. Forget the attempt so that
    // building the file and re-toggling picks it up without a page reload.
    if (!r.ok) {
      loaded.delete(id)
      return
    }
    const fc = (await r.json()) as GeoJSON.FeatureCollection
    ;(map.getSource(sourceId) as maplibregl.GeoJSONSource)?.setData(fc)

    if (id === 'toponyms') {
      // Draw streams along their length. GNIS locates a stream at its MOUTH,
      // which can be kilometres from whatever it was named for — a bare dot
      // there is actively misleading about where the name points.
      const lines: GeoJSON.Feature[] = []
      for (const f of fc.features) {
        const p = f.properties as Record<string, number | null> | null
        if (!p?.source_lat || !p?.source_lon) continue
        const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates
        lines.push({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [[lon, lat], [p.source_lon, p.source_lat]],
          },
          properties: { color: (f.properties as { color?: string })?.color ?? '#6b7280' },
        })
      }
      ;(map.getSource('toponym-extents') as maplibregl.GeoJSONSource)?.setData({
        type: 'FeatureCollection',
        features: lines,
      })
    }
  } catch {
    // A missing reference layer must never break the map
    loaded.delete(id)
  }
}

// --- Popups ----------------------------------------------------------------

const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!)
  )

/**
 * Only http(s) links are rendered as links.
 *
 * These URLs come out of a data file, and a `javascript:` value in a `href`
 * inside a popup would execute in the page. The scheme check is the whole
 * defence; escaping alone would not stop it.
 */
function safeUrl(v: unknown): string | null {
  const s = String(v ?? '').trim()
  return /^https?:\/\//i.test(s) ? s : null
}

interface ScannedDoc {
  title?: string
  author?: string
  date?: number | string | null
  type?: string
  url?: string
}

/**
 * `docs` is an array in the GeoJSON we serve, but MapLibre round-trips feature
 * properties through its vector-tile serializer, which has no array type and
 * JSON-stringifies anything non-scalar. Which of the two a click handler sees
 * depends on the source path, so accept both rather than discover the
 * difference the first time someone clicks a site with documents.
 */
function parseDocs(v: unknown): ScannedDoc[] {
  if (Array.isArray(v)) return v as ScannedDoc[]
  if (typeof v === 'string' && v.trim().startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(v)
      return Array.isArray(parsed) ? (parsed as ScannedDoc[]) : []
    } catch {
      return []
    }
  }
  return []
}

/** Flags survive as booleans through a GeoJSON source and as strings through a
 *  vector-tile round trip; treat both as true. */
const isTrue = (v: unknown) => v === true || v === 'true' || v === 1

const POPUP_WRAP = 'font-size:11px;line-height:1.45;max-height:340px;overflow-y:auto'
const MUTED = 'color:#6b7280'

/**
 * Occurrence popup — the acceptance criterion in steps-2.0 §32: name, commodity,
 * district, assay/production flags, location accuracy, and a link to the scanned
 * source document where one exists.
 *
 * Written by hand rather than dumped through `popupHtml` because for this record
 * *absence is information*: "no assay on record" is a real statement about a
 * site, and a key/value table that simply omits empty fields cannot make it.
 */
function occurrencePopupHtml(p: Record<string, unknown>): string {
  const s = (k: string) => String(p[k] ?? '').trim()
  const chip = (label: string, on: boolean) =>
    `<span style="display:inline-block;padding:1px 5px;border-radius:3px;margin-right:4px;` +
    `background:${on ? '#1d4ed8' : '#e5e7eb'};color:${on ? '#ffffff' : '#6b7280'}">` +
    `${esc(label)}</span>`

  const out: string[] = []
  out.push(
    `<div style="font-size:13px;font-weight:600;margin-bottom:2px">${esc(s('name') || 'Unnamed site')}</div>`
  )

  const where = [s('district') && `${esc(s('district'))} district`, s('county') && `${esc(s('county'))} Co.`]
    .filter(Boolean)
    .join(' · ')
  if (where) out.push(`<div style="${MUTED};margin-bottom:4px">${where}</div>`)

  if (s('commodity_primary') || s('commodities')) {
    out.push(
      `<div><b>${esc(s('commodity_primary') || '—')}</b>` +
        (s('commodities') ? `<span style="${MUTED}"> · ${esc(s('commodities'))}</span>` : '') +
        `</div>`
    )
  }

  // Evidence flags first: these are what the historical agent's assay-primacy
  // rule keys off, so they are the most decision-relevant thing in the record.
  out.push(
    `<div style="margin:5px 0">` +
      chip(isTrue(p.production) ? 'production' : 'no production', isTrue(p.production)) +
      chip(isTrue(p.assays) ? 'assays' : 'no assays', isTrue(p.assays)) +
      `</div>`
  )

  if (s('location_accuracy') || s('accuracy_class')) {
    out.push(
      `<div style="${MUTED}">Position: ${esc(s('location_accuracy') || s('accuracy_class'))}</div>`
    )
  }
  if (s('accuracy_class') === 'district_centroid') {
    out.push(
      `<div style="color:#b45309;margin-top:2px">` +
        `This point is a mining-district <b>centroid</b>, not a site location.</div>`
    )
  }
  if (s('legal_description')) {
    out.push(`<div style="${MUTED}">${esc(s('legal_description'))}</div>`)
  }

  const mineralogy = [s('ore_minerals'), s('gangue')].filter(Boolean).join(' / ')
  if (mineralogy) {
    out.push(`<div style="margin-top:4px">${esc(mineralogy)}</div>`)
  }
  if (s('comments')) {
    out.push(`<div style="margin-top:4px;${MUTED}">${esc(s('comments'))}</div>`)
  }

  // Scanned source documents — a direct path from a scored cell to primary
  // literature, which is the whole reason this layer beats MRDS.
  const docs = parseDocs(p.docs)
  const total = Number(p.doc_count ?? docs.length) || docs.length
  if (docs.length > 0) {
    const items = docs
      .map((d) => {
        const label = esc([d.title, d.author, d.date].filter(Boolean).join(' — ') || 'document')
        const href = safeUrl(d.url)
        return `<li>${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label}</li>`
      })
      .join('')
    out.push(
      `<div style="margin-top:5px"><b>Scanned documents</b>` +
        (total > docs.length ? `<span style="${MUTED}"> (${docs.length} of ${total})</span>` : '') +
        `<ul style="margin:2px 0 0 14px;padding:0">${items}</ul></div>`
    )
  } else if (total > 0) {
    out.push(`<div style="margin-top:5px;${MUTED}">${total} scanned document(s) on record</div>`)
  }

  if (s('location_source')) {
    out.push(`<div style="margin-top:4px;${MUTED}">Source: ${esc(s('location_source'))}</div>`)
  }
  return `<div style="${POPUP_WRAP}">${out.join('')}</div>`
}

/** District popup — production is the point of the layer, so it leads. */
function districtPopupHtml(p: Record<string, unknown>): string {
  const s = (k: string) => String(p[k] ?? '').trim()
  const out: string[] = []
  out.push(
    `<div style="font-size:13px;font-weight:600;margin-bottom:2px">${esc(s('name') || 'Unnamed district')}</div>`
  )
  const sub = [s('other_name'), s('county') && `${esc(s('county'))} Co.`].filter(Boolean).join(' · ')
  if (sub) out.push(`<div style="${MUTED};margin-bottom:4px">${sub}</div>`)

  if (s('commodity_primary')) out.push(`<div><b>${esc(s('commodity_primary'))}</b></div>`)
  if (s('deposit_type')) out.push(`<div>${esc(s('deposit_type'))}</div>`)

  const amount = [s('production_amount'), s('production_unit')].filter(Boolean).join(' ')
  if (amount || s('production_years')) {
    out.push(
      `<div style="margin-top:4px">Production: <b>${esc(amount || 'recorded, amount unstated')}</b>` +
        (s('production_years') ? `<span style="${MUTED}"> (${esc(s('production_years'))})</span>` : '') +
        `</div>`
    )
  } else {
    out.push(`<div style="margin-top:4px;${MUTED}">No production recorded in this table</div>`)
  }
  if (s('discovery')) out.push(`<div style="${MUTED}">Discovery: ${esc(s('discovery'))}</div>`)
  if (s('notes')) out.push(`<div style="margin-top:4px;${MUTED}">${esc(s('notes'))}</div>`)

  const links: string[] = []
  for (const [key, label] of [
    ['district_link', 'District record'],
    ['production_link', 'Production record'],
  ] as const) {
    const href = safeUrl(p[key])
    if (href) links.push(`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`)
  }
  if (s('citation')) links.push(`<span style="${MUTED}">${esc(s('citation'))}</span>`)
  if (links.length > 0) out.push(`<div style="margin-top:5px">${links.join(' · ')}</div>`)

  return `<div style="${POPUP_WRAP}">${out.join('')}</div>`
}

/**
 * Which renderer each clickable layer uses. The generic dump is right for
 * toponyms, IAML and the user's own pins — every field on those is short and
 * worth seeing verbatim.
 */
const POPUP_RENDERERS: Record<string, (p: Record<string, unknown>) => string> = {
  'toponym-points': popupHtml,
  'occurrence-points': occurrencePopupHtml,
  'districts-fill': districtPopupHtml,
  'iaml-points': popupHtml,
  'user-sites-points': popupHtml,
}

/** Layers whose click must yield to a scored cell drawn on top of them. */
const BLOCKED_BY_RESULTS = new Set(['districts-fill'])

function popupHtml(props: Record<string, unknown>): string {
  const rows = Object.entries(props)
    .filter(([k, v]) => v !== null && v !== '' && k !== 'color')
    .map(
      ([k, v]) =>
        `<tr><td style="color:#6b7280;padding-right:8px">${esc(k)}</td><td>${esc(v)}</td></tr>`
    )
    .join('')
  return `<table style="font-size:11px;border-collapse:collapse">${rows}</table>`
}

function scoreTier(score: number): string {
  if (score >= 0.65) return 'high'
  if (score >= 0.40) return 'medium'
  if (score >= 0.20) return 'low'
  return 'negligible'
}
