import { apiClient, isMockMode } from './client'
import type { PeriodSummary, FinalSummary } from '../types/meeting'

export async function getPeriodSummaries(meetingId: string): Promise<PeriodSummary[]> {
  if (isMockMode()) {
    return []
  }
  try {
    const resp = await apiClient.get(`/meetings/${meetingId}/summaries/period`)
    return resp.data.items
  } catch {
    return []
  }
}

export async function getFinalSummary(meetingId: string): Promise<FinalSummary | null> {
  if (isMockMode()) {
    return null
  }
  try {
    const resp = await apiClient.get(`/meetings/${meetingId}/summaries/final`)
    return resp.data
  } catch {
    return null
  }
}
