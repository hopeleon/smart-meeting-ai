import { apiClient } from './client'

export async function uploadAudio(meetingId: string, file: File): Promise<{ audio_id: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const resp = await apiClient.post(`/meetings/${meetingId}/audio`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return resp.data
}

export async function uploadAudioChunk(
  meetingId: string,
  chunk: Blob,
  chunkIndex: number,
  isLast: boolean,
): Promise<{ chunk_index: number }> {
  const formData = new FormData()
  formData.append('file', chunk, `chunk_${chunkIndex}.pcm`)
  const resp = await apiClient.post(`/meetings/${meetingId}/audio/chunk`, formData, {
    params: { chunk_index: chunkIndex, is_last: isLast },
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return resp.data
}
