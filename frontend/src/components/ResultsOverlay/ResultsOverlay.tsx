import { useAppStore } from '../../store'
import { NORMALIZATION_SCOPE_LABEL, NOVELTY, NOVELTY_ORDER } from '../../types'
import type { NormalizationScope, NoveltyClass } from '../../types'

export default function ResultsOverlay() {
  const { analysisResults, currentJob, targetMineral, shadingMode, setShadingMode } = useAppStore()

  if (analysisResults.length === 0) return null

  const highCount = analysisResults.filter((c) => c.tier === 'high').length
  const medCount = analysisResults.filter((c) => c.tier === 'medium').length
  const interpolated = analysisResults.some((c) => c.parent_cell_id)
  const mineral = (currentJob?.target_mineral ?? targetMineral)?.toUpperCase()

  // What population the relative shading is relative TO. "Top 10%" is a
  // different claim for a drawn polygon than for a whole corridor sweep, and a
  // legend that does not say which is quietly asserting the wrong one. Absent
  // on runs predating the field, which were all AOI-scoped.
  const scope: NormalizationScope =
    analysisResults.find((c) => c.normalization_scope)?.normalization_scope ?? 'aoi'
  const scopeLabel = NORMALIZATION_SCOPE_LABEL[scope]

  // Novelty is optional: absent on older runs and wherever the occurrence
  // extract was never built. If no cell carries it, the whole block stays off
  // rather than showing three zeroes that look like a finding.
  const noveltyCounts = analysisResults.reduce(
    (acc, c) => {
      if (c.novelty && c.novelty in acc) acc[c.novelty] += 1
      return acc
    },
    { lead: 0, extends: 0, confirms: 0 } as Record<NoveltyClass, number>
  )
  const hasNovelty = NOVELTY_ORDER.some((c) => noveltyCounts[c] > 0)
  // The headline number: high-scoring ground with nothing recorded near it.
  const highLeads = analysisResults.filter(
    (c) => c.tier === 'high' && c.novelty === 'lead'
  ).length

  return (
    <>
      {/* Results summary bar */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-gray-900/90 backdrop-blur rounded-lg px-4 py-2 text-xs text-white shadow-lg flex items-center gap-4 z-10">
        <span className="font-medium">{mineral} Analysis</span>
        <span className="text-gray-400">{analysisResults.length} cells</span>
        <span className="text-red-400">{highCount} high</span>
        <span className="text-orange-400">{medCount} medium</span>
        {hasNovelty && (
          <span
            style={{ color: NOVELTY.lead.color }}
            title="High-scoring cells with nothing recorded nearby — leads rather than re-discoveries"
          >
            {highLeads} novel
          </span>
        )}
      </div>

      {/* Legend */}
      <div className="absolute bottom-8 right-4 bg-gray-900/90 backdrop-blur rounded-lg p-3 text-xs text-white shadow-lg z-10 w-52">
        <div className="flex items-center justify-between mb-2">
          <span className="font-medium text-gray-300">Prospectivity</span>
          <div className="flex rounded overflow-hidden border border-gray-600">
            <button
              onClick={() => setShadingMode('relative')}
              className={`px-1.5 py-0.5 text-[10px] ${
                shadingMode === 'relative'
                  ? 'bg-gray-200 text-gray-900 font-medium'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
              title={`Shade cells relative to the other cells in ${scopeLabel}`}
            >
              Relative
            </button>
            <button
              onClick={() => setShadingMode('absolute')}
              className={`px-1.5 py-0.5 text-[10px] ${
                shadingMode === 'absolute'
                  ? 'bg-gray-200 text-gray-900 font-medium'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
              title="Shade cells by absolute composite score"
            >
              Absolute
            </button>
          </div>
        </div>

        {/* Continuous ramp */}
        <div
          className="h-2 rounded-sm mb-1"
          style={{
            background:
              'linear-gradient(to right, #6b7280, #eab308 35%, #f97316 65%, #ef4444 90%, #b91c1c)',
          }}
        />
        <div className="flex justify-between text-[10px] text-gray-400 mb-2">
          {shadingMode === 'relative' ? (
            <>
              <span>worst in {scopeLabel}</span>
              <span>best in {scopeLabel}</span>
            </>
          ) : (
            <>
              <span>0.0</span>
              <span>1.0</span>
            </>
          )}
        </div>

        {shadingMode === 'relative' && (
          <p className="text-[10px] text-gray-500 leading-snug">
            {scope === 'region'
              ? 'Ranking across the whole swept region — "high" means top decile of the sweep, not of any one tile.'
              : 'Ranking within this polygon only — a "best" cell here may still be weak in absolute terms.'}{' '}
            Click a cell to see its absolute score.
          </p>
        )}
        {interpolated && (
          <p className="text-[10px] text-gray-500 leading-snug mt-1">
            Fine cells interpolated from a coarser analysis grid.
          </p>
        )}

        {/* Novelty — a separate axis from score, and drawn in a separate
            channel (cell outline, not fill) for exactly that reason. */}
        {hasNovelty && (
          <div className="mt-2 pt-2 border-t border-gray-700">
            <div className="font-medium text-gray-300 mb-1.5">
              Novelty <span className="text-gray-500 font-normal">— cell outline</span>
            </div>
            {NOVELTY_ORDER.map((cls) => (
              <div key={cls} className="flex items-start gap-2 mb-1">
                <span
                  className="w-4 flex-shrink-0 mt-[7px]"
                  style={{
                    borderTop: `2px ${NOVELTY[cls].borderStyle} ${NOVELTY[cls].color}`,
                  }}
                />
                <span className="text-[10px] leading-snug">
                  <span className="text-gray-200">{NOVELTY[cls].label}</span>
                  <span className="text-gray-500 tabular-nums"> · {noveltyCounts[cls]}</span>
                  <span className="block text-gray-500">{NOVELTY[cls].blurb}</span>
                </span>
              </div>
            ))}
            <p className="text-[10px] text-gray-500 leading-snug mt-1">
              Fill is the score, outline is whether anything is already recorded
              there. Cells with no outline were never checked.
            </p>
          </div>
        )}
      </div>
    </>
  )
}
