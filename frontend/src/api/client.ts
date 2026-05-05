/**
 * Typed API client for the GeoProspector backend.
 * All endpoints mirror the FastAPI routes in backend/app/api/.
 */
import type { AnalysisJob, Channel, SSEEvent } from '../types'

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

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
  onError?: (err: Error) => void
): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/v1/analysis/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')

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
    if (onError) onError(err instanceof Error ? err : new Error(String(err)))
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
