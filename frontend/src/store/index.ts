import { create } from 'zustand'
import { DEFAULT_BASEMAP } from '../components/Map/basemaps'
import type { AnalysisJob, AnalysisRun, LogEntry, RunUsage, ScoredCell } from '../types'

type ActiveView = 'analysis' | 'channels' | 'results'
type ShadingMode = 'relative' | 'absolute'
type AgentPhase = 'pending' | 'running' | 'done' | 'failed'
export type RunStatus = 'idle' | 'running' | 'completed' | 'failed' | 'stopped'

// Cap on retained log lines. A 150-cell run across 6 agents produces a few
// hundred entries; 2000 leaves headroom without letting a pathological run
// grow the store without bound.
const MAX_LOG_ENTRIES = 2000

const EMPTY_USAGE: RunUsage = {
  inputTokens: 0,
  outputTokens: 0,
  llmCalls: 0,
  estCostUsd: 0,
}

export type OverlayId =
  | 'plss'
  | 'wilderness'
  | 'toponyms'
  | 'occurrences'
  /** WA DNR mining-district polygons */
  | 'districts'
  /** Inactive and abandoned mine lands — adits and shafts */
  | 'iaml'
  /** The user's own imported field pins ("My Sites") */
  | 'user_sites'
  | 'coverage'

export interface MapView {
  lng: number
  lat: number
  zoom: number
}

// Map/layer preferences persist; run state deliberately does not. Written on
// every change and read once at store creation.
const PREFS_KEY = 'geoprospector.map.v1'

interface MapPrefs {
  basemap: string
  resultsOpacity: number
  resultsVisible: boolean
  noveltyOutlines: boolean
  overlays: Record<OverlayId, boolean>
  mapView: MapView | null
}

const DEFAULT_PREFS: MapPrefs = {
  basemap: DEFAULT_BASEMAP,
  resultsOpacity: 0.6,
  resultsVisible: true,
  // On by default: without it a hot cell on known ground and a hot cell on
  // virgin ground look identical, which is the thing they most must not.
  noveltyOutlines: true,
  overlays: {
    plss: false,
    wilderness: false,
    toponyms: false,
    occurrences: false,
    districts: false,
    iaml: false,
    user_sites: false,
    coverage: false,
  },
  mapView: null,
}

function loadPrefs(): MapPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    if (!raw) return DEFAULT_PREFS
    const parsed = JSON.parse(raw) as Partial<MapPrefs>
    return {
      ...DEFAULT_PREFS,
      ...parsed,
      overlays: { ...DEFAULT_PREFS.overlays, ...(parsed.overlays ?? {}) },
    }
  } catch {
    // Corrupt or unavailable storage must never stop the app booting
    return DEFAULT_PREFS
  }
}

function savePrefs(prefs: Partial<MapPrefs>) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...prefs }))
  } catch {
    /* private browsing, quota — not worth surfacing */
  }
}

const PREFS = loadPrefs()

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

  // ---- Map layers (persisted to localStorage) ------------------------------
  basemap: string
  setBasemap: (id: string) => void

  /** Multiplies the results fill opacity. Fading results in and out over fixed
   *  terrain is the single most useful interaction for judging whether a score
   *  makes geological sense. */
  resultsOpacity: number
  setResultsOpacity: (v: number) => void
  resultsVisible: boolean
  setResultsVisible: (v: boolean) => void

  /** Draw the per-cell novelty outline (confirms / extends / lead) over the
   *  results grid. Worth being able to switch off on a dense display grid,
   *  where contiguous leads merge into one wash of colour. */
  noveltyOutlines: boolean
  setNoveltyOutlines: (v: boolean) => void

  overlays: Record<OverlayId, boolean>
  toggleOverlay: (id: OverlayId, on?: boolean) => void

  /** Which reference layers this backend actually has built on disk. */
  availableLayers: Record<string, boolean> | null
  setAvailableLayers: (v: Record<string, boolean> | null) => void

  /** Last map view, restored on reload so every refresh doesn't dump you back
   *  to a statewide default. */
  mapView: MapView | null
  setMapView: (v: MapView) => void

  // History of completed runs (in-memory). Old polygons can be re-viewed
  // and deleted after inspecting their data.
  runs: AnalysisRun[]
  activeRunId: string | null
  addRun: (run: AnalysisRun) => void
  activateRun: (id: string) => void
  deleteRun: (id: string) => void

  // ---- Live run telemetry (in-memory, cleared on reload) -------------------
  // Lives in the store rather than AnalysisPanel local state so the log
  // survives sidebar tab switches and can be rendered outside the sidebar.
  runStatus: RunStatus
  runStartedAt: number | null
  runLog: LogEntry[]
  runUsage: RunUsage
  agentPhase: Record<string, AgentPhase>
  /** agent_id → knowledge file, or null when it ran ungrounded */
  agentGrounding: Record<string, string | null>
  logOpen: boolean

  /** AbortController for the in-flight run. Non-serializable by design —
   *  the store never persists, so there is nothing to serialize it into. */
  runAbort: AbortController | null

  startRun: (controller: AbortController, agentIds: string[]) => void
  appendLog: (entry: Omit<LogEntry, 'id' | 't'>) => void
  addUsage: (delta: Partial<RunUsage>) => void
  setAgentPhase: (agentId: string, phase: AgentPhase) => void
  setAgentGrounding: (agentId: string, file: string | null) => void
  finishRun: (status: Exclude<RunStatus, 'idle' | 'running'>) => void
  stopRun: () => void
  setLogOpen: (open: boolean) => void
  clearLog: () => void
}

let logSeq = 0

export const useAppStore = create<AppState>((set, get) => ({
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

  basemap: PREFS.basemap,
  setBasemap: (basemap) => {
    savePrefs({ basemap })
    set({ basemap })
  },

  resultsOpacity: PREFS.resultsOpacity,
  setResultsOpacity: (resultsOpacity) => {
    savePrefs({ resultsOpacity })
    set({ resultsOpacity })
  },

  resultsVisible: PREFS.resultsVisible,
  setResultsVisible: (resultsVisible) => {
    savePrefs({ resultsVisible })
    set({ resultsVisible })
  },

  noveltyOutlines: PREFS.noveltyOutlines,
  setNoveltyOutlines: (noveltyOutlines) => {
    savePrefs({ noveltyOutlines })
    set({ noveltyOutlines })
  },

  overlays: PREFS.overlays,
  toggleOverlay: (id, on) =>
    set((state) => {
      const overlays = { ...state.overlays, [id]: on ?? !state.overlays[id] }
      savePrefs({ overlays })
      return { overlays }
    }),

  availableLayers: null,
  setAvailableLayers: (availableLayers) => set({ availableLayers }),

  mapView: PREFS.mapView,
  setMapView: (mapView) => {
    savePrefs({ mapView })
    // Deliberately not in React state: this fires on every moveend and would
    // re-render the whole tree. The store value exists only to be read once at
    // init and written to localStorage.
    PREFS.mapView = mapView
  },

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

  // ---- Live run telemetry ---------------------------------------------------

  runStatus: 'idle',
  runStartedAt: null,
  runLog: [],
  runUsage: EMPTY_USAGE,
  agentPhase: {},
  agentGrounding: {},
  logOpen: false,
  runAbort: null,

  startRun: (controller, agentIds) => {
    logSeq = 0
    set({
      runStatus: 'running',
      runStartedAt: Date.now(),
      runLog: [],
      runUsage: EMPTY_USAGE,
      agentPhase: Object.fromEntries(agentIds.map((id) => [id, 'pending' as AgentPhase])),
      agentGrounding: {},
      runAbort: controller,
      logOpen: true,
    })
  },

  appendLog: (entry) =>
    set((state) => {
      const t = state.runStartedAt ? Date.now() - state.runStartedAt : 0
      const next = [...state.runLog, { ...entry, id: logSeq++, t }]
      return {
        runLog: next.length > MAX_LOG_ENTRIES ? next.slice(-MAX_LOG_ENTRIES) : next,
      }
    }),

  addUsage: (delta) =>
    set((state) => ({
      runUsage: {
        inputTokens: state.runUsage.inputTokens + (delta.inputTokens ?? 0),
        outputTokens: state.runUsage.outputTokens + (delta.outputTokens ?? 0),
        llmCalls: state.runUsage.llmCalls + (delta.llmCalls ?? 0),
        estCostUsd: state.runUsage.estCostUsd + (delta.estCostUsd ?? 0),
      },
    })),

  setAgentPhase: (agentId, phase) =>
    set((state) => ({ agentPhase: { ...state.agentPhase, [agentId]: phase } })),

  setAgentGrounding: (agentId, file) =>
    set((state) => ({ agentGrounding: { ...state.agentGrounding, [agentId]: file } })),

  finishRun: (status) =>
    set((state) => ({
      runStatus: status,
      runAbort: null,
      // Any agent still marked running when the run ends never reported back
      agentPhase: Object.fromEntries(
        Object.entries(state.agentPhase).map(([id, p]) => [
          id,
          p === 'running' || p === 'pending' ? (status === 'completed' ? p : 'failed') : p,
        ])
      ) as Record<string, AgentPhase>,
    })),

  stopRun: () => {
    const { runAbort, runStatus } = get()
    if (runStatus !== 'running') return
    runAbort?.abort()
    set({ runStatus: 'stopped', runAbort: null })
  },

  setLogOpen: (logOpen) => set({ logOpen }),

  clearLog: () => set({ runLog: [], runUsage: EMPTY_USAGE, runStatus: 'idle' }),
}))
