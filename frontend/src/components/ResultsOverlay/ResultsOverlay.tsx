import React from 'react'
import { useAppStore } from '../../store'

const TIERS = [
  { label: 'High', color: 'bg-red-500', range: '≥ 0.65' },
  { label: 'Medium', color: 'bg-orange-500', range: '0.40 – 0.65' },
  { label: 'Low', color: 'bg-yellow-500', range: '0.20 – 0.40' },
  { label: 'Negligible', color: 'bg-gray-500', range: '< 0.20' },
]

export default function ResultsOverlay() {
  const { analysisResults, currentJob } = useAppStore()

  if (analysisResults.length === 0) return null

  const highCount = analysisResults.filter((c) => (c.score ?? 0) >= 0.65).length
  const medCount = analysisResults.filter((c) => (c.score ?? 0) >= 0.4 && (c.score ?? 0) < 0.65).length

  return (
    <>
      {/* Results summary bar */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-gray-900/90 backdrop-blur rounded-lg px-4 py-2 text-xs text-white shadow-lg flex items-center gap-4 z-10">
        <span className="font-medium">{currentJob?.target_mineral?.toUpperCase()} Analysis</span>
        <span className="text-gray-400">{analysisResults.length} cells</span>
        <span className="text-red-400">{highCount} high</span>
        <span className="text-orange-400">{medCount} medium</span>
      </div>

      {/* Legend */}
      <div className="absolute bottom-8 right-4 bg-gray-900/90 backdrop-blur rounded-lg p-3 text-xs text-white shadow-lg z-10">
        <div className="font-medium mb-2 text-gray-300">Prospectivity</div>
        {TIERS.map((tier) => (
          <div key={tier.label} className="flex items-center gap-2 mb-1">
            <span className={`w-3 h-3 rounded-sm ${tier.color}`} />
            <span className="text-gray-300">{tier.label}</span>
            <span className="text-gray-500 ml-auto pl-4">{tier.range}</span>
          </div>
        ))}
      </div>
    </>
  )
}
