import { useEffect, useState } from 'react'

import { approveReviewItem, getReviewQueue, rejectReviewItem } from '../api/pipeline'
import { APPLICATION_STATUSES, type Application } from '../types/application'
import type { ReviewDecision, ReviewItem } from '../types/pipeline'
import { STATUS_LABELS } from '../constants'

interface Props {
  applications: Application[]
  onApplicationsChanged: () => void
  refreshSignal: number
}

interface EditState {
  company: string
  position: string
  status: string
  status_date: string
}

function emptyEdit(item: ReviewItem): EditState {
  return {
    company: item.extracted_company ?? '',
    position: item.extracted_position ?? '',
    status: item.extracted_status ?? 'applied',
    status_date: item.extracted_status_date ?? '',
  }
}

export function ReviewQueue({ applications, onApplicationsChanged, refreshSignal }: Props) {
  const [items, setItems] = useState<ReviewItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [edit, setEdit] = useState<EditState | null>(null)

  const refresh = () => {
    getReviewQueue()
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load review queue'))
  }

  useEffect(refresh, [refreshSignal])

  const startEditing = (item: ReviewItem) => {
    setEditingId(item.id)
    setEdit(emptyEdit(item))
  }

  const handleApprove = async (item: ReviewItem) => {
    setError(null)
    try {
      const edits: ReviewDecision =
        editingId === item.id && edit
          ? {
              company: edit.company || undefined,
              position: edit.position || undefined,
              status: edit.status || undefined,
              status_date: edit.status_date || undefined,
            }
          : {}
      await approveReviewItem(item.id, edits)
      setEditingId(null)
      setEdit(null)
      refresh()
      onApplicationsChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve item')
    }
  }

  const handleReject = async (item: ReviewItem) => {
    setError(null)
    try {
      await rejectReviewItem(item.id)
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reject item')
    }
  }

  if (items.length === 0) return null

  return (
    <section className="space-y-3 rounded border border-amber-200 bg-amber-50 p-4">
      <h2 className="text-sm font-semibold text-gray-900">
        Needs review ({items.length})
      </h2>

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      <ul className="space-y-3">
        {items.map((item) => {
          const matched = item.matched_application_id
            ? applications.find((a) => a.id === item.matched_application_id)
            : undefined

          return (
            <li key={item.id} className="rounded border border-gray-200 bg-white p-3 text-sm">
              <p className="font-medium text-gray-900">{item.subject}</p>
              <p className="text-gray-500">{item.sender}</p>
              <p className="mt-1 text-gray-600">{item.snippet}</p>

              {matched && (
                <p className="mt-2 rounded bg-gray-50 p-2 text-xs text-gray-600">
                  Current: {matched.company} — {matched.position} ({STATUS_LABELS[matched.status]}
                  {matched.applied_at ? `, ${matched.applied_at}` : ''})
                </p>
              )}

              <p className="mt-1 text-gray-700">
                {item.proposed_action === 'update' ? 'Update to' : 'Create'}: {item.extracted_company ?? '?'} —{' '}
                {item.extracted_position ?? '?'} ({item.extracted_status ?? 'unknown status'}) — confidence{' '}
                {Math.round(item.confidence * 100)}%
              </p>

              {editingId === item.id && edit && (
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <input
                    className="rounded border border-gray-300 px-2 py-1 text-sm"
                    placeholder="Company"
                    value={edit.company}
                    onChange={(e) => setEdit({ ...edit, company: e.target.value })}
                  />
                  <input
                    className="rounded border border-gray-300 px-2 py-1 text-sm"
                    placeholder="Position"
                    value={edit.position}
                    onChange={(e) => setEdit({ ...edit, position: e.target.value })}
                  />
                  <select
                    className="rounded border border-gray-300 px-2 py-1 text-sm"
                    value={edit.status}
                    onChange={(e) => setEdit({ ...edit, status: e.target.value })}
                  >
                    {APPLICATION_STATUSES.map((value) => (
                      <option key={value} value={value}>
                        {STATUS_LABELS[value]}
                      </option>
                    ))}
                  </select>
                  <input
                    type="date"
                    className="rounded border border-gray-300 px-2 py-1 text-sm"
                    value={edit.status_date}
                    onChange={(e) => setEdit({ ...edit, status_date: e.target.value })}
                  />
                </div>
              )}

              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => handleApprove(item)}
                  className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
                >
                  Approve
                </button>
                <button
                  onClick={() => startEditing(item)}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  Edit
                </button>
                <button
                  onClick={() => handleReject(item)}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  Reject
                </button>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
