import type { Application } from '../types/application'
import { STATUS_LABELS, STATUS_STYLES } from '../constants'

interface Props {
  application: Application
  onEdit: () => void
  onDelete: () => void
}

export function ApplicationRow({ application, onEdit, onDelete }: Props) {
  function handleDelete() {
    if (window.confirm(`Delete the ${application.company} application?`)) {
      onDelete()
    }
  }

  return (
    <tr className="border-b border-gray-100 last:border-0">
      <td className="px-4 py-3 text-sm font-medium text-gray-900">{application.company}</td>
      <td className="px-4 py-3 text-sm text-gray-700">{application.position}</td>
      <td className="px-4 py-3 text-sm">
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[application.status]}`}
        >
          {STATUS_LABELS[application.status]}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-gray-700">{application.applied_at ?? '—'}</td>
      <td className="px-4 py-3 text-right text-sm">
        <button onClick={onEdit} className="mr-3 text-blue-600 hover:underline">
          Edit
        </button>
        <button onClick={handleDelete} className="text-red-600 hover:underline">
          Delete
        </button>
      </td>
    </tr>
  )
}
