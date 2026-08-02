import { useEffect, useRef, useCallback, useState } from 'react'
import maplibregl from 'maplibre-gl'
import MapboxDraw from '@mapbox/mapbox-gl-draw'
import area from '@turf/area'
import { useAppStore } from '../../store'
import type { OverlayId } from '../../store'
import type { ScoredCell } from '../../types'
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
    resultsOpacity, resultsVisible,
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

      // Popups for the reference point layers
      for (const id of ['toponym-points', 'occurrence-points']) {
        map.on('click', id, (e) => {
          const f = e.features?.[0]
          if (!f) return
          new maplibregl.Popup({ closeButton: true, maxWidth: '280px' })
            .setLngLat(e.lngLat)
            .setHTML(popupHtml(f.properties as Record<string, unknown>))
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
        evidence: JSON.stringify(cell.evidence),
        data_sources_used: JSON.stringify(cell.data_sources_used),
      },
    }))

    source.setData({ type: 'FeatureCollection', features })

    // Thin (then drop) the outline as the grid gets dense — past a couple of
    // thousand cells the strokes dominate the fill they are meant to delimit.
    if (map.getLayer('results-cells-outline')) {
      const n = features.length
      map.setPaintProperty(
        'results-cells-outline',
        'line-opacity',
        n > OUTLINE_MAX_CELLS ? 0 : n > 600 ? 0.15 : 0.3
      )
      map.setPaintProperty('results-cells-outline', 'line-width', n > 600 ? 0.3 : 0.5)
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
  }, [shadingMode, resultsOpacity, resultsVisible, styleReady])

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

// --- Overlay wiring --------------------------------------------------------

const OVERLAY_LAYERS: Record<OverlayId, string[]> = {
  coverage: ['coverage-fill'],
  wilderness: ['wilderness-fill', 'wilderness-outline', 'wilderness-label'],
  plss: ['plss-raster'],
  occurrences: ['occurrence-points'],
  toponyms: ['toponym-lines', 'toponym-points', 'toponym-labels'],
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

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

  map.addLayer({
    id: 'occurrence-points',
    type: 'circle',
    source: 'occurrences',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 4,
      'circle-color': '#0ea5e9',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1,
    },
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

    const url =
      id === 'wilderness'
        ? `${API_BASE}/reference/wilderness`
        : id === 'toponyms'
        ? `${API_BASE}/reference/toponyms`
        : `${API_BASE}/reference/occurrences`

    const r = await fetch(url)
    if (!r.ok) return
    const fc = (await r.json()) as GeoJSON.FeatureCollection
    ;(map.getSource(id) as maplibregl.GeoJSONSource)?.setData(fc)

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

function popupHtml(props: Record<string, unknown>): string {
  const esc = (v: unknown) =>
    String(v ?? '').replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!)
    )
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
