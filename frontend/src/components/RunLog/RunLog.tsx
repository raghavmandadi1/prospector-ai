/**
 * Run log — a console docked to the bottom of the map.
 *
 * Collapsed it is a one-line ledger: elapsed, agents finished, how many of
 * them are actually grounded on a knowledge file, tokens, estimated cost, and
 * Stop. Expanded it streams every telemetry event from the backend.
 *
 * The "grounded" counter is the point of this thing. Four of six agents ship
 * with no knowledge file and run with system=None while still contributing
 * full weight to the composite; before this panel existed, nothing in the UI
 * distinguished a grounded score from an ungrounded one.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useAppStore } from '../../store'
import { AGENT_LABELS } from '../../hooks/useAnalysisRunner'
import type { LogEntry } from '../../types'

const LEVEL_DOT: Record<LogEntry['level'], string> = {
  info: 'bg-gray-600',
  success: 'bg-emerald-500',
  warn: 'bg-amber-400',
  error: 'bg-red-500',
}

const LEVEL_TEXT: Record<LogEntry['level'], string> = {
  info: 'text-gray-300',
  success: 'text-gray-300',
  warn: 'text-amber-300',
  error: 'text-red-300',
}

const LEVEL_ROW: Record<LogEntry['level'], string> = {
  info: '',
  success: '',
  warn: 'bg-amber-500/5',
  error: 'bg-red-500/5',
}

function elapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}.${String(
    Math.floor((ms % 1000) / 100)
  )}`
}

function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export default function RunLog() {
  const runStatus = useAppStore((s) => s.runStatus)
  const runStartedAt = useAppStore((s) => s.runStartedAt)
  const runLog = useAppStore((s) => s.runLog)
  const runUsage = useAppStore((s) => s.runUsage)
  const agentPhase = useAppStore((s) => s.agentPhase)
  const agentGrounding = useAppStore((s) => s.agentGrounding)
  const logOpen = useAppStore((s) => s.logOpen)
  const setLogOpen = useAppStore((s) => s.setLogOpen)
  const stopRun = useAppStore((s) => s.stopRun)
  const appendLog = useAppStore((s) => s.appendLog)

  const [warnOnly, setWarnOnly] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [now, setNow] = useState(Date.now())
  const [copied, setCopied] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)

  const isRunning = runStatus === 'running'

  // Tick the elapsed clock only while a run is live.
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [isRunning])

  // Follow the tail, but stop following the moment the user scrolls up to
  // read something — nothing is more hostile than a log that yanks itself
  // away mid-sentence.
  useEffect(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [runLog, logOpen])

  const entries = useMemo(
    () => (warnOnly ? runLog.filter((e) => e.level === 'warn' || e.level === 'error') : runLog),
    [runLog, warnOnly]
  )

  const agentIds = Object.keys(agentPhase)
  const doneCount = agentIds.filter((id) => agentPhase[id] === 'done' || agentPhase[id] === 'failed').length
  const groundedIds = Object.entries(agentGrounding).filter(([, f]) => f !== null)
  const groundingKnown = Object.keys(agentGrounding).length > 0
  const ungrounded = groundingKnown && groundedIds.length < Object.keys(agentGrounding).length

  const warnCount = runLog.filter((e) => e.level === 'warn' || e.level === 'error').length
  // While running the clock ticks live; once finished it freezes at the
  // timestamp of the last event rather than drifting with wall time.
  const elapsedMs = !runStartedAt
    ? 0
    : isRunning
    ? now - runStartedAt
    : runLog[runLog.length - 1]?.t ?? 0

  if (runStatus === 'idle') return null

  async function copyLog() {
    const text = runLog
      .map((e) => {
        const head = `[${elapsed(e.t)}] ${e.level.toUpperCase()} ${e.agentId ?? '-'} :: ${e.message}${
          e.metric ? `  (${e.metric})` : ''
        }`
        return e.detail ? `${head}\n    ${e.detail.replace(/\n/g, '\n    ')}` : head
      })
      .join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      appendLog({ level: 'error', message: 'Clipboard write blocked by the browser' })
    }
  }

  return (
    <div className="absolute bottom-0 left-0 right-0 z-20 border-t border-gray-700 bg-gray-900">
      {/* Ledger bar */}
      <div className="flex items-stretch h-11 px-4 gap-6">
        {/* Status */}
        <div className="flex items-center gap-2 min-w-[7.5rem]">
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${
              isRunning
                ? 'bg-blue-400 animate-pulse motion-reduce:animate-none'
                : runStatus === 'completed'
                ? 'bg-emerald-500'
                : runStatus === 'stopped'
                ? 'bg-amber-400'
                : 'bg-red-500'
            }`}
          />
          <span className="text-[13px] text-gray-200 capitalize">{runStatus}</span>
        </div>

        <Stat label="Elapsed" value={elapsed(Math.max(0, elapsedMs))} />
        <Stat label="Agents" value={`${doneCount}/${agentIds.length}`} />
        <Stat
          label="Grounded"
          value={groundingKnown ? `${groundedIds.length}/${Object.keys(agentGrounding).length}` : '—'}
          tone={ungrounded ? 'warn' : 'normal'}
          title={
            ungrounded
              ? 'Some agents ran with no knowledge file. Their scores are ungrounded model prior but carry full weight.'
              : undefined
          }
        />
        <Stat label="Tokens" value={`${tokens(runUsage.inputTokens)}→${tokens(runUsage.outputTokens)}`} />
        <Stat label="Est. cost" value={`$${runUsage.estCostUsd.toFixed(4)}`} />

        <div className="flex-1" />

        <div className="flex items-center gap-2">
          {isRunning && (
            <button
              onClick={stopRun}
              className="h-8 px-4 rounded bg-red-600 hover:bg-red-500 text-white text-[13px] font-medium transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
            >
              Stop
            </button>
          )}
          <button
            onClick={() => setLogOpen(!logOpen)}
            aria-expanded={logOpen}
            className="h-8 px-3 rounded border border-gray-700 hover:bg-gray-800 text-gray-300 text-[13px] transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
          >
            {logOpen ? 'Hide log' : `Show log (${runLog.length})`}
            {!logOpen && warnCount > 0 && (
              <span className="ml-2 text-amber-300">{warnCount} ⚠</span>
            )}
          </button>
        </div>
      </div>

      {/* Log stream */}
      {logOpen && (
        <>
          <div className="flex items-center gap-3 h-9 px-4 border-t border-gray-800 bg-gray-900">
            <button
              onClick={() => setWarnOnly(!warnOnly)}
              className={`h-6 px-2.5 rounded text-[12px] transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
                warnOnly
                  ? 'bg-amber-400/15 text-amber-300 border border-amber-400/40'
                  : 'border border-gray-700 text-gray-400 hover:text-gray-200'
              }`}
            >
              Warnings only ({warnCount})
            </button>
            <button
              onClick={copyLog}
              className="h-6 px-2.5 rounded border border-gray-700 text-[12px] text-gray-400 hover:text-gray-200 transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
            >
              {copied ? 'Copied' : 'Copy log'}
            </button>
            <span className="text-[12px] text-gray-500">
              In-memory only — this log is lost on reload.
            </span>
          </div>

          <div
            ref={scrollRef}
            onScroll={(e) => {
              const el = e.currentTarget
              pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
            }}
            className="h-72 overflow-y-auto border-t border-gray-800"
          >
            {entries.length === 0 ? (
              <p className="px-4 py-6 text-[13px] text-gray-500">
                {warnOnly ? 'No warnings.' : 'Waiting for the first event…'}
              </p>
            ) : (
              entries.map((e) => {
                const open = expandedId === e.id
                return (
                  <div key={e.id} className={LEVEL_ROW[e.level]}>
                    <button
                      onClick={() => setExpandedId(open ? null : e.detail ? e.id : null)}
                      className={`w-full flex items-baseline gap-3 px-4 py-1 text-left text-[13px] leading-6 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-blue-400 ${
                        e.detail ? 'hover:bg-gray-800/60 cursor-pointer' : 'cursor-default'
                      }`}
                    >
                      <span className="w-14 flex-shrink-0 font-mono tabular-nums text-gray-500">
                        {elapsed(e.t)}
                      </span>
                      <span
                        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 self-center ${LEVEL_DOT[e.level]}`}
                      />
                      <span className="w-28 flex-shrink-0 truncate text-gray-500">
                        {e.agentId ? AGENT_LABELS[e.agentId] ?? e.agentId : ''}
                      </span>
                      <span className={`flex-1 min-w-0 ${LEVEL_TEXT[e.level]}`}>
                        {e.message}
                        {e.detail && (
                          <span className="text-gray-600 ml-2">{open ? '−' : '+'}</span>
                        )}
                      </span>
                      {e.metric && (
                        <span className="flex-shrink-0 font-mono tabular-nums text-gray-500">
                          {e.metric}
                        </span>
                      )}
                    </button>
                    {open && e.detail && (
                      <pre className="mx-4 mb-2 ml-[4.25rem] px-3 py-2 rounded bg-gray-950 border border-gray-800 text-[12px] leading-5 text-gray-400 whitespace-pre-wrap break-words max-h-64 overflow-y-auto">
                        {e.detail}
                      </pre>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  tone = 'normal',
  title,
}: {
  label: string
  value: string
  tone?: 'normal' | 'warn'
  title?: string
}) {
  return (
    <div className="flex flex-col justify-center leading-none" title={title}>
      <span className="text-[11px] uppercase tracking-wider text-gray-500">{label}</span>
      <span
        className={`text-[17px] font-mono tabular-nums mt-0.5 ${
          tone === 'warn' ? 'text-amber-300' : 'text-gray-200'
        }`}
      >
        {value}
      </span>
    </div>
  )
}
