import { apiClient } from './client'
import type { PeriodSummary, FinalSummary } from '../types/meeting'

export async function getPeriodSummaries(meetingId: string): Promise<PeriodSummary[]> {
  const resp = await apiClient.get(`/meetings/${meetingId}/summaries/period`)
  return resp.data.items
}

export async function getFinalSummary(meetingId: string): Promise<FinalSummary | null> {
  try {
    const resp = await apiClient.get(`/meetings/${meetingId}/summaries/final`)
    return resp.data
  } catch (err: unknown) {
    if (err && typeof err === 'object' && 'response' in err) {
      const axiosErr = err as { response?: { status?: number } }
      if (axiosErr.response?.status === 404) {
        return null
      }
    }
    throw err
  }
}
