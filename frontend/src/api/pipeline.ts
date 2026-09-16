import { parseResponse } from './http'
import type { Application } from '../types/application'
import type { ProcessResult, ReviewDecision, ReviewItem } from '../types/pipeline'

const BASE = '/api/v1/pipeline'

export function processInbox(): Promise<ProcessResult> {
  return fetch(`${BASE}/process`, { method: 'POST' }).then((r) => parseResponse<ProcessResult>(r))
}

export function getReviewQueue(): Promise<ReviewItem[]> {
  return fetch(`${BASE}/review`).then((r) => parseResponse<ReviewItem[]>(r))
}

export function approveReviewItem(id: string, edits: ReviewDecision = {}): Promise<Application> {
  return fetch(`${BASE}/review/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edits),
  }).then((r) => parseResponse<Application>(r))
}

export function rejectReviewItem(id: string): Promise<void> {
  return fetch(`${BASE}/review/${id}/reject`, { method: 'POST' }).then((r) => parseResponse<void>(r))
}
