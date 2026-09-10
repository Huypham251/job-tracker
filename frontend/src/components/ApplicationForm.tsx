import { useState, type FormEvent } from 'react'

import {
  APPLICATION_STATUSES,
  type Application,
  type ApplicationCreate,
  type ApplicationStatus,
} from '../types/application'
import { STATUS_LABELS } from '../constants'

interface Props {
  initial?: Application
  submitLabel: string
  onSubmit: (data: ApplicationCreate) => Promise<void>
  onCancel?: () => void
}

export function ApplicationForm({ initial, submitLabel, onSubmit, onCancel }: Props) {
  const [company, setCompany] = useState(initial?.company ?? '')
  const [position, setPosition] = useState(initial?.position ?? '')
  const [status, setStatus] = useState<ApplicationStatus>(initial?.status ?? 'applied')
  const [appliedAt, setAppliedAt] = useState(initial?.applied_at ?? '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({
        company: company.trim(),
        position: position.trim(),
        status,
        applied_at: appliedAt || null,
      })
      if (!initial) {
        setCompany('')
        setPosition('')
        setStatus('applied')
        setAppliedAt('')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none'

  return (
    <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Company</span>
          <input
            className={inputClass}
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            required
            maxLength={255}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Position</span>
          <input
            className={inputClass}
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            required
            maxLength={255}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Status</span>
          <select
            className={inputClass}
            value={status}
            onChange={(e) => setStatus(e.target.value as ApplicationStatus)}
          >
            {APPLICATION_STATUSES.map((value) => (
              <option key={value} value={value}>
                {STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Applied on</span>
          <input
            type="date"
            className={inputClass}
            value={appliedAt ?? ''}
            onChange={(e) => setAppliedAt(e.target.value)}
          />
        </label>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
