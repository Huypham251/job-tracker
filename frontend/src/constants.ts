import type { ApplicationStatus } from './types/application'

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: 'Applied',
  oa: 'OA',
  interview: 'Interview',
  rejected: 'Rejected',
  offer: 'Offer',
  withdrawn: 'Withdrawn',
}

export const STATUS_STYLES: Record<ApplicationStatus, string> = {
  applied: 'bg-blue-100 text-blue-800',
  oa: 'bg-purple-100 text-purple-800',
  interview: 'bg-amber-100 text-amber-800',
  rejected: 'bg-red-100 text-red-800',
  offer: 'bg-green-100 text-green-800',
  withdrawn: 'bg-gray-100 text-gray-700',
}
