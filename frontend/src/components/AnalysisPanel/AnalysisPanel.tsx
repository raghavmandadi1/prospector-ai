import { useState } from 'react'
import { useAppStore } from '../../store'
import { useAnalysisRunner } from '../../hooks/useAnalysisRunner'

const MINERALS = ['gold', 'silver', 'copper', 'uranium', 'lithium', 'zinc', 'lead']
const AGENTS: { id: string; label: string; description: string }[] = [
  { id: 'lithology', label: 'Lithology', description: 'Bedrock geology favorability' },
  { id: 'historical', label: 'Historical', description: 'Historic mining records & GLO notes' },
  { id: 'structure', label: 'Structure', description: 'Faults, shear zones, fold axes' },
  { id: 'geochemistry', label: 'Geochemistry', description: 'Geochemical anomalies' },
  { id: 'remote_sensing', label: 'Remote Sensing', description: 'Alteration signatures from imagery' },
  { id: 'proximity', label: 'Proximity', description: 'Distance to known deposits' },
]
const RESOLUTIONS = [100, 250, 500, 1000, 2000, 5000]

// Above this cell count the backend coarsens the LLM analysis grid and
// interpolates back down — worth telling the user before they run.
const MAX_LLM_CELLS = 150

export default function AnalysisPanel() {
  const {
    aoi,
    targetMineral, setTargetMineral,
    agentWeights, setAgentWeights,
    enabledAgents, setEnabledAgents,
    resolutionM, setResolutionM,
    apiKey, setApiKey,
    isDrawing, setIsDrawing,
    aoiAreaKm2,
    setAoi, setAoiAreaKm2,
    runs, activeRunId, activateRun, deleteRun,
  } = useAppStore()

  // Run state now lives in the store so RunLog (outside this subtree) can
  // read it and so it survives a sidebar tab switch mid-run.
  const runStatus = useAppStore((s) => s.runStatus)
  const agentPhase = useAppStore((s) => s.agentPhase)
  const agentGrounding = useAppStore((s) => s.agentGrounding)
  const setLogOpen = useAppStore((s) => s.setLogOpen)
  const { run, stop } = useAnalysisRunner()

  const [error, setError] = useState<string | null>(null)
  const [showApiKey, setShowApiKey] = useState(false)

  const isRunning = runStatus === 'running'

  const selectedAgentIds = Object.entries(enabledAgents)
    .filter(([, enabled]) => enabled)
    .map(([id]) => id)

  const totalAgents = selectedAgentIds.length
  const completedAgents = selectedAgentIds.filter(
    (id) => agentPhase[id] === 'done' || agentPhase[id] === 'failed'
  ).length

  function toggleAgent(agentId: string) {
    setEnabledAgents({ ...enabledAgents, [agentId]: !enabledAgents[agentId] })
  }

  async function handleRunAnalysis() {
    if (!aoi) {
      setError('Draw an area of interest on the map first.')
      return
    }
    if (selectedAgentIds.length === 0) {
      setError('Select at least one agent to run.')
      return
    }
    if (!apiKey.trim()) {
      setError('Enter your Anthropic API key to run analysis.')
      return
    }

    setError(null)
    await run({
      aoi,
      aoiAreaKm2: aoiAreaKm2 ?? 0,
      targetMineral,
      resolutionM,
      weights: agentWeights,
      agentIds: selectedAgentIds,
      apiKey,
      onError: setError,
    })
  }

  return (
    <div className="p-4 space-y-5">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
        New Analysis
      </h2>

      {/* API Key input */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">Anthropic API Key</label>
        <div className="relative">
          <input
            type={showApiKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-ant-..."
            disabled={isRunning}
            className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-blue-500 pr-16"
          />
          <button
            type="button"
            onClick={() => setShowApiKey(!showApiKey)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-gray-400 hover:text-white"
          >
            {showApiKey ? 'Hide' : 'Show'}
          </button>
        </div>
        {apiKey && (
          <p className="text-xs text-green-400/70 mt-1">Key set (in-memory only)</p>
        )}
      </div>

      {/* AOI draw button */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">Area of Interest</label>
        {aoi ? (
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-gray-700 rounded px-3 py-2 text-sm border border-green-600/50">
              <span className="text-green-400">{aoiAreaKm2?.toFixed(1)} km²</span>
              <span className="text-gray-400 ml-1">polygon defined</span>
            </div>
            <button
              onClick={() => {
                setAoi(null)
                setAoiAreaKm2(null)
                setIsDrawing(true)
              }}
              disabled={isRunning}
              className="px-3 py-2 text-xs rounded bg-gray-700 border border-gray-600 hover:bg-gray-600 text-gray-300"
            >
              Redraw
            </button>
          </div>
        ) : (
          <button
            onClick={() => setIsDrawing(true)}
            disabled={isRunning}
            className={`w-full py-2 px-4 rounded text-sm font-medium transition-colors ${
              isDrawing
                ? 'bg-yellow-600/30 border border-yellow-500 text-yellow-300'
                : 'bg-gray-700 border border-gray-600 hover:bg-gray-600 text-gray-300'
            }`}
          >
            {isDrawing ? 'Drawing... click map to place vertices' : 'Draw polygon on map'}
          </button>
        )}
        <p className="text-xs text-gray-500 mt-1">Min 25 km². Double-click to close polygon.</p>
      </div>

      {/* Mineral selector */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">Target Mineral</label>
        <select
          value={targetMineral}
          onChange={(e) => setTargetMineral(e.target.value)}
          disabled={isRunning}
          className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-blue-500"
        >
          {MINERALS.map((m) => (
            <option key={m} value={m}>{m.charAt(0).toUpperCase() + m.slice(1)}</option>
          ))}
        </select>
      </div>

      {/* Resolution selector */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">
          Grid Resolution: {resolutionM}m
        </label>
        <select
          value={resolutionM}
          onChange={(e) => setResolutionM(Number(e.target.value))}
          disabled={isRunning}
          className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-blue-500"
        >
          {RESOLUTIONS.map((r) => (
            <option key={r} value={r}>{r}m</option>
          ))}
        </select>
        {aoiAreaKm2 !== null && (() => {
          const estCells = Math.ceil(aoiAreaKm2 / Math.pow(resolutionM / 1000, 2))
          return estCells > MAX_LLM_CELLS ? (
            <p className="text-xs text-gray-500 mt-1">
              ~{estCells.toLocaleString()} cells — agents will score a coarser
              grid and interpolate down to {resolutionM}m.
            </p>
          ) : null
        })()}
      </div>

      {/* Agent selection — checkboxes + weight sliders */}
      <div>
        <label className="block text-xs text-gray-400 mb-2">Agents to Run</label>
        <div className="space-y-2">
          {AGENTS.map((agent) => {
            const enabled = enabledAgents[agent.id] ?? false
            return (
              <div key={agent.id} className={`rounded border px-3 py-2 transition-colors ${
                enabled ? 'border-blue-500/50 bg-gray-700/50' : 'border-gray-700 bg-gray-800/50 opacity-60'
              }`}>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggleAgent(agent.id)}
                    disabled={isRunning}
                    className="accent-blue-500 w-3.5 h-3.5"
                  />
                  <span className="text-sm text-white flex-1">{agent.label}</span>
                  {enabled && (
                    <span className="text-xs text-gray-400">
                      {(agentWeights[agent.id] ?? 0.5).toFixed(2)}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500 ml-6 mt-0.5">{agent.description}</p>
                {enabled && (
                  <div className="flex items-center gap-2 mt-1.5 ml-6">
                    <span className="text-xs text-gray-500 w-10">Weight</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.01}
                      value={agentWeights[agent.id] ?? 0.5}
                      disabled={isRunning}
                      onChange={(e) =>
                        setAgentWeights({ ...agentWeights, [agent.id]: Number(e.target.value) })
                      }
                      className="flex-1 accent-blue-500 h-1"
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Error message */}
      {error && (
        <p className="text-xs text-red-400 bg-red-900/30 rounded p-2">{error}</p>
      )}

      {/* Progress bar */}
      {isRunning && (
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Running agents...</span>
            <span>{completedAgents}/{totalAgents}</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div
              className="bg-blue-500 h-2 rounded-full transition-all duration-300"
              style={{ width: `${totalAgents > 0 ? (completedAgents / totalAgents) * 100 : 0}%` }}
            />
          </div>
          <div className="mt-2 space-y-1">
            {selectedAgentIds.map((agentId) => {
              const state = agentPhase[agentId]
              const agent = AGENTS.find((a) => a.id === agentId)
              // undefined = not reported yet; null = reported as ungrounded
              const grounding = agentGrounding[agentId]
              return (
                <div key={agentId} className="flex items-center gap-2 text-xs">
                  <span
                    className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      state === 'done'
                        ? 'bg-green-400'
                        : state === 'running'
                        ? 'bg-blue-400 animate-pulse motion-reduce:animate-none'
                        : state === 'failed'
                        ? 'bg-red-400'
                        : 'bg-gray-600'
                    }`}
                  />
                  <span className="text-gray-400">
                    {agent?.label ?? agentId}
                  </span>
                  {grounding === null && (
                    <span
                      className="text-amber-300"
                      title="No knowledge file — this agent is scoring with no system prompt, at full weight."
                    >
                      ungrounded
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Run / Stop */}
      {isRunning ? (
        <div className="flex gap-2">
          <button
            onClick={stop}
            className="flex-1 py-2.5 px-4 rounded bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
          >
            Stop Run
          </button>
          <button
            onClick={() => setLogOpen(true)}
            className="px-4 py-2.5 rounded border border-gray-600 text-gray-300 hover:bg-gray-700 text-sm transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
          >
            Log
          </button>
        </div>
      ) : (
        <button
          onClick={handleRunAnalysis}
          disabled={!aoi || selectedAgentIds.length === 0 || !apiKey.trim()}
          className="w-full py-2.5 px-4 rounded bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-sm font-medium transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
        >
          {`Run Analysis (${selectedAgentIds.length} agent${selectedAgentIds.length !== 1 ? 's' : ''})`}
        </button>
      )}

      {/* Past runs — revisit or delete old polygons */}
      {runs.length > 0 && (
        <div>
          <label className="block text-xs text-gray-400 mb-2 uppercase tracking-wider">
            Past Runs
          </label>
          <div className="space-y-1.5">
            {runs.map((run) => {
              const isActive = run.id === activeRunId
              const when = new Date(run.createdAt)
              return (
                <div
                  key={run.id}
                  className={`flex items-center gap-2 rounded border px-2.5 py-2 text-xs ${
                    isActive
                      ? 'border-blue-500/60 bg-gray-700/60'
                      : 'border-gray-700 bg-gray-800/50'
                  }`}
                >
                  <button
                    onClick={() => activateRun(run.id)}
                    disabled={isRunning}
                    className="flex-1 text-left min-w-0"
                    title="Show this run's polygon and results on the map"
                  >
                    <div className="text-gray-200 truncate">
                      {run.targetMineral.charAt(0).toUpperCase() + run.targetMineral.slice(1)}
                      <span className="text-gray-500"> · {run.aoiAreaKm2.toFixed(0)} km² · {run.resolutionM}m</span>
                    </div>
                    <div className="text-gray-500">
                      {when.toLocaleDateString()} {when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      {isActive && <span className="text-blue-400 ml-1.5">on map</span>}
                    </div>
                  </button>
                  <button
                    onClick={() => {
                      if (window.confirm('Delete this run and remove its polygon from the map?')) {
                        deleteRun(run.id)
                      }
                    }}
                    disabled={isRunning}
                    className="flex-shrink-0 px-2 py-1.5 rounded text-gray-500 hover:text-red-400 hover:bg-gray-700 transition-colors"
                    title="Delete this run"
                  >
                    Delete
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
