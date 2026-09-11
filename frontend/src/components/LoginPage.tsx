import { GOOGLE_LOGIN_URL } from '../api/auth'

export function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-8">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-bold text-gray-900">Job Application Tracker</h1>
        <p className="text-sm text-gray-500">Sign in to track your applications.</p>
        <a
          href={GOOGLE_LOGIN_URL}
          className="inline-flex w-full items-center justify-center gap-2 rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Sign in with Google
        </a>
      </div>
    </div>
  )
}
