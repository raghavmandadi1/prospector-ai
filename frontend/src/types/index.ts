// Shared TypeScript types mirroring backend Pydantic models

export interface ScoredCell {
  cell_id: string
  geometry: GeoJSON.Geometry
  score: number           // 0.0–1.0 absolute composite
  confidence: number      // 0.0–1.0
  evidence: string[]
  data_sources_used: string[]
  // AOI-relative fields (set by the scoring engine)
  relative_score?: number // min-max stretch within this AOI, 0–1
  percentile?: number     // rank within this AOI, 0–1
  tier?: 'high' | 'medium' | 'low' | 'negligible'
  // Set when this cell was interpolated from a coarser analysis grid
  parent_cell_id?: string
}

// A completed analysis run kept in history so old polygons can be
// revisited and deleted after viewing their data
export interface AnalysisRun {
  id: string
  createdAt: string
  targetMineral: string
  resolutionM: number
  aoi: GeoJSON.Feature
  aoiAreaKm2: number
  results: ScoredCell[]
  agentResults: Record<string, AgentResult> | null
}

export interface AgentUsage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  llm_calls: number
  est_cost_usd: number
  duration_ms: number
}

export interface AgentResult {
  agent_id: string
  status: 'completed' | 'failed' | 'skipped'
  scored_cells: ScoredCell[]
  agent_notes?: string
  warnings: string[]
  usage?: AgentUsage | null
  // null means the agent ran with system=None — no domain grounding
  knowledge_file?: string | null
}

export interface AnalysisJob {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  target_mineral: string
  aoi_geojson: GeoJSON.FeatureCollection
  config?: AnalysisConfig
  agent_results?: Record<string, AgentResult>
  final_scores?: {
    scored_cells: ScoredCell[]
    cell_count: number
    target_mineral: string
  }
  created_at: string
  completed_at?: string
  error_message?: string
}

export interface AnalysisConfig {
  resolution_m?: number
  weights?: Record<string, number>
  enabled_agents?: string[]
}

export interface Channel {
  id: string
  name: string
  source_type: string
  endpoint?: string
  data_type?: string
  is_active: boolean
  last_synced_at?: string
}

export interface SSEEvent {
  event: string
  agent_id?: string
  job_id?: string
  status?: string
  message?: string

  // grid_info
  display_resolution_m?: number
  analysis_resolution_m?: number
  analysis_cell_count?: number

  // spatial_context — `error` non-null means agents got no database evidence
  error?: string | null
  counts?: Record<string, number>

  // agent_grounding — knowledge_file null means system=None (ungrounded)
  knowledge_file?: string | null
  knowledge_chars?: number

  // batch_started / batch_complete / batch_failed
  batch_index?: number
  batch_count?: number
  cell_count?: number
  prompt_chars?: number
  duration_ms?: number
  cells_scored?: number
  cells_requested?: number
  parse_status?: 'ok' | 'partial' | 'failed'
  response_chars?: number
  response_preview?: string
  input_tokens?: number
  output_tokens?: number
  cache_read_tokens?: number
  cache_creation_tokens?: number
  stop_reason?: string | null
  model?: string

  // agent_complete
  cells_total?: number
  warnings?: string[]
  usage?: AgentUsage | null

  // usage (job rollup)
  llm_calls?: number
  est_cost_usd?: number
  by_agent?: Record<string, AgentUsage>
  ungrounded_agents?: string[]
}

// One line in the run log. Severity drives the marker glyph, not a colored
// stripe — `warn` is the interesting one (ungrounded agent, partial parse,
// truncated response) and must stay scannable in a fast-scrolling stream.
export interface LogEntry {
  id: number
  /** ms since the run started */
  t: number
  level: 'info' | 'warn' | 'error' | 'success'
  agentId?: string
  message: string
  /** right-aligned numeric column, e.g. "12.4k → 3.1k tok" */
  metric?: string
  /** revealed on click — raw response preview, stack, warning list */
  detail?: string
}

export interface RunUsage {
  inputTokens: number
  outputTokens: number
  llmCalls: number
  estCostUsd: number
}

// GeoJSON type augmentation
declare global {
  namespace GeoJSON {
    interface Feature {
      id?: string | number
    }
  }
}
