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

export async function downloadSummaryDocument(meetingId: string): Promise<{ blob: Blob; filename: string }> {
  const resp = await apiClient.get(`/meetings/${meetingId}/downloads/summary`, {
    responseType: 'blob',
  })
  const disposition = resp.headers['content-disposition'] as string | undefined
  const match = disposition?.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/)
  const rawName = match?.[1] || match?.[2] || `meeting-${meetingId}-summary.md`
  const filename = decodeURIComponent(rawName)
  return { blob: resp.data, filename }
}
