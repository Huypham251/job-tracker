export const APPLICATION_STATUSES = [
  'applied',
  'oa',
  'interview',
  'rejected',
  'offer',
  'withdrawn',
  'other',
] as const

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number]

export interface Application {
  id: string
  company: string
  position: string
  status: ApplicationStatus
  applied_at: string | null
  source: 'manual' | 'gmail'
  created_at: string
  updated_at: string
}

export interface ApplicationCreate {
  company: string
  position: string
  status?: ApplicationStatus
  applied_at?: string | null
}

export type ApplicationUpdate = Partial<ApplicationCreate>
