export interface GmailStatus {
  connected: boolean
  email: string | null
  connected_at: string | null
}

export interface GmailMessageSummary {
  id: string
  subject: string
  from_: string
  date: string
  snippet: string
}
