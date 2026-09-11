import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

import * as authApi from '../api/auth'
import type { User } from '../types/user'

interface AuthContextValue {
  user: User | null
  loading: boolean
  error: string | null
  refetch: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setUser(await authApi.getCurrentUser())
    } catch {
      // A 401 here just means "not logged in" — expected on first load,
      // not a real error to surface. Any genuine failure just leaves the
      // user on the login screen, which is the safe default anyway.
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refetch()
  }, [refetch])

  const logout = useCallback(async () => {
    setError(null)
    try {
      await authApi.logout()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to log out')
    } finally {
      setUser(null)
    }
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, error, refetch, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
