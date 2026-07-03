import { apiClient } from './client'
import type { Meeting, MeetingCreate, PaginatedResponse } from '../types/meeting'

export async function listMeetings(page = 1, size = 20): Promise<PaginatedResponse<Meeting>> {
  const resp = await apiClient.get('/meetings', { params: { page, size } })
  return resp.data
}

export async function getMeeting(meetingId: string): Promise<Meeting> {
  const resp = await apiClient.get(`/meetings/${meetingId}`)
  return resp.data
}

export async function createMeeting(data: MeetingCreate): Promise<Meeting> {
  const resp = await apiClient.post('/meetings', data)
  return resp.data
}

export async function updateMeetingStatus(meetingId: string, status: string): Promise<Meeting> {
  const resp = await apiClient.patch(`/meetings/${meetingId}`, { status })
  return resp.data
}

export async function deleteMeeting(meetingId: string): Promise<void> {
  await apiClient.delete(`/meetings/${meetingId}`)
}

// ==================== 离线处理相关 ====================

export interface UploadResponse {
  status: string
  message: string
  file_path?: string
  file_size?: number
  meeting_id: string
}

export interface ProcessingStatus {
  meeting_id: string
  status: string
  title?: string
  updated_at?: string
}

/**
 * 上传音频文件 — VibeVoice-ASR + 3D-Speaker
 */
export async function uploadAudioFile(
  meetingId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const estimatedSeconds = Math.max(600, Math.ceil(file.size / (512 * 1024)))

  const resp = await apiClient.post<UploadResponse>(
    `/meetings/${meetingId}/upload`,
    formData,
    {
      timeout: estimatedSeconds * 1000,
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          const progress = (progressEvent.loaded / progressEvent.total) * 100
          onProgress(progress)
        }
      },
    }
  )

  return resp.data
}

/**
 * 获取会议处理状态
 */
export async function getMeetingStatus(meetingId: string): Promise<ProcessingStatus> {
  const resp = await apiClient.get<ProcessingStatus>(`/meetings/${meetingId}/status`)
  return resp.data
}

export async function triggerSummary(meetingId: string): Promise<{ message: string }> {
  const resp = await apiClient.post(`/meetings/${meetingId}/summaries/generate`, null, {
    params: { summary_type: 'final' },
  })
  return resp.data
}

export interface ModelStatus {
  status: string
  env: string
  models: {
    funasr: boolean
    vad: boolean
    campplus: boolean
    campplus_en: boolean
  }
}

export async function checkBackendHealth(): Promise<ModelStatus | null> {
  try {
    const resp = await apiClient.get<ModelStatus>('/health')
    return resp.data
  } catch {
    return null
  }
}
