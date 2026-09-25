import { useEffect, useRef, useState } from 'react'

import { GMAIL_CONNECT_URL, GMAIL_REAUTH_CODE } from '../api/gmail'
import { ApiError } from '../api/http'
import { getLatestSyncJob, getSyncJob, startSync } from '../api/sync'
import type { SyncJob } from '../types/sync'

const POLL_INTERVAL_MS = 2000
// A dispatched worker normally starts within ~15-30s; past this, offer a Retry
// (which re-requests the worker) instead of an open-ended "Starting…".
const QUEUED_HINT_AFTER_MS = 2 * 60 * 1000

interface Props {
  onSyncCompleted: () => void
}

export function SyncPanel({ onSyncCompleted }: Props) {
  const [job, setJob] = useState<SyncJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Set when POST /sync is refused because the Gmail grant is gone (403).
  const [reauthRefused, setReauthRefused] = useState(false)
  const [queuedSeenAt, setQueuedSeenAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const pollRef = useRef<number | null>(null)

  // Every job update goes through here so "how long has it sat in the
  // queue" is tracked from the first moment this page saw it queued.
  const applyJob = (next: SyncJob | null) => {
    setJob(next)
    setNow(Date.now())
    setQueuedSeenAt((seenAt) =>
      next?.status === 'queued' ? (seenAt ?? Date.now()) : null,
    )
  }

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const pollJob = (id: string) => {
    stopPolling()
    pollRef.current = window.setInterval(() => {
      getSyncJob(id)
        .then((updated) => {
          applyJob(updated)
          if (updated.status === 'completed' || updated.status === 'failed') {
            stopPolling()
            onSyncCompleted()
          }
        })
        .catch((err) => {
          stopPolling()
          setError(err instanceof Error ? err.message : 'Failed to check sync status')
        })
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    getLatestSyncJob()
      .then((latest) => {
        applyJob(latest)
        if (latest && (latest.status === 'queued' || latest.status === 'running')) {
          pollJob(latest.id)
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load sync status'))

    return stopPolling
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSync = async () => {
    setError(null)
    setReauthRefused(false)
    try {
      const started = await startSync()
      applyJob(started)
      pollJob(started.id)
    } catch (err) {
      setReauthRefused(err instanceof ApiError && err.code === GMAIL_REAUTH_CODE)
      setError(err instanceof Error ? err.message : 'Failed to start sync')
    }
  }

  // POST /sync on an already-queued job returns it (409) and asks the backend
  // to request the worker again — same job, fresh start attempt.
  const handleRetry = async () => {
    setError(null)
    try {
      const current = await startSync()
      setQueuedSeenAt(null)
      applyJob(current)
      pollJob(current.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to restart sync')
    }
  }

  const isActive = job?.status === 'queued' || job?.status === 'running'
  const needsReconnect =
    reauthRefused || (job?.status === 'failed' && job.error_code === GMAIL_REAUTH_CODE)
  const waitingTooLong =
    job?.status === 'queued' && queuedSeenAt !== null && now - queuedSeenAt > QUEUED_HINT_AFTER_MS

  return (
    <section className="space-y-2 rounded border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold text-gray-900">Gmail Sync</h2>
        <button
          onClick={handleSync}
          disabled={isActive}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {isActive ? 'Syncing…' : 'Sync Gmail'}
        </button>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {job && job.status === 'queued' && (
        <p className="text-sm text-gray-600">
          Starting {job.job_type === 'initial' ? 'your first Gmail import' : 'sync'}…
        </p>
      )}
      {job && job.status === 'running' && (
        <p className="text-sm text-gray-600">
          {job.job_type === 'initial' ? 'Importing' : 'Syncing'} — {job.messages_processed} of{' '}
          {job.messages_seen || '?'} messages processed.
        </p>
      )}
      {job && isActive && job.job_type === 'initial' && (
        <p className="text-xs text-gray-500">
          The first import runs in the background and can take up to an hour for a large
          mailbox. You can close this page.
        </p>
      )}
      {waitingTooLong && (
        <p className="text-sm text-amber-700">
          The sync worker hasn&apos;t started yet.{' '}
          <button onClick={handleRetry} className="font-medium underline">
            Retry
          </button>
        </p>
      )}
      {job && job.status === 'completed' && (
        <p className="text-sm text-gray-600">
          Synced: {job.auto_applied} auto-applied, {job.queued_for_review} queued for review,{' '}
          {job.ignored} ignored{job.failed_count > 0 ? `, ${job.failed_count} failed` : ''}.
        </p>
      )}
      {job && job.status === 'failed' && !reauthRefused && (
        <p className="text-sm text-red-700">Sync failed: {job.error_message}</p>
      )}
      {needsReconnect && (
        <p className="text-sm">
          <a href={GMAIL_CONNECT_URL} className="font-medium text-amber-700 underline">
            Reconnect Gmail
          </a>{' '}
          <span className="text-gray-500">— your sync history is kept.</span>
        </p>
      )}
    </section>
  )
}
