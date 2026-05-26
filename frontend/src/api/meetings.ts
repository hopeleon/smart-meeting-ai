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
