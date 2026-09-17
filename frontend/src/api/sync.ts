import { parseResponse } from './http'
import type { SyncJob } from '../types/sync'

const BASE = '/api/v1/gmail'

async function parseSyncStartResponse(response: Response): Promise<SyncJob> {
  // 202 (newly started) and 409 (already running) both carry a SyncJob body —
  // only a genuine error should throw.
  if (response.status === 202 || response.status === 409) {
    return (await response.json()) as SyncJob
  }
  return parseResponse<SyncJob>(response)
}

export function startSync(): Promise<SyncJob> {
  return fetch(`${BASE}/sync`, { method: 'POST' }).then(parseSyncStartResponse)
}

export function getSyncJob(id: string): Promise<SyncJob> {
  return fetch(`${BASE}/sync/${id}`).then((r) => parseResponse<SyncJob>(r))
}

export function getLatestSyncJob(): Promise<SyncJob | null> {
  return fetch(`${BASE}/sync/latest`).then((r) => parseResponse<SyncJob | null>(r))
}
