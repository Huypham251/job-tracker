import { useState } from 'react'

import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import type { User } from '../types/user'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'
import { GmailPanel } from './GmailPanel'
import { ReviewQueue } from './ReviewQueue'
import { SyncPanel } from './SyncPanel'
import { UserMenu } from './UserMenu'

interface Props {
  user: User
  onLogout: () => void
}

export function ApplicationsPage({ user, onLogout }: Props) {
  const { applications, loading, error, refetch, create, update, remove } = useApplications()
  const [editing, setEditing] = useState<Application | null>(null)
  const [reviewRefreshSignal, setReviewRefreshSignal] = useState(0)

  const handleSyncCompleted = () => {
    void refetch()
    setReviewRefreshSignal((n) => n + 1)
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

      <SyncPanel onSyncCompleted={handleSyncCompleted} />

      <ReviewQueue
        applications={applications}
        onApplicationsChanged={() => void refetch()}
        refreshSignal={reviewRefreshSignal}
      />

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
