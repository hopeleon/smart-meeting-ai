import { apiClient, isMockMode } from './client'
import type { TranscriptSegment, PaginatedResponse } from '../types/meeting'

export async function getTranscripts(
  meetingId: string,
  offset = 0,
  limit = 100,
): Promise<PaginatedResponse<TranscriptSegment>> {
  if (isMockMode()) {
    return { items: [], total: 0 }
  }
  try {
    const resp = await apiClient.get(`/meetings/${meetingId}/transcripts`, {
      params: { offset, limit },
    })
    return resp.data
  } catch {
    return { items: [], total: 0 }
  }
}
