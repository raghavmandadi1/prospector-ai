import React, { useState } from 'react'
import MapView from './components/Map/MapView'
import AnalysisPanel from './components/AnalysisPanel/AnalysisPanel'
import ResultsOverlay from './components/ResultsOverlay/ResultsOverlay'
import EvidenceDrawer from './components/EvidenceDrawer/EvidenceDrawer'
import ChannelDashboard from './components/ChannelDashboard/ChannelDashboard'
import { useAppStore } from './store'

type SidebarView = 'analysis' | 'channels'

export default function App() {
  const [sidebarView, setSidebarView] = useState<SidebarView>('analysis')
  const { selectedCell, setSelectedCell } = useAppStore()

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-gray-900 text-white">
      {/* Left sidebar */}
      <div className="w-80 flex-shrink-0 flex flex-col border-r border-gray-700 bg-gray-800 z-10">
        {/* Sidebar header / tab switcher */}
        <div className="flex border-b border-gray-700">
          <button
            onClick={() => setSidebarView('analysis')}
            className={`flex-1 px-4 py-3 text-sm font-medium transition-colors ${
              sidebarView === 'analysis'
                ? 'bg-gray-700 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Analysis
          </button>
          <button
            onClick={() => setSidebarView('channels')}
            className={`flex-1 px-4 py-3 text-sm font-medium transition-colors ${
              sidebarView === 'channels'
                ? 'bg-gray-700 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Channels
          </button>
        </div>

        {/* Sidebar content */}
        <div className="flex-1 overflow-y-auto">
          {sidebarView === 'analysis' ? (
            <AnalysisPanel />
          ) : (
            <ChannelDashboard />
          )}
        </div>

        {/* Branding */}
        <div className="px-4 py-3 border-t border-gray-700 text-xs text-gray-500">
          GeoProspector v0.1
        </div>
      </div>

      {/* Main map area */}
      <div className="flex-1 relative">
        <MapView />
        <ResultsOverlay />
      </div>

      {/* Evidence drawer (slides in from right on cell click) */}
      <EvidenceDrawer
        cell={selectedCell}
        onClose={() => setSelectedCell(null)}
      />
    </div>
  )
}
