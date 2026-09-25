import { useEffect, useState } from 'react'

import {
  disconnectGmail,
  fetchGmailMessages,
  getGmailStatus,
  GMAIL_CONNECT_URL,
} from '../api/gmail'
import type { GmailMessageSummary, GmailStatus } from '../types/gmail'

interface Props {
  // Lets the dashboard hide Gmail-only UI (the Sync panel) for users who
  // track applications manually without connecting Gmail.
  onConnectionChange?: (connected: boolean) => void
}

export function GmailPanel({ onConnectionChange }: Props) {
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [messages, setMessages] = useState<GmailMessageSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getGmailStatus()
      .then((loaded) => {
        setStatus(loaded)
        onConnectionChange?.(loaded.connected)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load Gmail status'))
      .finally(() => setLoading(false))
    // onConnectionChange must be stable (the dashboard passes a useState
    // setter), or this would refetch the status on every render.
  }, [onConnectionChange])

  const handleDisconnect = async () => {
    setError(null)
    try {
      await disconnectGmail()
      setStatus({ connected: false, email: null, connected_at: null })
      onConnectionChange?.(false)
      setMessages(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disconnect Gmail')
    }
  }

  const handleFetchMessages = async () => {
    setError(null)
    try {
      setMessages(await fetchGmailMessages())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch Gmail messages')
    }
  }

  if (loading) return null

  return (
    <section className="space-y-3 rounded border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Gmail</h2>
          <p className="text-sm text-gray-500">
            {status?.connected ? `Connected as ${status.email}` : 'Not connected'}
          </p>
        </div>
        {status?.connected ? (
          <div className="flex gap-2">
            <button
              onClick={handleFetchMessages}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Fetch recent messages
            </button>
            <button
              onClick={handleDisconnect}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Disconnect
            </button>
          </div>
        ) : (
          <a
            href={GMAIL_CONNECT_URL}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            Connect Gmail
          </a>
        )}
      </div>

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      {messages && (
        <ul className="divide-y divide-gray-100">
          {messages.length === 0 && <li className="py-2 text-sm text-gray-500">No messages found.</li>}
          {messages.map((message) => (
            <li key={message.id} className="py-2 text-sm">
              <p className="font-medium text-gray-900">{message.subject || '(no subject)'}</p>
              <p className="text-gray-500">
                {message.from_} · {message.date}
              </p>
              <p className="text-gray-600">{message.snippet}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
