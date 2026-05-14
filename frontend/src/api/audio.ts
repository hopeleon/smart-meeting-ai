import { apiClient, isMockMode } from './client'

export async function uploadAudio(meetingId: string, file: File): Promise<{ audio_id: string }> {
  if (isMockMode()) {
    return { audio_id: `mock-audio-${Date.now()}` }
  }
  try {
    const formData = new FormData()
    formData.append('file', file)
    const resp = await apiClient.post(`/meetings/${meetingId}/audio`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return resp.data
  } catch {
    return { audio_id: `mock-audio-${Date.now()}` }
  }
}

export async function uploadAudioChunk(
  meetingId: string,
  chunk: Blob,
  chunkIndex: number,
  isLast: boolean,
): Promise<{ chunk_index: number }> {
  if (isMockMode()) {
    return { chunk_index: chunkIndex }
  }
  try {
    const formData = new FormData()
    formData.append('file', chunk, `chunk_${chunkIndex}.pcm`)
    const resp = await apiClient.post(`/meetings/${meetingId}/audio/chunk`, formData, {
      params: { chunk_index: chunkIndex, is_last: isLast },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return resp.data
  } catch {
    return { chunk_index: chunkIndex }
  }
}
