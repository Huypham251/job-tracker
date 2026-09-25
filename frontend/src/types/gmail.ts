export interface GmailStatus {
  connected: boolean
  email: string | null
  connected_at: string | null
  // Optional so an older backend without the field still type-checks at runtime.
  needs_reconnect?: boolean
}

export interface GmailMessageSummary {
  id: string
  subject: string
  from_: string
  date: string
  snippet: string
}
