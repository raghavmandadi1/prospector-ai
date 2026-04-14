import React from 'react'
import type { ScoredCell } from '../../types'
import { useAppStore } from '../../store'

interface Props {
  cell: ScoredCell | null
  onClose: () => void
}

export default function EvidenceDrawer({ cell, onClose }: Props) {
  const { currentJob } = useAppStore()

  if (!cell) return null

  const agentBreakdown = currentJob?.agent_results
    ? Object.entries(currentJob.agent_results).map(([agentId, result]) => {
        const scored = result.scored_cells.find((c) => c.cell_id === cell.cell_id)
        return { agentId, score: scored?.score ?? null, evidence: scored?.evidence ?? [] }
      })
    : []

  function tierColor(score: number): string {
    if (score >= 0.65) return 'text-red-400'
    if (score >= 0.4) return 'text-orange-400'
    if (score >= 0.2) return 'text-yellow-400'
    return 'text-gray-400'
  }

  return (
    <div className="w-80 flex-shrink-0 flex flex-col bg-gray-800 border-l border-gray-700 z-20 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <div>
          <div className="text-sm font-medium">Cell Evidence</div>
          <div className={`text-xl font-bold ${tierColor(cell.score)}`}>
            {(cell.score * 100).toFixed(0)}
            <span className="text-sm font-normal text-gray-400 ml-1">/ 100</span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-white transition-colors text-lg"
        >
          ×
        </button>
      </div>

      {/* Composite stats */}
      <div className="px-4 py-3 border-b border-gray-700 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-gray-400">Composite Score</div>
          <div className={`font-semibold ${tierColor(cell.score)}`}>
            {cell.score.toFixed(3)}
          </div>
        </div>
        <div>
          <div className="text-gray-400">Confidence</div>
          <div className="font-semibold text-white">
            {(cell.confidence * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      {/* Agent breakdown */}
      {agentBreakdown.length > 0 && (
        <div className="px-4 py-3 border-b border-gray-700">
          <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Agent Scores</div>
          {agentBreakdown.map(({ agentId, score }) => (
            <div key={agentId} className="mb-2">
              <div className="flex justify-between text-xs mb-0.5">
                <span className="text-gray-300 capitalize">{agentId.replace('_', ' ')}</span>
                <span className={score !== null ? tierColor(score) : 'text-gray-500'}>
                  {score !== null ? (score * 100).toFixed(0) : 'N/A'}
                </span>
              </div>
              <div className="w-full bg-gray-700 rounded-full h-1.5">
                <div
                  className={`h-1.5 rounded-full ${
                    score !== null && score >= 0.65
                      ? 'bg-red-500'
                      : score !== null && score >= 0.4
                      ? 'bg-orange-500'
                      : score !== null && score >= 0.2
                      ? 'bg-yellow-500'
                      : 'bg-gray-500'
                  }`}
                  style={{ width: `${(score ?? 0) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Evidence list */}
      <div className="px-4 py-3 border-b border-gray-700">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Evidence</div>
        {cell.evidence.length > 0 ? (
          <ul className="space-y-1">
            {cell.evidence.map((ev, i) => (
              <li key={i} className="text-xs text-gray-300 flex gap-2">
                <span className="text-blue-400 flex-shrink-0">•</span>
                <span>{ev}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-gray-500 italic">No evidence recorded</p>
        )}
      </div>

      {/* Data sources */}
      <div className="px-4 py-3">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Data Sources</div>
        {cell.data_sources_used.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {cell.data_sources_used.map((src) => (
              <span
                key={src}
                className="px-2 py-0.5 bg-gray-700 rounded text-xs text-gray-300"
              >
                {src}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-xs text-gray-500 italic">No data sources recorded</p>
        )}
      </div>
    </div>
  )
}
