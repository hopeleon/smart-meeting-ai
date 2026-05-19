import { apiClient } from './client'
import type { PeriodSummary, FinalSummary } from '../types/meeting'

export async function getPeriodSummaries(meetingId: string): Promise<PeriodSummary[]> {
  const resp = await apiClient.get(`/meetings/${meetingId}/summaries/period`)
  return resp.data.items
}

export async function getFinalSummary(meetingId: string): Promise<FinalSummary | null> {
  const resp = await apiClient.get(`/meetings/${meetingId}/summaries/final`)
  return resp.data
}
