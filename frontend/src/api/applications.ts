import type {
  Application,
  ApplicationCreate,
  ApplicationUpdate,
} from '../types/application'

const BASE = '/api/v1/applications'

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // response had no JSON body; keep the default message
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function listApplications(): Promise<Application[]> {
  return fetch(BASE).then((r) => parse<Application[]>(r))
}

export function createApplication(
  data: ApplicationCreate,
): Promise<Application> {
  return fetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parse<Application>(r))
}

export function updateApplication(
  id: string,
  data: ApplicationUpdate,
): Promise<Application> {
  return fetch(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parse<Application>(r))
}

export function deleteApplication(id: string): Promise<void> {
  return fetch(`${BASE}/${id}`, { method: 'DELETE' }).then((r) =>
    parse<void>(r),
  )
}
