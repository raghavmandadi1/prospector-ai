import React, { useState } from 'react'
import { useAppStore } from '../../store'
import { analysisApi, subscribeToJobEvents } from '../../api/client'
import type { SSEEvent } from '../../types'

const MINERALS = ['gold', 'silver', 'copper', 'uranium', 'lithium', 'zinc', 'lead']
const AGENTS = ['lithology', 'structure', 'geochemistry', 'historical', 'remote_sensing', 'proximity']
const RESOLUTIONS = [250, 500, 1000, 2000, 5000]

export default function AnalysisPanel() {
  const {
    aoi,
    targetMineral, setTargetMineral,
    agentWeights, setAgentWeights,
    resolutionM, setResolutionM,
    setCurrentJob,
    setAnalysisResults,
  } = useAppStore()

  const [isRunning, setIsRunning] = useState(false)
  const [progress, setProgress] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [completedAgents, setCompletedAgents] = useState(0)

  const totalAgents = AGENTS.length

  async function handleRunAnalysis() {
    if (!aoi) {
      setError('Please draw an area of interest on the map first.')
      return
    }

    setIsRunning(true)
    setError(null)
    setProgress({})
    setCompletedAgents(0)

    try {
      const job = await analysisApi.createJob({
        aoi_geojson: {
          type: 'FeatureCollection',
          features: [aoi],
        },
        target_mineral: targetMineral,
        config: {
          resolution_m: resolutionM,
          weights: agentWeights,
        },
      })

      setCurrentJob(job)

      // Subscribe to SSE progress events
      const unsubscribe = subscribeToJobEvents(
        job.id,
        (event: SSEEvent) => {
          if (event.event === 'agent_started') {
            setProgress((prev) => ({ ...prev, [event.agent_id!]: 'running' }))
          } else if (event.event === 'agent_complete') {
            setProgress((prev) => ({
              ...prev,
              [event.agent_id!]: event.status === 'completed' ? 'done' : 'failed',
            }))
            setCompletedAgents((n) => n + 1)
          } else if (event.event === 'job_complete') {
            // Fetch final results
            analysisApi.getJob(job.id).then((finalJob) => {
              setCurrentJob(finalJob)
              const cells = finalJob.final_scores?.scored_cells ?? []
              setAnalysisResults(cells)
              setIsRunning(false)
              unsubscribe()
            })
          } else if (event.event === 'error') {
            setError(event.message ?? 'Analysis failed')
            setIsRunning(false)
            unsubscribe()
          }
        },
        () => {
          setError('Lost connection to job stream')
          setIsRunning(false)
        }
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start analysis')
      setIsRunning(false)
    }
  }

  return (
    <div className="p-4 space-y-5">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
        New Analysis
      </h2>

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
      </div>

      {/* Agent weight sliders */}
      <div>
        <label className="block text-xs text-gray-400 mb-2">Agent Weights</label>
        <div className="space-y-2">
          {AGENTS.map((agent) => (
            <div key={agent} className="flex items-center gap-2">
              <span className="text-xs text-gray-400 w-28 flex-shrink-0 capitalize">
                {agent.replace('_', ' ')}
              </span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={agentWeights[agent] ?? 0.5}
                disabled={isRunning}
                onChange={(e) =>
                  setAgentWeights({ ...agentWeights, [agent]: Number(e.target.value) })
                }
                className="flex-1 accent-blue-500"
              />
              <span className="text-xs text-gray-400 w-8 text-right">
                {(agentWeights[agent] ?? 0.5).toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* AOI hint */}
      {!aoi && (
        <p className="text-xs text-yellow-400 bg-yellow-900/30 rounded p-2">
          Draw an area of interest on the map to enable analysis.
          <br />
          <span className="text-gray-400">(AOI draw tool coming soon)</span>
        </p>
      )}

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
              style={{ width: `${(completedAgents / totalAgents) * 100}%` }}
            />
          </div>
          <div className="mt-2 space-y-1">
            {AGENTS.map((agent) => {
              const state = progress[agent]
              return (
                <div key={agent} className="flex items-center gap-2 text-xs">
                  <span
                    className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      state === 'done'
                        ? 'bg-green-400'
                        : state === 'running'
                        ? 'bg-blue-400 animate-pulse'
                        : state === 'failed'
                        ? 'bg-red-400'
                        : 'bg-gray-600'
                    }`}
                  />
                  <span className="text-gray-400 capitalize">
                    {agent.replace('_', ' ')}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Run button */}
      <button
        onClick={handleRunAnalysis}
        disabled={isRunning || !aoi}
        className="w-full py-2 px-4 rounded bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-sm font-medium transition-colors"
      >
        {isRunning ? 'Running Analysis...' : 'Run Analysis'}
      </button>
    </div>
  )
}
