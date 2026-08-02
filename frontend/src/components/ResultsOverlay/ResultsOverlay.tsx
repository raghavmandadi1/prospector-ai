import { useAppStore } from '../../store'

export default function ResultsOverlay() {
  const { analysisResults, currentJob, targetMineral, shadingMode, setShadingMode } = useAppStore()

  if (analysisResults.length === 0) return null

  const highCount = analysisResults.filter((c) => c.tier === 'high').length
  const medCount = analysisResults.filter((c) => c.tier === 'medium').length
  const interpolated = analysisResults.some((c) => c.parent_cell_id)
  const mineral = (currentJob?.target_mineral ?? targetMineral)?.toUpperCase()

  return (
    <>
      {/* Results summary bar */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-gray-900/90 backdrop-blur rounded-lg px-4 py-2 text-xs text-white shadow-lg flex items-center gap-4 z-10">
        <span className="font-medium">{mineral} Analysis</span>
        <span className="text-gray-400">{analysisResults.length} cells</span>
        <span className="text-red-400">{highCount} high</span>
        <span className="text-orange-400">{medCount} medium</span>
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
              title="Shade cells relative to the other cells in this AOI"
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
              <span>worst in AOI</span>
              <span>best in AOI</span>
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
            Ranking within this polygon only — a "best" cell here may still be
            weak in absolute terms. Click a cell to see its absolute score.
          </p>
        )}
        {interpolated && (
          <p className="text-[10px] text-gray-500 leading-snug mt-1">
            Fine cells interpolated from a coarser analysis grid.
          </p>
        )}
      </div>
    </>
  )
}
