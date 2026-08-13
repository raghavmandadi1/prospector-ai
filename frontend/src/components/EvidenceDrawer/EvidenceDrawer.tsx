import { useState } from 'react'
import { NOVELTY } from '../../types'
import type { NoveltyMeta, ScoredCell } from '../../types'
import { useAppStore } from '../../store'

interface Props {
  cell: ScoredCell | null
  onClose: () => void
}

export default function EvidenceDrawer({ cell, onClose }: Props) {
  const { currentJob, lastAgentResults } = useAppStore()
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null)

  if (!cell) return null

  // Build per-agent breakdown from either lastAgentResults (dev mode) or currentJob (prod mode).
  // Interpolated fine cells look up agent scores via their coarse parent cell.
  const lookupId = cell.parent_cell_id ?? cell.cell_id
  const agentData = lastAgentResults || currentJob?.agent_results || {}
  const agentBreakdown = Object.entries(agentData).map(([agentId, result]: [string, any]) => {
    const scored = result.scored_cells?.find((c: any) => c.cell_id === lookupId)
    return {
      agentId,
      score: scored?.score ?? null,
      confidence: scored?.confidence ?? null,
      evidence: scored?.evidence ?? [],
      dataSources: scored?.data_sources_used ?? [],
      agentNotes: result.agent_notes ?? null,
      status: result.status,
    }
  })

  // Novelty answers the first question a high score raises: is this finding
  // something, or re-finding something? Undefined means the run never computed
  // it — render nothing rather than implying the cell is virgin ground.
  const noveltyMeta: NoveltyMeta | null =
    cell.novelty != null ? NOVELTY[cell.novelty] ?? null : null
  const nearestKm =
    typeof cell.nearest_occurrence_km === 'number' ? cell.nearest_occurrence_km : null

  function tierColor(score: number): string {
    if (score >= 0.65) return 'text-red-400'
    if (score >= 0.4) return 'text-orange-400'
    if (score >= 0.2) return 'text-yellow-400'
    return 'text-gray-400'
  }

  function tierBg(score: number): string {
    if (score >= 0.65) return 'bg-red-500'
    if (score >= 0.4) return 'bg-orange-500'
    if (score >= 0.2) return 'bg-yellow-500'
    return 'bg-gray-500'
  }

  return (
    <div className="w-96 flex-shrink-0 flex flex-col bg-gray-800 border-l border-gray-700 z-20 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <div>
          <div className="text-xs text-gray-400">{cell.cell_id}</div>
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

      {/* Novelty — deliberately above the score breakdown. A 0.91 cell sitting
          on three recorded workings and a 0.91 cell on ground nobody has
          written up mean opposite things. */}
      {(noveltyMeta !== null || nearestKm !== null) && (
        <div className="px-4 py-3 border-b border-gray-700 flex items-start gap-2">
          <span
            className="w-2.5 h-2.5 rounded-full flex-shrink-0 mt-1"
            style={{ background: noveltyMeta?.color ?? '#6b7280' }}
          />
          <div className="min-w-0">
            {noveltyMeta !== null && (
              <>
                <div
                  className="text-xs font-semibold"
                  style={{ color: noveltyMeta.color }}
                >
                  {noveltyMeta.label}
                </div>
                <div className="text-[11px] text-gray-400 leading-snug">
                  {noveltyMeta.blurb}
                </div>
              </>
            )}
            <div className="text-[11px] text-gray-300 mt-1">
              {nearestKm !== null ? (
                <>
                  Nearest recorded working{' '}
                  <span className="font-semibold tabular-nums">
                    {nearestKm.toFixed(2)} km
                  </span>
                </>
              ) : (
                'No recorded working within the search radius'
              )}
            </div>
          </div>
        </div>
      )}

      {/* Composite stats */}
      <div className="px-4 py-3 border-b border-gray-700 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-gray-400">Composite Score</div>
          <div className={`font-semibold ${tierColor(cell.score)}`}>
            {cell.score.toFixed(3)}
            <span className="text-gray-500 font-normal ml-1">absolute</span>
          </div>
        </div>
        <div>
          <div className="text-gray-400">Confidence</div>
          <div className="font-semibold text-white">
            {(cell.confidence * 100).toFixed(0)}%
          </div>
        </div>
        {cell.percentile != null && (
          <div>
            <div className="text-gray-400">Within this AOI</div>
            <div className="font-semibold text-white">
              Top {Math.max(1, Math.round((1 - cell.percentile) * 100))}%
            </div>
          </div>
        )}
        {cell.parent_cell_id && (
          <div>
            <div className="text-gray-400">Source</div>
            <div className="font-semibold text-gray-300">
              Interpolated
              <span className="text-gray-500 font-normal ml-1">({cell.parent_cell_id})</span>
            </div>
          </div>
        )}
      </div>

      {/* Agent breakdown — expandable */}
      {agentBreakdown.length > 0 && (
        <div className="px-4 py-3 border-b border-gray-700">
          <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Agent Scores</div>
          {agentBreakdown.map(({ agentId, score, confidence, evidence, agentNotes, status }) => {
            const isExpanded = expandedAgent === agentId
            return (
              <div key={agentId} className="mb-2">
                <button
                  onClick={() => setExpandedAgent(isExpanded ? null : agentId)}
                  className="w-full text-left"
                >
                  <div className="flex justify-between text-xs mb-0.5">
                    <span className="text-gray-300 capitalize">
                      {agentId.replace('_', ' ')}
                      {status === 'failed' && <span className="text-red-400 ml-1">(failed)</span>}
                      <span className="text-gray-500 ml-1">{isExpanded ? '▼' : '▶'}</span>
                    </span>
                    <span className={score !== null ? tierColor(score) : 'text-gray-500'}>
                      {score !== null ? (score * 100).toFixed(0) : 'N/A'}
                      {confidence !== null && (
                        <span className="text-gray-500 ml-1">({(confidence * 100).toFixed(0)}%)</span>
                      )}
                    </span>
                  </div>
                  <div className="w-full bg-gray-700 rounded-full h-1.5">
                    <div
                      className={`h-1.5 rounded-full ${score !== null ? tierBg(score) : 'bg-gray-600'}`}
                      style={{ width: `${(score ?? 0) * 100}%` }}
                    />
                  </div>
                </button>

                {isExpanded && (
                  <div className="mt-2 ml-2 pl-2 border-l border-gray-600">
                    {/* Per-agent evidence for this cell */}
                    {evidence.length > 0 && (
                      <div className="mb-2">
                        <div className="text-xs text-gray-500 mb-1">Evidence:</div>
                        {evidence.map((ev: string, i: number) => (
                          <div key={i} className="text-xs text-gray-300 mb-0.5 flex gap-1.5">
                            <span className="text-green-400 flex-shrink-0">•</span>
                            <span>{ev}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {/* Agent notes (LLM response summary) */}
                    {agentNotes && (
                      <div className="mb-2">
                        <div className="text-xs text-gray-500 mb-1">LLM Response (excerpt):</div>
                        <div className="text-xs text-gray-400 bg-gray-900 rounded p-2 max-h-40 overflow-y-auto font-mono whitespace-pre-wrap">
                          {agentNotes}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Aggregated evidence list */}
      <div className="px-4 py-3 border-b border-gray-700">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Combined Evidence</div>
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
