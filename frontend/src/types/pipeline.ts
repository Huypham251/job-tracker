export interface ProcessResult {
  processed: number
  auto_applied: number
  queued_for_review: number
  ignored: number
}

export interface ReviewItem {
  id: string
  subject: string
  sender: string
  snippet: string
  confidence: number
  proposed_action: 'create' | 'update' | null
  matched_application_id: string | null
  extracted_company: string | null
  extracted_position: string | null
  extracted_status: string | null
  extracted_status_date: string | null
  created_at: string
}

export interface ReviewDecision {
  company?: string
  position?: string
  status?: string
  status_date?: string
}
