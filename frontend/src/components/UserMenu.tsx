import type { User } from '../types/user'

interface Props {
  user: User
  onLogout: () => void
}

export function UserMenu({ user, onLogout }: Props) {
  return (
    <div className="flex items-center gap-3">
      {user.picture_url ? (
        <img src={user.picture_url} alt="" className="h-8 w-8 rounded-full" />
      ) : (
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-200 text-xs font-medium text-gray-600">
          {user.name.slice(0, 1).toUpperCase()}
        </div>
      )}
      <span className="text-sm text-gray-700">{user.name}</span>
      <button
        onClick={onLogout}
        className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        Log out
      </button>
    </div>
  )
}
