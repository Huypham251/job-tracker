// Carries the HTTP status and the backend's machine-readable `code` (when it
// sends one) so callers can react to specific failures — e.g. showing a
// "Reconnect Gmail" link — instead of only displaying the message.
export class ApiError extends Error {
  status: number
  code: string | null

  constructor(message: string, status: number, code: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    let code: string | null = null
    try {
      const body = (await response.json()) as { detail?: unknown; code?: unknown }
      if (typeof body.detail === 'string') message = body.detail
      if (typeof body.code === 'string') code = body.code
    } catch {
      // response had no JSON body; keep the default message
    }
    throw new ApiError(message, response.status, code)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
