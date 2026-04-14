// Shared TypeScript types mirroring backend Pydantic models

export interface ScoredCell {
  cell_id: string
  geometry: GeoJSON.Geometry
  score: number           // 0.0–1.0
  confidence: number      // 0.0–1.0
  evidence: string[]
  data_sources_used: string[]
  tier?: 'high' | 'medium' | 'low' | 'negligible'
}

export interface AgentResult {
  agent_id: string
  status: 'completed' | 'failed' | 'skipped'
  scored_cells: ScoredCell[]
  agent_notes?: string
  warnings: string[]
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
}

// GeoJSON type augmentation
declare global {
  namespace GeoJSON {
    interface Feature {
      id?: string | number
    }
  }
}
