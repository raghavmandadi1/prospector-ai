import { create } from 'zustand'
import type { AnalysisJob, AnalysisRun, ScoredCell } from '../types'

type ActiveView = 'analysis' | 'channels' | 'results'
type ShadingMode = 'relative' | 'absolute'

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

  // Agent results from last run (for evidence drawer breakdown)
  lastAgentResults: Record<string, any> | null
  setLastAgentResults: (results: Record<string, any> | null) => void

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

  // Which agents are enabled for the next run
  enabledAgents: Record<string, boolean>
  setEnabledAgents: (enabled: Record<string, boolean>) => void

  // Grid resolution in meters
  resolutionM: number
  setResolutionM: (m: number) => void

  // Anthropic API key (in-memory only, never persisted)
  apiKey: string
  setApiKey: (key: string) => void

  // Whether the AOI polygon is being drawn
  isDrawing: boolean
  setIsDrawing: (drawing: boolean) => void

  // AOI area in km² (computed when polygon is drawn)
  aoiAreaKm2: number | null
  setAoiAreaKm2: (area: number | null) => void

  // How the results grid is shaded: relative to this AOI, or absolute score
  shadingMode: ShadingMode
  setShadingMode: (mode: ShadingMode) => void

  // History of completed runs (in-memory). Old polygons can be re-viewed
  // and deleted after inspecting their data.
  runs: AnalysisRun[]
  activeRunId: string | null
  addRun: (run: AnalysisRun) => void
  activateRun: (id: string) => void
  deleteRun: (id: string) => void
}

export const useAppStore = create<AppState>((set) => ({
  aoi: null,
  setAoi: (aoi) => set({ aoi }),

  targetMineral: 'gold',
  setTargetMineral: (targetMineral) => set({ targetMineral }),

  currentJob: null,
  setCurrentJob: (currentJob) => set({ currentJob }),

  lastAgentResults: null,
  setLastAgentResults: (lastAgentResults) => set({ lastAgentResults }),

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

  enabledAgents: {
    lithology: true,
    historical: true,
    structure: false,
    geochemistry: false,
    remote_sensing: false,
    proximity: false,
  },
  setEnabledAgents: (enabledAgents) => set({ enabledAgents }),

  resolutionM: 1000,
  setResolutionM: (resolutionM) => set({ resolutionM }),

  apiKey: '',
  setApiKey: (apiKey) => set({ apiKey }),

  isDrawing: false,
  setIsDrawing: (isDrawing) => set({ isDrawing }),

  aoiAreaKm2: null,
  setAoiAreaKm2: (aoiAreaKm2) => set({ aoiAreaKm2 }),

  shadingMode: 'relative',
  setShadingMode: (shadingMode) => set({ shadingMode }),

  runs: [],
  activeRunId: null,
  addRun: (run) =>
    set((state) => ({
      runs: [run, ...state.runs].slice(0, 20), // cap history
      activeRunId: run.id,
    })),
  activateRun: (id) =>
    set((state) => {
      const run = state.runs.find((r) => r.id === id)
      if (!run) return {}
      return {
        activeRunId: id,
        analysisResults: run.results,
        lastAgentResults: run.agentResults,
        aoi: run.aoi,
        aoiAreaKm2: run.aoiAreaKm2,
        targetMineral: run.targetMineral,
        selectedCell: null,
      }
    }),
  deleteRun: (id) =>
    set((state) => {
      const runs = state.runs.filter((r) => r.id !== id)
      // If the deleted run is on-screen, clear the map
      if (state.activeRunId === id) {
        return {
          runs,
          activeRunId: null,
          analysisResults: [],
          lastAgentResults: null,
          aoi: null,
          aoiAreaKm2: null,
          selectedCell: null,
        }
      }
      return { runs }
    }),
}))
