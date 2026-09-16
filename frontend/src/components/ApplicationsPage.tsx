import { useState } from 'react'

import { processInbox } from '../api/pipeline'
import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import type { User } from '../types/user'
import type { ProcessResult } from '../types/pipeline'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'
import { GmailPanel } from './GmailPanel'
import { ReviewQueue } from './ReviewQueue'
import { UserMenu } from './UserMenu'

interface Props {
  user: User
  onLogout: () => void
}

export function ApplicationsPage({ user, onLogout }: Props) {
  const { applications, loading, error, refetch, create, update, remove } = useApplications()
  const [editing, setEditing] = useState<Application | null>(null)
  const [processResult, setProcessResult] = useState<ProcessResult | null>(null)
  const [processError, setProcessError] = useState<string | null>(null)
  const [processing, setProcessing] = useState(false)

  const handleProcessInbox = async () => {
    setProcessing(true)
    setProcessError(null)
    try {
      const result = await processInbox()
      setProcessResult(result)
      await refetch()
    } catch (err) {
      setProcessError(err instanceof Error ? err.message : 'Failed to process inbox')
    } finally {
      setProcessing(false)
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 sm:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Job Application Tracker</h1>
          <p className="text-sm text-gray-500">
            {applications.length} application{applications.length === 1 ? '' : 's'}
          </p>
        </div>
        <UserMenu user={user} onLogout={onLogout} />
      </header>

      <GmailPanel />

      <section className="space-y-2 rounded border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-gray-900">Pipeline</h2>
          <button
            onClick={handleProcessInbox}
            disabled={processing}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {processing ? 'Processing…' : 'Process Inbox'}
          </button>
        </div>
        {processError && <p className="text-sm text-red-700">{processError}</p>}
        {processResult && (
          <p className="text-sm text-gray-600">
            Processed {processResult.processed}: {processResult.auto_applied} auto-applied,{' '}
            {processResult.queued_for_review} queued for review, {processResult.ignored} ignored.
          </p>
        )}
      </section>

      <ReviewQueue applications={applications} onApplicationsChanged={() => void refetch()} />

      {editing ? (
        <ApplicationForm
          key={editing.id}
          initial={editing}
          submitLabel="Save changes"
          onSubmit={async (data) => {
            await update(editing.id, data)
            setEditing(null)
          }}
          onCancel={() => setEditing(null)}
        />
      ) : (
        <ApplicationForm submitLabel="Add application" onSubmit={create} />
      )}

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <ApplicationList
          applications={applications}
          onEdit={setEditing}
          onDelete={(id) => {
            void remove(id)
          }}
        />
      )}
    </div>
  )
}
