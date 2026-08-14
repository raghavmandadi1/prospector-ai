import { useCallback, useEffect, useRef, useState } from 'react'
import {
  runSweepTile,
  sweepsApi,
  type SweepManifest,
  type SweepPreview,
  type SweepSummary,
} from '../../api/client'
import { useAppStore } from '../../store'
import type { ScoredCell } from '../../types'

/**
 * Regional sweep manager (Workstream 5, §42).
 *
 * The client owns the tile loop deliberately: create the sweep, then POST each
 * pending tile in turn. Pause is "stop asking for the next tile" and needs no
 * server-side job registry; Stop aborts the in-flight tile, which closes the
 * response body and really does cancel the Anthropic calls.
 *
 * The cost of that choice is that closing the tab stops the sweep — mitigated
 * by the manifest, which is rewritten after every tile transition and puts an
 * interrupted tile back to `pending` rather than `failed`. Resuming re-runs at
 * most one tile, and the cell cache makes even that nearly free.
 */
export default function SweepPanel() {
  const {
    aoi,
    apiKey,
    targetMineral,
    enabledAgents,
    setAnalysisResults,
    setSelectedCell,
  } = useAppStore()

  // The store keeps agents as Record<id, enabled>; the API wants the ids.
  const activeAgents = Object.entries(enabledAgents)
    .filter(([, on]) => on)
    .map(([id]) => id)

  const [resolutionM, setResolutionM] = useState(2000)
  const [preview, setPreview] = useState<SweepPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [manifest, setManifest] = useState<SweepManifest | null>(null)
  const [history, setHistory] = useState<SweepSummary[]>([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [log, setLog] = useState<string[]>([])

  // `running` is also read inside the tile loop, which closes over its initial
  // value — a ref is the state the loop can actually see.
  const stopRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)

  const say = useCallback((line: string) => {
    setLog((l) => [...l.slice(-60), line])
  }, [])

  const refreshHistory = useCallback(async () => {
    try {
      const { sweeps } = await sweepsApi.list()
      setHistory(sweeps)
    } catch {
      /* history is a convenience; never let it break the panel */
    }
  }, [])

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  // --- preview -------------------------------------------------------------

  const regionGeometry = aoi?.geometry ?? null

  const doPreview = async () => {
    if (!regionGeometry) return
    setPreviewing(true)
    setError(null)
    try {
      const p = await sweepsApi.preview({
        region_geojson: regionGeometry,
        resolution_m: resolutionM,
        enabled_agents: activeAgents,
      })
      setPreview(p)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPreviewing(false)
    }
  }

  // Re-preview whenever the region or resolution changes, so the cost on
  // screen always belongs to the region on screen.
  useEffect(() => {
    setPreview(null)
    if (regionGeometry) void doPreview()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionGeometry, resolutionM, activeAgents.join(',')])

  // --- the tile loop -------------------------------------------------------

  const runTiles = useCallback(
    async (m: SweepManifest) => {
      setRunning(true)
      stopRef.current = false
      let current = m
      try {
        for (;;) {
          if (stopRef.current) {
            say('Paused — outstanding tiles left pending, Resume picks up here.')
            break
          }
          const next = current.tiles.find(
            (t) => t.status === 'pending' || t.status === 'running'
          )
          if (!next) break

          say(`Tile ${next.tile_id} (${next.cell_count} cells)…`)
          const ctrl = new AbortController()
          abortRef.current = ctrl
          await runSweepTile(
            current.sweep_id,
            next.tile_id,
            apiKey,
            (ev) => {
              if (ev.event === 'tile_complete') {
                const status = String(ev.status)
                say(
                  status === 'complete'
                    ? `  ✓ ${ev.cells_scored} cells — ${ev.remaining} tiles left`
                    : `  ✗ ${ev.reason ?? 'failed'}`
                )
              } else if (ev.event === 'sweep_complete') {
                say(`Sweep complete — ${ev.cells} cells normalized across the region.`)
              } else if (ev.event === 'error') {
                say(`  ! ${ev.message}`)
              }
            },
            (err) => say(`  ! ${err.message}`),
            ctrl.signal
          )
          abortRef.current = null
          // Re-read the manifest rather than mutating locally: the server owns
          // tile state, and a resumed sweep may have been touched elsewhere.
          current = await sweepsApi.get(current.sweep_id)
          setManifest(current)
        }
        if (current.status === 'complete' || current.tiles.every((t) => t.status === 'complete')) {
          await loadResults(current.sweep_id)
        }
      } catch (e) {
        if (!stopRef.current) setError(e instanceof Error ? e.message : String(e))
      } finally {
        setRunning(false)
        abortRef.current = null
        void refreshHistory()
      }
    },
    [apiKey, say, refreshHistory]
  )

  const loadResults = async (sweepId: string) => {
    const fc = await sweepsApi.cells(sweepId)
    const cells = fc.features.map(
      (f) => ({ ...f.properties, geometry: f.geometry }) as ScoredCell
    )
    setAnalysisResults(cells)
    setSelectedCell(null)
    say(
      `Loaded ${cells.length} cells onto the map${fc.partial ? ' (PARTIAL sweep)' : ''}.`
    )
  }

  const start = async () => {
    if (!regionGeometry || !apiKey) return
    setError(null)
    setLog([])
    try {
      const m = await sweepsApi.create({
        region_geojson: regionGeometry,
        resolution_m: resolutionM,
        target_mineral: targetMineral,
        enabled_agents: activeAgents,
        corridor_note: 'drawn in the sweep panel',
        confirm_large: true,
      })
      setManifest(m)
      say(`Sweep ${m.sweep_id}: ${m.tiles.length} tiles.`)
      await runTiles(m)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const pause = () => {
    stopRef.current = true
    abortRef.current?.abort()
  }

  const resume = async (sweepId: string) => {
    setError(null)
    const m = await sweepsApi.get(sweepId)
    setManifest(m)
    say(`Resuming ${sweepId} — ${m.tiles.filter((t) => t.status !== 'complete').length} tiles outstanding.`)
    await runTiles(m)
  }

  const view = async (sweepId: string) => {
    setError(null)
    try {
      const m = await sweepsApi.get(sweepId)
      setManifest(m)
      await loadResults(sweepId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async (sweepId: string) => {
    await sweepsApi.remove(sweepId)
    if (manifest?.sweep_id === sweepId) setManifest(null)
    void refreshHistory()
  }

  // --- render --------------------------------------------------------------

  const est = preview?.estimate
  const done = manifest?.tiles.filter((t) => t.status === 'complete').length ?? 0
  const total = manifest?.tiles.length ?? 0

  return (
    <div className="p-4 space-y-4 text-sm">
      <div>
        <h2 className="font-medium mb-1">Regional Sweep</h2>
        <p className="text-xs text-gray-400 leading-snug">
          Draw a region, then sweep it tile by tile. Every cell is ranked against
          the <span className="text-gray-200">whole sweep</span>, not against the
          tile it happened to land in.
        </p>
      </div>

      {!regionGeometry && (
        <p className="text-xs text-amber-400">
          Draw a region on the map first — the sweep uses the same polygon tool
          as a single analysis, with no minimum size.
        </p>
      )}

      {/* Resolution */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">
          Analysis resolution
        </label>
        <div className="flex gap-1">
          {[4000, 2000, 1000, 500].map((r) => (
            <button
              key={r}
              onClick={() => setResolutionM(r)}
              disabled={running}
              className={`flex-1 px-2 py-1 text-xs rounded ${
                resolutionM === r
                  ? 'bg-gray-200 text-gray-900 font-medium'
                  : 'bg-gray-700 text-gray-300 hover:text-white'
              } disabled:opacity-40`}
            >
              {r >= 1000 ? `${r / 1000} km` : `${r} m`}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-gray-500 mt-1">
          Coarse first for reconnaissance; the ladder nests, so a fine re-sweep of
          the best ground slots straight into the coarse map.
        </p>
      </div>

      {/* Preview */}
      {previewing && <p className="text-xs text-gray-400">Tiling region…</p>}
      {est && (
        <div className="rounded bg-gray-900/60 border border-gray-700 p-2 space-y-1">
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">Tiles</span>
            <span>{est.tiles}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">Cells scored</span>
            <span>{est.core_cells.toLocaleString()}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">LLM calls</span>
            <span>{est.llm_calls.toLocaleString()}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">Est. cost</span>
            <span className="text-amber-300">~${est.est_cost_usd.toFixed(2)}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">Est. time</span>
            <span>
              {est.est_seconds < 90
                ? `${Math.round(est.est_seconds)} s`
                : `${Math.round(est.est_seconds / 60)} min`}
            </span>
          </div>
          {est.raggedness_overhead > 1.2 && (
            <p className="text-[10px] text-gray-500 leading-snug pt-1 border-t border-gray-700">
              {est.raggedness_overhead}× the calls of a perfectly packed region
              ({est.ideal_llm_calls}), because the region's edges cut across tile
              blocks and a part-full tile still costs one call per agent. A
              slightly different outline can be materially cheaper.
            </p>
          )}
          <p className="text-[10px] text-gray-500 leading-snug pt-1 border-t border-gray-700">
            {est.basis === 'default'
              ? 'Order of magnitude only — from default per-batch figures and a hardcoded price table, not billing data.'
              : 'Based on measured per-batch figures from a real run; prices are still from a hardcoded table.'}
          </p>
        </div>
      )}

      {/* Controls */}
      <div className="flex gap-2">
        <button
          onClick={start}
          disabled={!regionGeometry || !apiKey || running || !est}
          className="flex-1 px-3 py-2 rounded bg-amber-600 hover:bg-amber-500 disabled:opacity-40 disabled:hover:bg-amber-600 text-sm font-medium"
        >
          {running ? 'Sweeping…' : 'Start sweep'}
        </button>
        {running && (
          <button
            onClick={pause}
            className="px-3 py-2 rounded bg-gray-700 hover:bg-gray-600 text-sm"
          >
            Pause
          </button>
        )}
      </div>
      {!apiKey && (
        <p className="text-[10px] text-amber-400">
          An API key is needed — set it in the Analysis tab.
        </p>
      )}

      {/* Progress */}
      {manifest && (
        <div className="space-y-2">
          <div className="flex justify-between text-xs">
            <span className="text-gray-400">
              {manifest.sweep_id.slice(0, 8)} · {manifest.status}
            </span>
            <span>
              {done}/{total} tiles
            </span>
          </div>
          <div className="h-1.5 bg-gray-700 rounded overflow-hidden">
            <div
              className="h-full bg-amber-500 transition-all"
              style={{ width: total ? `${(done / total) * 100}%` : '0%' }}
            />
          </div>
          <div className="grid grid-cols-10 gap-0.5">
            {manifest.tiles.map((t) => (
              <div
                key={t.tile_id}
                title={`${t.tile_id} · ${t.cell_count} cells · ${t.status}${
                  t.error ? ` · ${t.error}` : ''
                }`}
                className={`h-2 rounded-sm ${
                  t.status === 'complete'
                    ? 'bg-emerald-500'
                    : t.status === 'failed'
                    ? 'bg-red-500'
                    : t.status === 'running'
                    ? 'bg-amber-400 animate-pulse'
                    : 'bg-gray-600'
                }`}
              />
            ))}
          </div>
          {manifest.status === 'complete' && (
            <a
              href={sweepsApi.csvUrl(manifest.sweep_id)}
              className="block text-center px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-xs"
            >
              Download ranked CSV
            </a>
          )}
        </div>
      )}

      {error && (
        <p className="text-xs text-red-400 leading-snug break-words">{error}</p>
      )}

      {/* Log */}
      {log.length > 0 && (
        <div className="rounded bg-black/40 border border-gray-700 p-2 max-h-40 overflow-y-auto font-mono text-[10px] leading-relaxed text-gray-300">
          {log.map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-gray-400 mb-1">Past sweeps</h3>
          <div className="space-y-1">
            {history.slice(0, 8).map((s) => (
              <div
                key={s.sweep_id}
                className="flex items-center gap-1 text-xs bg-gray-900/50 rounded px-2 py-1"
              >
                <span className="flex-1 truncate">
                  <span className="text-gray-500">{s.sweep_id.slice(0, 8)}</span>{' '}
                  {s.totals?.complete ?? 0}/{s.totals?.tiles ?? 0} ·{' '}
                  <span
                    className={
                      s.status === 'complete'
                        ? 'text-emerald-400'
                        : s.status === 'partial' || s.status === 'cancelled'
                        ? 'text-amber-400'
                        : 'text-gray-400'
                    }
                  >
                    {s.status}
                  </span>
                </span>
                <button
                  onClick={() => void view(s.sweep_id)}
                  className="px-1.5 py-0.5 rounded bg-gray-700 hover:bg-gray-600"
                >
                  View
                </button>
                {s.resumable && !running && (
                  <button
                    onClick={() => void resume(s.sweep_id)}
                    disabled={!apiKey}
                    className="px-1.5 py-0.5 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40"
                  >
                    Resume
                  </button>
                )}
                <button
                  onClick={() => void remove(s.sweep_id)}
                  className="px-1.5 py-0.5 rounded bg-gray-700 hover:bg-red-700"
                  title="Delete this sweep"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
