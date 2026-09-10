import type { Application } from '../types/application'
import { ApplicationRow } from './ApplicationRow'

interface Props {
  applications: Application[]
  onEdit: (application: Application) => void
  onDelete: (id: string) => void
}

export function ApplicationList({ applications, onEdit, onDelete }: Props) {
  if (applications.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
        No applications yet. Add your first one above.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="min-w-full">
        <thead>
          <tr className="border-b border-gray-200 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
            <th className="px-4 py-3">Company</th>
            <th className="px-4 py-3">Position</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Applied</th>
            <th className="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {applications.map((application) => (
            <ApplicationRow
              key={application.id}
              application={application}
              onEdit={() => onEdit(application)}
              onDelete={() => onDelete(application.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
