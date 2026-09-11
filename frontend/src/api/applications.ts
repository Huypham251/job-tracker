import { parseResponse } from './http'
import type {
  Application,
  ApplicationCreate,
  ApplicationUpdate,
} from '../types/application'

const BASE = '/api/v1/applications'
const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function listApplications(): Promise<Application[]> {
  return fetch(BASE).then((r) => parseResponse<Application[]>(r))
}

export function createApplication(
  data: ApplicationCreate,
): Promise<Application> {
  return fetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parseResponse<Application>(r))
}

export function updateApplication(
  id: string,
  data: ApplicationUpdate,
): Promise<Application> {
  return fetch(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parseResponse<Application>(r))
}

export function deleteApplication(id: string): Promise<void> {
  return fetch(`${BASE}/${id}`, { method: 'DELETE' }).then((r) =>
    parseResponse<void>(r),
  )
}
