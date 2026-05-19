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
