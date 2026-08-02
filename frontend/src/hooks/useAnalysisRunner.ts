/**
 * Owns the lifecycle of a dev-mode analysis run: starting it, translating the
 * SSE telemetry stream into run-log entries, and stopping it.
 *
 * Pulled out of AnalysisPanel so the panel stays a form and the event
 * translation lives somewhere it can be read (and eventually tested) on its own.
 */
import { useCallback } from 'react'
import { useAppStore } from '../store'
import { runAnalysisDev } from '../api/client'
import type { AnalysisRun, ScoredCell, SSEEvent } from '../types'

/** Human labels for agent ids, kept here so the log reads like prose. */
export const AGENT_LABELS: Record<string, string> = {
  lithology: 'Lithology',
  historical: 'Historical',
  structure: 'Structure',
  geochemistry: 'Geochemistry',
  remote_sensing: 'Remote Sensing',
  proximity: 'Proximity',
}

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function useAnalysisRunner() {
  const startRun = useAppStore((s) => s.startRun)
  const appendLog = useAppStore((s) => s.appendLog)
  const addUsage = useAppStore((s) => s.addUsage)
  const setAgentPhase = useAppStore((s) => s.setAgentPhase)
  const setAgentGrounding = useAppStore((s) => s.setAgentGrounding)
  const finishRun = useAppStore((s) => s.finishRun)
  const stopRun = useAppStore((s) => s.stopRun)

  /**
   * Translate one SSE event into log entries + derived state.
   * Unknown event types are logged verbatim rather than dropped — a silent
   * `else` branch is how a new backend event goes unnoticed for months.
   */
  const handleEvent = useCallback(
    (event: SSEEvent) => {
      const label = event.agent_id ? AGENT_LABELS[event.agent_id] ?? event.agent_id : undefined

      switch (event.event) {
        case 'started':
          appendLog({ level: 'info', message: `Job ${event.job_id?.slice(0, 8)} started` })
          break

        case 'grid_info':
          appendLog({
            level: 'info',
            message:
              `Grid coarsened for scoring: ${event.display_resolution_m}m display → ` +
              `${event.analysis_resolution_m}m analysis`,
            metric: `${event.analysis_cell_count} cells`,
            detail:
              'Agents score the coarse grid; composite scores are IDW-interpolated ' +
              'back down to the display resolution afterwards.',
          })
          break

        case 'spatial_context': {
          const counts = event.counts ?? {}
          const total = Object.values(counts).reduce((a, b) => a + b, 0)
          if (event.error) {
            appendLog({
              level: 'warn',
              message: 'Spatial context unavailable — agents run on LLM regional knowledge only',
              metric: '0 records',
              detail: event.error,
            })
          } else {
            appendLog({
              level: total > 0 ? 'info' : 'warn',
              message:
                total > 0
                  ? 'Spatial context loaded'
                  : 'Spatial context empty — no database evidence for any agent',
              metric: `${total} records`,
              detail: Object.entries(counts)
                .map(([k, v]) => `${k}: ${v}`)
                .join('\n'),
            })
          }
          break
        }

        case 'agent_grounding':
          setAgentGrounding(event.agent_id!, event.knowledge_file ?? null)
          if (event.knowledge_file) {
            appendLog({
              level: 'info',
              agentId: event.agent_id,
              message: `Grounded on ${event.knowledge_file}`,
              metric: `${fmtTokens(event.knowledge_chars ?? 0)} chars`,
            })
          } else {
            appendLog({
              level: 'warn',
              agentId: event.agent_id,
              message: 'No knowledge file — running with no system prompt',
              detail:
                `${label} has no file under agents/knowledge/. Its scores are ` +
                'ungrounded model prior, but carry full weight in the composite.',
            })
          }
          break

        case 'agent_started':
          setAgentPhase(event.agent_id!, 'running')
          break

        case 'batch_started':
          appendLog({
            level: 'info',
            agentId: event.agent_id,
            message: `Batch ${(event.batch_index ?? 0) + 1}/${event.batch_count} — ${event.cell_count} cells`,
            metric: `${fmtTokens(event.prompt_chars ?? 0)} chars`,
          })
          break

        case 'batch_complete': {
          const inTok = event.input_tokens ?? 0
          const outTok = event.output_tokens ?? 0
          addUsage({ inputTokens: inTok, outputTokens: outTok, llmCalls: 1 })

          const truncated = event.stop_reason === 'max_tokens'
          const bad = event.parse_status !== 'ok' || truncated
          const notes: string[] = []
          if (truncated) notes.push('Response hit max_tokens — JSON was truncated.')
          if (event.parse_status === 'partial') {
            notes.push(
              `Parsed ${event.cells_scored}/${event.cells_requested} cells. The rest ` +
                'get confidence=0 placeholders and are ignored by the scoring engine.'
            )
          }
          if (event.parse_status === 'failed') {
            notes.push('No cells parsed from this batch. Entire batch lost.')
          }
          notes.push(`--- response preview ---\n${event.response_preview ?? ''}`)

          appendLog({
            level: bad ? 'warn' : 'success',
            agentId: event.agent_id,
            message:
              `Batch ${(event.batch_index ?? 0) + 1}/${event.batch_count} scored ` +
              `${event.cells_scored}/${event.cells_requested}` +
              (truncated ? ' · truncated' : ''),
            metric: `${fmtTokens(inTok)}→${fmtTokens(outTok)} · ${((event.duration_ms ?? 0) / 1000).toFixed(1)}s`,
            detail: notes.join('\n\n'),
          })
          break
        }

        case 'batch_failed':
          appendLog({
            level: 'error',
            agentId: event.agent_id,
            message: `Batch ${(event.batch_index ?? 0) + 1} failed`,
            detail: event.error ?? undefined,
          })
          break

        case 'agent_complete': {
          const ok = event.status === 'completed'
          setAgentPhase(event.agent_id!, ok ? 'done' : 'failed')
          const u = event.usage
          appendLog({
            level: ok ? 'success' : 'error',
            agentId: event.agent_id,
            message: ok
              ? `Complete — ${event.cells_scored}/${event.cells_total} cells scored`
              : 'Failed',
            metric: u
              ? `${fmtTokens(u.input_tokens)}→${fmtTokens(u.output_tokens)} · $${u.est_cost_usd.toFixed(3)}`
              : undefined,
            detail: event.warnings?.length ? event.warnings.join('\n') : undefined,
          })
          break
        }

        case 'usage':
          appendLog({
            level: 'info',
            message: `All agents done — ${event.llm_calls} LLM calls`,
            metric: `$${(event.est_cost_usd ?? 0).toFixed(4)}`,
            detail: Object.entries(event.by_agent ?? {})
              .map(
                ([id, u]) =>
                  `${id}: ${u.input_tokens} in / ${u.output_tokens} out / ` +
                  `${u.llm_calls} calls / $${u.est_cost_usd.toFixed(4)}`
              )
              .join('\n'),
          })
          if (event.ungrounded_agents?.length) {
            appendLog({
              level: 'warn',
              message: `${event.ungrounded_agents.length} of ${
                Object.keys(event.by_agent ?? {}).length
              } agents scored without domain grounding`,
              detail: event.ungrounded_agents.join(', '),
            })
          }
          break

        case 'results':
          appendLog({ level: 'info', message: 'Synthesizing composite scores' })
          break

        case 'job_complete':
          appendLog({ level: 'success', message: 'Run complete' })
          break

        case 'error':
          appendLog({ level: 'error', message: event.message ?? 'Analysis failed' })
          break

        default:
          appendLog({
            level: 'info',
            message: `Unhandled event: ${event.event}`,
            detail: JSON.stringify(event, null, 2),
          })
      }
    },
    [appendLog, addUsage, setAgentPhase, setAgentGrounding]
  )

  const run = useCallback(
    async (args: {
      aoi: GeoJSON.Feature
      aoiAreaKm2: number
      targetMineral: string
      resolutionM: number
      weights: Record<string, number>
      agentIds: string[]
      apiKey: string
      onError: (msg: string) => void
    }) => {
      const controller = new AbortController()
      startRun(controller, args.agentIds)

      const store = useAppStore.getState()

      try {
        await runAnalysisDev(
          {
            aoi_geojson: { type: 'FeatureCollection', features: [args.aoi] },
            target_mineral: args.targetMineral,
            config: {
              resolution_m: args.resolutionM,
              weights: args.weights,
              enabled_agents: args.agentIds,
            },
            anthropic_api_key: args.apiKey,
          },
          (event) => {
            handleEvent(event)

            if (event.event === 'results') {
              const scores = event.final_scores as { scored_cells: ScoredCell[] } | undefined
              const cells = scores?.scored_cells ?? []
              store.setAnalysisResults(cells)
              const agentResults = (event.agent_results as Record<string, any>) ?? null
              if (agentResults) store.setLastAgentResults(agentResults)

              if (cells.length > 0) {
                const record: AnalysisRun = {
                  id: (crypto as any).randomUUID?.() ?? String(Date.now()),
                  createdAt: new Date().toISOString(),
                  targetMineral: args.targetMineral,
                  resolutionM: args.resolutionM,
                  aoi: args.aoi,
                  aoiAreaKm2: args.aoiAreaKm2,
                  results: cells,
                  agentResults,
                }
                store.addRun(record)
              }
            } else if (event.event === 'job_complete') {
              finishRun('completed')
            } else if (event.event === 'error') {
              args.onError(event.message ?? 'Analysis failed')
              finishRun('failed')
            }
          },
          (err) => {
            args.onError(err.message || 'Lost connection to analysis stream')
            appendLog({ level: 'error', message: err.message || 'Stream disconnected' })
            finishRun('failed')
          },
          controller.signal
        )
      } catch (err) {
        // An abort surfaces here only if it lands before the stream opens;
        // stopRun() has already set the status, so do not overwrite it.
        if (useAppStore.getState().runStatus === 'running') {
          const msg = err instanceof Error ? err.message : 'Failed to start analysis'
          args.onError(msg)
          appendLog({ level: 'error', message: msg })
          finishRun('failed')
        }
      }
    },
    [startRun, handleEvent, finishRun, appendLog]
  )

  const stop = useCallback(() => {
    appendLog({ level: 'warn', message: 'Stopped by user — in-flight LLM calls cancelled' })
    stopRun()
  }, [appendLog, stopRun])

  return { run, stop }
}
