import React, { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import { useAppStore } from '../../store'
import type { ScoredCell } from '../../types'

const TILESERVER_URL = import.meta.env.VITE_TILESERVER_URL ?? 'http://localhost:3000'

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
  const { analysisResults, setSelectedCell, aoi } = useAppStore()

  // Initialize map
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: 'https://demotiles.maplibre.org/style.json',
      center: [-105, 40],  // Default center: western US
      zoom: 6,
    })

    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(new maplibregl.ScaleControl(), 'bottom-right')

    map.on('load', () => {
      // --- Features layer from Martin tileserver ---
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
          'circle-color': '#60a5fa',  // blue-400
          'circle-opacity': 0.8,
          'circle-stroke-color': '#1e40af',
          'circle-stroke-width': 1,
        },
      })

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

      // --- AOI layer stub ---
      map.addSource('aoi-source', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })

      map.addLayer({
        id: 'aoi-fill',
        type: 'fill',
        source: 'aoi-source',
        paint: { 'fill-color': '#3b82f6', 'fill-opacity': 0.1 },
      })

      map.addLayer({
        id: 'aoi-outline',
        type: 'line',
        source: 'aoi-source',
        paint: { 'line-color': '#3b82f6', 'line-width': 2 },
      })
    })

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
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

  // Update AOI layer when aoi changes
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    const source = map.getSource('aoi-source') as maplibregl.GeoJSONSource
    if (!source) return
    if (aoi) {
      source.setData({ type: 'FeatureCollection', features: [aoi] })
    } else {
      source.setData({ type: 'FeatureCollection', features: [] })
    }
  }, [aoi])

  return (
    <div ref={mapContainerRef} className="w-full h-full" />
  )
}

function scoreTier(score: number): string {
  if (score >= 0.65) return 'high'
  if (score >= 0.40) return 'medium'
  if (score >= 0.20) return 'low'
  return 'negligible'
}
