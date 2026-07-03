import { apiClient } from './client'
import type { TranscriptSegment, PaginatedResponse } from '../types/meeting'

export async function getTranscripts(
  meetingId: string,
  offset = 0,
  limit = 100,
): Promise<PaginatedResponse<TranscriptSegment>> {
  const resp = await apiClient.get(`/meetings/${meetingId}/transcripts`, {
    params: { offset, limit },
  })
  return resp.data
}

export async function downloadTranscriptDocument(meetingId: string): Promise<{ blob: Blob; filename: string }> {
  const resp = await apiClient.get(`/meetings/${meetingId}/downloads/transcript`, {
    responseType: 'blob',
  })
  const disposition = resp.headers['content-disposition'] as string | undefined
  const match = disposition?.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/)
  const rawName = match?.[1] || match?.[2] || `meeting-${meetingId}-transcript.md`
  const filename = decodeURIComponent(rawName)
  return { blob: resp.data, filename }
}
