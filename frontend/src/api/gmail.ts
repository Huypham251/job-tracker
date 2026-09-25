import { parseResponse } from './http'
import type { GmailMessageSummary, GmailStatus } from '../types/gmail'

const BASE = '/api/v1/gmail'

export function getGmailStatus(): Promise<GmailStatus> {
  return fetch(`${BASE}/status`).then((r) => parseResponse<GmailStatus>(r))
}

export function disconnectGmail(): Promise<void> {
  return fetch(`${BASE}/disconnect`, { method: 'POST' }).then((r) => parseResponse<void>(r))
}

export function fetchGmailMessages(limit = 20): Promise<GmailMessageSummary[]> {
  return fetch(`${BASE}/messages?limit=${limit}`).then((r) =>
    parseResponse<GmailMessageSummary[]>(r),
  )
}

export const GMAIL_CONNECT_URL = `${BASE}/connect`

// The backend's code for an expired/revoked Gmail grant. Reconnecting goes
// through the normal connect flow, which keeps the existing sync history.
export const GMAIL_REAUTH_CODE = 'gmail_reauth_required'
