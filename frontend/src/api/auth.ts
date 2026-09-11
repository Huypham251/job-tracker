import { parseResponse } from './http'
import type { User } from '../types/user'

const BASE = '/api/v1/auth'

export function getCurrentUser(): Promise<User> {
  return fetch(`${BASE}/me`).then((r) => parseResponse<User>(r))
}

export function logout(): Promise<void> {
  return fetch(`${BASE}/logout`, { method: 'POST' }).then((r) =>
    parseResponse<void>(r),
  )
}

export const GOOGLE_LOGIN_URL = `${BASE}/google/login`
