import { create } from 'zustand'
import type { AnalysisJob, ScoredCell } from '../types'

type ActiveView = 'analysis' | 'channels' | 'results'

interface AppState {
  // AOI drawn by the user on the map (GeoJSON Feature with Polygon geometry)
  aoi: GeoJSON.Feature | null
  setAoi: (aoi: GeoJSON.Feature | null) => void

  // Target mineral for the current analysis
  targetMineral: string
  setTargetMineral: (mineral: string) => void

  // Currently active analysis job (after submission)
  currentJob: AnalysisJob | null
  setCurrentJob: (job: AnalysisJob | null) => void

  // Final scored results from a completed job
  analysisResults: ScoredCell[]
  setAnalysisResults: (cells: ScoredCell[]) => void

  // Currently selected cell (drives EvidenceDrawer)
  selectedCell: ScoredCell | null
  setSelectedCell: (cell: ScoredCell | null) => void

  // Which primary view is active
  activeView: ActiveView
  setActiveView: (view: ActiveView) => void

  // Agent weight overrides (agent_id → weight)
  agentWeights: Record<string, number>
  setAgentWeights: (weights: Record<string, number>) => void

  // Grid resolution in meters
  resolutionM: number
  setResolutionM: (m: number) => void
}

export const useAppStore = create<AppState>((set) => ({
  aoi: null,
  setAoi: (aoi) => set({ aoi }),

  targetMineral: 'gold',
  setTargetMineral: (targetMineral) => set({ targetMineral }),

  currentJob: null,
  setCurrentJob: (currentJob) => set({ currentJob }),

  analysisResults: [],
  setAnalysisResults: (analysisResults) => set({ analysisResults }),

  selectedCell: null,
  setSelectedCell: (selectedCell) => set({ selectedCell }),

  activeView: 'analysis',
  setActiveView: (activeView) => set({ activeView }),

  agentWeights: {
    lithology: 0.25,
    structure: 0.30,
    geochemistry: 0.20,
    historical: 0.15,
    remote_sensing: 0.07,
    proximity: 0.03,
  },
  setAgentWeights: (agentWeights) => set({ agentWeights }),

  resolutionM: 1000,
  setResolutionM: (resolutionM) => set({ resolutionM }),
}))
