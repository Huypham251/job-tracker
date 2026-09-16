import { useEffect, useState } from 'react'

import { approveReviewItem, getReviewQueue, rejectReviewItem } from '../api/pipeline'
import type { ReviewItem } from '../types/pipeline'

interface Props {
  onApplicationsChanged: () => void
}

export function ReviewQueue({ onApplicationsChanged }: Props) {
  const [items, setItems] = useState<ReviewItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editedCompany, setEditedCompany] = useState('')

  const refresh = () => {
    getReviewQueue()
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load review queue'))
  }

  useEffect(refresh, [])

  const handleApprove = async (item: ReviewItem) => {
    setError(null)
    try {
      const edits = editingId === item.id && editedCompany ? { company: editedCompany } : {}
      await approveReviewItem(item.id, edits)
      setEditingId(null)
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
        {items.map((item) => (
          <li key={item.id} className="rounded border border-gray-200 bg-white p-3 text-sm">
            <p className="font-medium text-gray-900">{item.subject}</p>
            <p className="text-gray-500">{item.sender}</p>
            <p className="mt-1 text-gray-600">{item.snippet}</p>
            <p className="mt-1 text-gray-700">
              {item.proposed_action === 'update' ? 'Update' : 'Create'}: {item.extracted_company ?? '?'} —{' '}
              {item.extracted_position ?? '?'} ({item.extracted_status ?? 'unknown status'}) — confidence{' '}
              {Math.round(item.confidence * 100)}%
            </p>
            {editingId === item.id && (
              <input
                className="mt-2 w-full rounded border border-gray-300 px-2 py-1 text-sm"
                placeholder="Correct company name"
                value={editedCompany}
                onChange={(e) => setEditedCompany(e.target.value)}
              />
            )}
            <div className="mt-2 flex gap-2">
              <button
                onClick={() => handleApprove(item)}
                className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
              >
                Approve
              </button>
              <button
                onClick={() => {
                  setEditingId(item.id)
                  setEditedCompany(item.extracted_company ?? '')
                }}
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
        ))}
      </ul>
    </section>
  )
}
