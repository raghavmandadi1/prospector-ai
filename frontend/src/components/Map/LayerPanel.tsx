import { useEffect, useState } from 'react'
import { useAppStore } from '../../store'
import type { OverlayId } from '../../store'
import { BASEMAPS } from './basemaps'
import { referenceApi } from '../../api/client'

interface OverlayDef {
  id: OverlayId
  label: string
  hint: string
  /** Key in /reference/layers that must be true for this to be offered. */
  requires?: string
}

const OVERLAYS: OverlayDef[] = [
  {
    id: 'plss',
    label: 'PLSS sections',
    hint: 'Township / range / section — how claims are described',
  },
  {
    id: 'toponyms',
    label: 'Mining place names',
    hint: 'GNIS names matching the toponym lexicon',
    requires: 'toponyms',
  },
  {
    id: 'occurrences',
    label: 'Known occurrences',
    hint: 'MRDS / WA DNR recorded sites',
    requires: 'occurrences',
  },
  {
    id: 'wilderness',
    label: 'Wilderness areas',
    hint: 'Advisory only — not a mineral-entry determination',
    requires: 'wilderness',
  },
  {
    id: 'coverage',
    label: 'Cached coverage',
    hint: 'Everything scored to date — absolute score',
  },
]

export default function LayerPanel() {
  const {
    basemap, setBasemap,
    resultsOpacity, setResultsOpacity,
    resultsVisible, setResultsVisible,
    overlays, toggleOverlay,
    availableLayers, setAvailableLayers,
    analysisResults,
  } = useAppStore()
  const [open, setOpen] = useState(false)

  // Ask the backend which reference layers exist on disk. A toggle for a layer
  // that was never built would just yield an empty map.
  useEffect(() => {
    let cancelled = false
    referenceApi
      .layers()
      .then((l) => !cancelled && setAvailableLayers(l))
      .catch(() => !cancelled && setAvailableLayers({}))
    return () => {
      cancelled = true
    }
  }, [setAvailableLayers])

  const activeCount =
    (resultsVisible && analysisResults.length > 0 ? 1 : 0) +
    Object.values(overlays).filter(Boolean).length

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="absolute top-3 right-14 z-10 bg-gray-800/95 hover:bg-gray-700 text-white text-xs px-3 py-2 rounded border border-gray-600 shadow-lg"
        title="Layers"
      >
        Layers{activeCount > 0 ? ` · ${activeCount}` : ''}
      </button>
    )
  }

  return (
    <div className="absolute top-3 right-14 z-10 w-64 bg-gray-800/95 text-gray-100 text-xs rounded border border-gray-600 shadow-lg">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
        <span className="font-semibold">Layers</span>
        <button
          onClick={() => setOpen(false)}
          className="text-gray-400 hover:text-white px-1"
          aria-label="Close layer panel"
        >
          ×
        </button>
      </div>

      <section className="px-3 py-2 border-b border-gray-700">
        <div className="text-gray-400 uppercase tracking-wide mb-1.5 text-[10px]">
          Basemap
        </div>
        {BASEMAPS.map((b) => (
          <label
            key={b.id}
            className="flex items-start gap-2 py-1 cursor-pointer hover:bg-gray-700/40 rounded px-1 -mx-1"
          >
            <input
              type="radio"
              name="basemap"
              checked={basemap === b.id}
              onChange={() => setBasemap(b.id)}
              className="mt-0.5"
            />
            <span>
              <span className="block">{b.label}</span>
              <span className="block text-[10px] text-gray-400">{b.hint}</span>
            </span>
          </label>
        ))}
      </section>

      <section className="px-3 py-2 border-b border-gray-700">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={resultsVisible}
            onChange={(e) => setResultsVisible(e.target.checked)}
          />
          <span className="font-medium">Results</span>
          <span className="ml-auto text-gray-400 tabular-nums">
            {Math.round(resultsOpacity * 100)}%
          </span>
        </label>
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round(resultsOpacity * 100)}
          onChange={(e) => setResultsOpacity(Number(e.target.value) / 100)}
          disabled={!resultsVisible}
          className="w-full mt-1.5 accent-orange-500 disabled:opacity-40"
          aria-label="Results opacity"
        />
        <div className="text-[10px] text-gray-400 mt-0.5">
          Fade results to read the terrain underneath
        </div>
      </section>

      <section className="px-3 py-2">
        <div className="text-gray-400 uppercase tracking-wide mb-1.5 text-[10px]">
          Overlays
        </div>
        {OVERLAYS.map((o) => {
          const missing =
            o.requires !== undefined &&
            availableLayers !== null &&
            !availableLayers[o.requires]
          return (
            <label
              key={o.id}
              className={`flex items-start gap-2 py-1 rounded px-1 -mx-1 ${
                missing ? 'opacity-40' : 'cursor-pointer hover:bg-gray-700/40'
              }`}
              title={missing ? 'Not built on this install' : o.hint}
            >
              <input
                type="checkbox"
                checked={!!overlays[o.id] && !missing}
                disabled={missing}
                onChange={(e) => toggleOverlay(o.id, e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="block">{o.label}</span>
                <span className="block text-[10px] text-gray-400">
                  {missing ? 'not built — see scripts/' : o.hint}
                </span>
              </span>
            </label>
          )
        })}
      </section>
    </div>
  )
}
