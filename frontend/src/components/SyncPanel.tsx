import { useEffect, useRef, useState } from 'react'

import { getLatestSyncJob, getSyncJob, startSync } from '../api/sync'
import type { SyncJob } from '../types/sync'

const POLL_INTERVAL_MS = 2000

interface Props {
  onSyncCompleted: () => void
}

export function SyncPanel({ onSyncCompleted }: Props) {
  const [job, setJob] = useState<SyncJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

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
          setJob(updated)
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
        setJob(latest)
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
    try {
      const started = await startSync()
      setJob(started)
      pollJob(started.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start sync')
    }
  }

  const isActive = job?.status === 'queued' || job?.status === 'running'

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
      {job && isActive && (
        <p className="text-sm text-gray-600">
          {job.job_type === 'initial' ? 'Initial sync' : 'Incremental sync'} running —{' '}
          {job.messages_processed} of {job.messages_seen || '?'} messages processed.
        </p>
      )}
      {job && job.status === 'completed' && (
        <p className="text-sm text-gray-600">
          Synced: {job.auto_applied} auto-applied, {job.queued_for_review} queued for review,{' '}
          {job.ignored} ignored{job.failed_count > 0 ? `, ${job.failed_count} failed` : ''}.
        </p>
      )}
      {job && job.status === 'failed' && (
        <p className="text-sm text-red-700">Sync failed: {job.error_message}</p>
      )}
    </section>
  )
}
