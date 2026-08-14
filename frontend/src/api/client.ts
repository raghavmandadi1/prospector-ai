/**
 * Typed API client for the GeoProspector backend.
 * All endpoints mirror the FastAPI routes in backend/app/api/.
 */
import type { AnalysisJob, Channel, ScoredCell, SSEEvent } from '../types'

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

/** Route prefix for the map's direct fetches (reference overlays, cached coverage). */
export const API_BASE = `${BASE_URL}/api/v1`

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Reference overlays and cached coverage
//
// These read files on disk (a GeoJSON extract, the GNIS TSV, the SQLite cache)
// rather than Postgres, so unlike /channels and /features they work under
// DEV_MODE=true.
// ---------------------------------------------------------------------------

export const referenceApi = {
  /** Which reference layers this install actually has built. */
  layers: () =>
    request<Record<string, boolean>>('/api/v1/reference/layers'),

  cacheStats: () =>
    request<{ available: boolean; rows?: number; cells?: number }>(
      '/api/v1/cache/stats'
    ),
}

// ---------------------------------------------------------------------------
// Channels
// ---------------------------------------------------------------------------

export const channelsApi = {
  list: () => request<Channel[]>('/api/v1/channels'),

  create: (body: {
    name: string
    source_type: string
    endpoint?: string
    data_type?: string
    auth_config?: Record<string, string>
    spatial_coverage?: Record<string, unknown>
    refresh_schedule?: string
  }) =>
    request<Channel>('/api/v1/channels', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  sync: (channelId: string) =>
    request<{ task_id: string; channel_id: string; status: string }>(
      `/api/v1/channels/${channelId}/sync`,
      { method: 'POST' }
    ),
}

// ---------------------------------------------------------------------------
// Features
// ---------------------------------------------------------------------------

export const featuresApi = {
  list: (params: {
    bbox?: string
    commodity?: string
    feature_type?: string
    limit?: number
    offset?: number
  }) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)])
      )
    )
    return request<GeoJSON.FeatureCollection>(`/api/v1/features?${qs}`)
  },

  get: (id: string) =>
    request<GeoJSON.Feature>(`/api/v1/features/${id}`),
}

// ---------------------------------------------------------------------------
// Analysis Jobs
// ---------------------------------------------------------------------------

export const analysisApi = {
  createJob: (body: {
    aoi_geojson: GeoJSON.FeatureCollection
    target_mineral: string
    config?: {
      resolution_m?: number
      weights?: Record<string, number>
      enabled_agents?: string[]
    }
    anthropic_api_key?: string
  }) =>
    request<AnalysisJob>('/api/v1/analysis/jobs', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getJob: (jobId: string) =>
    request<AnalysisJob>(`/api/v1/analysis/jobs/${jobId}`),
}

// ---------------------------------------------------------------------------
// Dev-mode: stream analysis via POST response
// ---------------------------------------------------------------------------

/**
 * Run analysis in dev mode. The POST response itself is an SSE stream.
 * No separate job ID or EventSource subscription needed.
 */
/** Thrown-error shape for a run the user stopped, so callers can tell an
 *  intentional stop apart from a dropped connection. */
export function isAbortError(err: unknown): boolean {
  return (
    err instanceof DOMException && err.name === 'AbortError'
  ) || (err instanceof Error && err.name === 'AbortError')
}

export async function runAnalysisDev(
  body: {
    aoi_geojson: GeoJSON.FeatureCollection
    target_mineral: string
    config?: {
      resolution_m?: number
      weights?: Record<string, number>
      enabled_agents?: string[]
    }
    anthropic_api_key: string
  },
  onEvent: (event: SSEEvent & { final_scores?: unknown; agent_results?: unknown }) => void,
  onError?: (err: Error) => void,
  /** Abort to stop the run. Closing the response body is what the backend
   *  watches for; it cancels the orchestrator task on its next poll. */
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/v1/analysis/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')

  // fetch's own abort handling does not always reject a read() already in
  // flight, so cancel the reader explicitly to unblock the loop immediately.
  const onAbort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', onAbort, { once: true })

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Parse SSE lines from buffer
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? '' // Keep incomplete line in buffer

      for (const line of lines) {
        const trimmed = line.trim()
        // ": keepalive" comment frames are heartbeats — ignored here, but
        // they are what makes the server notice a dead socket.
        if (trimmed.startsWith('data: ')) {
          try {
            const parsed = JSON.parse(trimmed.slice(6))
            onEvent(parsed)
          } catch {
            // Ignore malformed JSON
          }
        }
      }
    }
  } catch (err) {
    // A stopped run is not an error — swallow it and let the caller's abort
    // handler own the UI state.
    if (isAbortError(err) || signal?.aborted) return
    if (onError) onError(err instanceof Error ? err : new Error(String(err)))
  } finally {
    signal?.removeEventListener('abort', onAbort)
  }
}

// ---------------------------------------------------------------------------
// Regional sweeps (Workstream 5)
//
// Mode-independent, like the reference layers: manifests and merged cells are
// files under data/sweeps/, so these work under DEV_MODE and in production.
//
// The client drives the tile loop — create the sweep, then POST each pending
// tile in turn. That is what makes Pause free (stop asking for the next one)
// and lets each tile reuse the same real cancellation as a single-AOI run.
// ---------------------------------------------------------------------------

export interface SweepEstimate {
  tiles: number
  core_cells: number
  context_cells: number
  agents: number
  llm_calls: number
  ideal_llm_calls: number
  raggedness_overhead: number
  input_tokens: number
  output_tokens: number
  est_cost_usd: number
  est_seconds: number
  model: string
  basis: 'default' | 'measured'
  pricing_note: string
  tile_cell_counts: number[]
}

export interface SweepTile {
  tile_id: string
  status: 'pending' | 'running' | 'complete' | 'failed'
  cell_count: number
  prompt_cell_count: number
  resolution_m: number
  cells_scored: number
  error: string | null
  usage: Record<string, number>
}

export interface SweepManifest {
  sweep_id: string
  status: 'pending' | 'running' | 'complete' | 'partial' | 'cancelled' | 'failed'
  created_at: string
  updated_at: string
  inputs: Record<string, unknown>
  estimate: SweepEstimate
  tiles: SweepTile[]
  totals: Record<string, number | string | boolean>
  error: string | null
}

export interface SweepSummary {
  sweep_id: string
  status: SweepManifest['status']
  created_at: string
  updated_at: string
  target_mineral?: string
  resolution_m?: number
  totals: Record<string, number>
  resumable: boolean
}

export interface SweepPreview {
  tiles: Array<{ tile_id: string; cell_count: number; prompt_cell_count: number }>
  tile_geometries: Record<string, GeoJSON.Geometry>
  estimate: SweepEstimate
  needs_confirmation: boolean
  max_tiles_without_confirmation: number
}

export const sweepsApi = {
  preview: (body: {
    region_geojson: GeoJSON.Geometry
    resolution_m: number
    enabled_agents?: string[]
    cache_hit_fraction?: number
  }) =>
    request<SweepPreview>('/api/v1/sweeps/preview', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  create: (body: {
    region_geojson: GeoJSON.Geometry
    resolution_m: number
    target_mineral: string
    enabled_agents?: string[]
    weights?: Record<string, number>
    corridor_note?: string
    confirm_large?: boolean
  }) =>
    request<SweepManifest>('/api/v1/sweeps', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  list: () => request<{ sweeps: SweepSummary[] }>('/api/v1/sweeps'),

  get: (id: string) => request<SweepManifest>(`/api/v1/sweeps/${id}`),

  remove: (id: string) =>
    request<{ deleted: string }>(`/api/v1/sweeps/${id}`, { method: 'DELETE' }),

  cancel: (id: string) =>
    request<SweepManifest>(`/api/v1/sweeps/${id}/cancel`, { method: 'POST' }),

  cells: (id: string, params?: { min_percentile?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.min_percentile != null) q.set('min_percentile', String(params.min_percentile))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<{
      type: 'FeatureCollection'
      sweep_id: string
      count: number
      partial: boolean
      features: Array<{ type: 'Feature'; geometry: GeoJSON.Geometry; properties: ScoredCell }>
    }>(`/api/v1/sweeps/${id}/cells${qs ? `?${qs}` : ''}`)
  },

  /** Download URL for the ranked CSV — the sweep's actual deliverable. */
  csvUrl: (id: string, minPercentile = 0) =>
    `${BASE_URL}/api/v1/sweeps/${id}/cells.csv?min_percentile=${minPercentile}`,

  refine: (id: string, body: { fine_resolution_m: number; top_n?: number; confirm_large?: boolean }) =>
    request<SweepManifest>(`/api/v1/sweeps/${id}/refine`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  diff: (a: string, b: string, noiseFloor?: number) => {
    const qs = noiseFloor != null ? `?noise_floor=${noiseFloor}` : ''
    return request<SweepDiff>(`/api/v1/sweeps/${a}/diff/${b}${qs}`)
  },
}

export interface SweepDiff {
  sweep_a: string
  sweep_b: string
  summary: {
    n_common: number
    n_only_a: number
    n_only_b: number
    mean_delta: number
    mean_abs_delta: number
    max_gain: number
    max_loss: number
    moved_up: number
    moved_down: number
    unchanged: number
    tier_changes: number
    noise_floor: number | null
    significant: number | null
    interpretation_note: string
  }
  cells: Array<{
    cell_id: string
    score_a: number
    score_b: number
    delta: number
    tier_a: string | null
    tier_b: string | null
    tier_changed: boolean
    significant: boolean | null
  }>
  only_in_a: string[]
  only_in_b: string[]
}

/**
 * Run one tile of a sweep, streaming its SSE events.
 *
 * Same transport and same abort semantics as `runAnalysisDev` — aborting the
 * signal closes the response body, which is what the backend watches for, and
 * it cancels the in-flight Anthropic calls rather than letting them finish.
 */
export async function runSweepTile(
  sweepId: string,
  tileId: string,
  apiKey: string,
  onEvent: (event: SSEEvent & Record<string, unknown>) => void,
  onError?: (err: Error) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(
    `${BASE_URL}/api/v1/sweeps/${sweepId}/tiles/${tileId}/run`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anthropic_api_key: apiKey }),
      signal,
    }
  )
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  await consumeSSE(res, onEvent, onError, signal)
}

/** Shared SSE line reader. Extracted so the sweep path cannot drift from the
 *  single-AOI path in how it handles aborts, partial frames or keepalives. */
async function consumeSSE<T>(
  res: Response,
  onEvent: (event: T) => void,
  onError?: (err: Error) => void,
  signal?: AbortSignal
): Promise<void> {
  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')
  const onAbort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', onAbort, { once: true })

  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(trimmed.slice(6)) as T)
          } catch {
            // Ignore malformed JSON
          }
        }
      }
    }
  } catch (err) {
    if (isAbortError(err) || signal?.aborted) return
    if (onError) onError(err instanceof Error ? err : new Error(String(err)))
  } finally {
    signal?.removeEventListener('abort', onAbort)
  }
}

// ---------------------------------------------------------------------------
// SSE hook for job progress (production mode)
// ---------------------------------------------------------------------------

/**
 * Subscribe to real-time agent progress events for a job.
 * Used in production mode (Celery + Redis).
 */
export function subscribeToJobEvents(
  jobId: string,
  onEvent: (event: SSEEvent) => void,
  onError?: (err: Event) => void
): () => void {
  const es = new EventSource(
    `${BASE_URL}/api/v1/analysis/jobs/${jobId}/events`
  )

  es.onmessage = (e) => {
    try {
      const parsed: SSEEvent = JSON.parse(e.data)
      onEvent(parsed)
      if (parsed.event === 'job_complete' || parsed.event === 'error') {
        es.close()
      }
    } catch {
      // ignore malformed events
    }
  }

  if (onError) {
    es.onerror = onError
  }

  return () => es.close()
}
