export interface SyncJob {
  id: string
  job_type: 'initial' | 'incremental'
  status: 'queued' | 'running' | 'completed' | 'failed'
  attempts: number
  window_start: string
  messages_seen: number
  messages_processed: number
  auto_applied: number
  queued_for_review: number
  ignored: number
  failed_count: number
  error_message: string | null
  error_code?: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}
