import { useCallback, useEffect, useState } from 'react'

import * as api from '../api/applications'
import type {
  Application,
  ApplicationCreate,
  ApplicationUpdate,
} from '../types/application'

export function useApplications() {
  const [applications, setApplications] = useState<Application[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setApplications(await api.listApplications())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load applications')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refetch()
  }, [refetch])

  const create = useCallback(
    async (data: ApplicationCreate) => {
      await api.createApplication(data)
      await refetch()
    },
    [refetch],
  )

  const update = useCallback(
    async (id: string, data: ApplicationUpdate) => {
      await api.updateApplication(id, data)
      await refetch()
    },
    [refetch],
  )

  const remove = useCallback(
    async (id: string) => {
      await api.deleteApplication(id)
      await refetch()
    },
    [refetch],
  )

  return { applications, loading, error, refetch, create, update, remove }
}
