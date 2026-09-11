import { ApplicationsPage } from './components/ApplicationsPage'
import { LoginPage } from './components/LoginPage'
import { useAuth } from './context/AuthContext'

export default function App() {
  const { user, loading, logout } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <p className="text-sm text-gray-500">Loading…</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {user ? <ApplicationsPage user={user} onLogout={logout} /> : <LoginPage />}
    </div>
  )
}
