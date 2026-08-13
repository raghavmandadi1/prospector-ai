// Shared TypeScript types mirroring backend Pydantic models

/**
 * Is a high score finding something, or re-finding something?
 *
 *   confirms — a recorded working inside or adjacent to the cell
 *   extends  — a recorded working nearby, but not in the cell
 *   lead     — nothing recorded within the novelty radius
 *
 * Set by the backend from the occurrence extract. It is **absent** on runs made
 * before the field existed and on installs where `wa_occurrences.geojson` was
 * never built. Absent means UNKNOWN and must render as nothing at all — never
 * as a `lead`, or the map would claim novelty it never checked for.
 */
export type NoveltyClass = 'confirms' | 'extends' | 'lead'

export interface NoveltyMeta {
  label: string
  /** One sentence, shown in the legend and the evidence drawer. */
  blurb: string
  /** Outline colour on the results grid; also the legend swatch. */
  color: string
  /**
   * Legend swatch border-style. MapView translates this into a
   * `line-dasharray`, so the swatch and the outline it explains cannot drift.
   */
  borderStyle: 'solid' | 'dashed' | 'dotted'
  /** Outline width on the results grid, px. */
  width: number
}

/**
 * The novelty vocabulary lives here, beside its type, because three components
 * render it: the map outline, the legend, and the evidence drawer. The tier
 * thresholds in this app are already duplicated across those same three files
 * and that duplication has already cost a drift bug — novelty gets one home.
 *
 * Hues are deliberately outside the score ramp (grey → yellow → orange → red).
 * Score and novelty are different axes and must never share a channel: a cell
 * is shaded by how good it looks and outlined by whether anyone has been there.
 */
export const NOVELTY: Record<NoveltyClass, NoveltyMeta> = {
  lead: {
    label: 'Lead',
    blurb: 'nothing recorded nearby',
    color: '#22d3ee', // cyan-400 — also the "not in any database" ring on My Sites
    borderStyle: 'solid',
    width: 1.8,
  },
  extends: {
    label: 'Extends known ground',
    blurb: 'a working nearby, but not in this cell',
    color: '#a78bfa', // violet-400
    borderStyle: 'dashed',
    width: 1.4,
  },
  confirms: {
    label: 'Confirms known ground',
    blurb: 'a recorded working in or beside this cell',
    color: '#34d399', // emerald-400
    borderStyle: 'dotted',
    width: 1.1,
  },
}

/** Legend / drawer ordering: the novel case reads first, it is the useful one. */
export const NOVELTY_ORDER: NoveltyClass[] = ['lead', 'extends', 'confirms']

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
  // ---- Novelty (optional — see NoveltyClass) -------------------------------
  /** Distance to the nearest recorded occurrence, km. null when none was found
   *  inside the search radius; undefined when novelty was never computed. */
  nearest_occurrence_km?: number | null
  novelty?: NoveltyClass | null
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

  // spatial_context — `error` non-null means NO source produced anything, so the
  // agents really are scoring from model prior alone
  error?: string | null
  counts?: Record<string, number>
  /** Which local artifacts loaded, e.g. ["wa_occurrences.geojson", "of00495.sqlite"]. */
  sources?: string[]
  /**
   * What covers THIS aoi, as opposed to what happens to be installed — the 1:24k
   * geology is a 342-quadrangle mosaic with real holes, so those are different
   * claims and only this one describes the run in front of you.
   */
  coverage?: Record<string, number>
  cells_with_facts?: number
  // `cells_total` is shared with agent_complete below — same meaning, and this is
  // one flat interface covering every event, so it is declared once.

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
