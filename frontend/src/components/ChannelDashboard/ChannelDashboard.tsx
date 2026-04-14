import React, { useEffect, useState } from 'react'
import { channelsApi } from '../../api/client'
import type { Channel } from '../../types'

export default function ChannelDashboard() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)
  const [syncingId, setSyncingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    channelsApi
      .list()
      .then(setChannels)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleSync(channelId: string) {
    setSyncingId(channelId)
    try {
      await channelsApi.sync(channelId)
      // Optimistic update — refresh list after short delay
      setTimeout(() => {
        channelsApi.list().then(setChannels).catch(console.error)
        setSyncingId(null)
      }, 1500)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed')
      setSyncingId(null)
    }
  }

  if (loading) {
    return (
      <div className="p-4 text-sm text-gray-400 text-center animate-pulse">
        Loading channels...
      </div>
    )
  }

  return (
    <div className="p-4 space-y-3">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
        Data Channels
      </h2>

      {error && (
        <p className="text-xs text-red-400 bg-red-900/30 rounded p-2">{error}</p>
      )}

      {channels.length === 0 ? (
        <p className="text-xs text-gray-500 italic">
          No channels registered. Use the API to add channels.
        </p>
      ) : (
        <div className="space-y-2">
          {channels.map((ch) => (
            <div
              key={ch.id}
              className="bg-gray-700 rounded-lg p-3 space-y-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-medium text-white">{ch.name}</div>
                  <div className="text-xs text-gray-400">{ch.source_type}</div>
                </div>
                <span
                  className={`flex-shrink-0 px-1.5 py-0.5 rounded text-xs ${
                    ch.is_active
                      ? 'bg-green-900 text-green-300'
                      : 'bg-gray-600 text-gray-400'
                  }`}
                >
                  {ch.is_active ? 'active' : 'inactive'}
                </span>
              </div>

              {ch.data_type && (
                <div className="text-xs text-gray-400">
                  Type: <span className="text-gray-300">{ch.data_type}</span>
                </div>
              )}

              {ch.last_synced_at && (
                <div className="text-xs text-gray-400">
                  Last synced:{' '}
                  <span className="text-gray-300">
                    {new Date(ch.last_synced_at).toLocaleString()}
                  </span>
                </div>
              )}

              <button
                onClick={() => handleSync(ch.id)}
                disabled={syncingId === ch.id}
                className="w-full py-1 px-3 rounded text-xs bg-gray-600 hover:bg-gray-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {syncingId === ch.id ? 'Syncing...' : 'Sync Now'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
