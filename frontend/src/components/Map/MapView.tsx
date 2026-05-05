import { useEffect, useRef, useCallback } from 'react'
import maplibregl from 'maplibre-gl'
import MapboxDraw from '@mapbox/mapbox-gl-draw'
import area from '@turf/area'
import { useAppStore } from '../../store'
import type { ScoredCell } from '../../types'

// mapbox-gl-draw CSS (inline the essential styles so we don't need a separate import)
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css'

const TILESERVER_URL = import.meta.env.VITE_TILESERVER_URL as string | undefined

// Minimum AOI area in square meters (~25 km²)
const MIN_AOI_AREA_M2 = 25_000_000

// Inline OSM raster basemap — no API key, no tileserver required.
const OSM_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: [
        'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
        'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
        'https://c.tile.openstreetmap.org/{z}/{x}/{y}.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
      maxzoom: 19,
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

// Tier color scale
const TIER_COLORS: Record<string, string> = {
  high: '#ef4444',      // red-500
  medium: '#f97316',    // orange-500
  low: '#eab308',       // yellow-500
  negligible: '#6b7280', // gray-500
}

export default function MapView() {
  const mapContainerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const drawRef = useRef<MapboxDraw | null>(null)
  const {
    analysisResults, setSelectedCell,
    aoi, setAoi,
    isDrawing, setIsDrawing,
    setAoiAreaKm2,
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

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: OSM_STYLE,
      center: [-120.5, 47.5],  // Center on Washington State
      zoom: 7,
    })

    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(new maplibregl.ScaleControl(), 'bottom-right')

    // Initialize draw control
    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: {
        polygon: true,
        trash: true,
      },
      defaultMode: 'simple_select',
    })

    // MapboxDraw works with MapLibre via the mapbox-gl compatibility
    map.addControl(draw as unknown as maplibregl.IControl, 'top-left')
    drawRef.current = draw

    map.on('draw.create', handleDrawCreate)
    map.on('draw.update', handleDrawUpdate)
    map.on('draw.delete', handleDrawDelete)

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
          'fill-color': [
            'match',
            ['get', 'tier'],
            'high', TIER_COLORS.high,
            'medium', TIER_COLORS.medium,
            'low', TIER_COLORS.low,
            TIER_COLORS.negligible,
          ],
          'fill-opacity': 0.6,
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
    })

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
      drawRef.current = null
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Update results grid layer when analysis results change
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return

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
        tier: cell.tier ?? scoreTier(cell.score),
        evidence: JSON.stringify(cell.evidence),
        data_sources_used: JSON.stringify(cell.data_sources_used),
      },
    }))

    source.setData({ type: 'FeatureCollection', features })
  }, [analysisResults])

  // When isDrawing toggled from the panel, activate draw mode
  useEffect(() => {
    if (isDrawing && drawRef.current) {
      drawRef.current.deleteAll()
      drawRef.current.changeMode('draw_polygon')
    }
  }, [isDrawing])

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainerRef} className="w-full h-full" />
      {/* AOI area indicator */}
      {aoi && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-gray-800/90 text-white text-xs px-3 py-1.5 rounded-full border border-gray-600 pointer-events-none">
          AOI: {useAppStore.getState().aoiAreaKm2?.toFixed(1) ?? '?'} km²
        </div>
      )}
    </div>
  )
}

function scoreTier(score: number): string {
  if (score >= 0.65) return 'high'
  if (score >= 0.40) return 'medium'
  if (score >= 0.20) return 'low'
  return 'negligible'
}
