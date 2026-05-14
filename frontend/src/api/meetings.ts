import { apiClient, isMockMode } from './client'
import type { Meeting, MeetingCreate, PaginatedResponse } from '../types/meeting'

const mockMeetings: Meeting[] = [
  {
    id: 'mock-1',
    title: '产品需求评审会',
    description: '讨论 Q2 产品规划',
    status: 'ended',
    participants: ['张三', '李四', '王五'],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: 'mock-2',
    title: '技术方案讨论',
    description: '讨论系统架构设计',
    status: 'recording',
    participants: ['李四', '赵六'],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
]

export async function listMeetings(page = 1, size = 20): Promise<PaginatedResponse<Meeting>> {
  if (isMockMode()) {
    return { items: mockMeetings, total: mockMeetings.length, page, size }
  }
  try {
    const resp = await apiClient.get('/meetings', { params: { page, size } })
    return resp.data
  } catch {
    return { items: mockMeetings, total: mockMeetings.length, page, size }
  }
}

export async function getMeeting(meetingId: string): Promise<Meeting> {
  if (isMockMode()) {
    return mockMeetings.find((m) => m.id === meetingId) || mockMeetings[0]
  }
  try {
    const resp = await apiClient.get(`/meetings/${meetingId}`)
    return resp.data
  } catch {
    return mockMeetings.find((m) => m.id === meetingId) || mockMeetings[0]
  }
}

export async function createMeeting(data: MeetingCreate): Promise<Meeting> {
  if (isMockMode()) {
    const meeting: Meeting = {
      id: `mock-${Date.now()}`,
      title: data.title,
      description: data.description || null,
      status: 'created',
      participants: data.participants || [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    mockMeetings.unshift(meeting)
    return meeting
  }
  try {
    const resp = await apiClient.post('/meetings', data)
    return resp.data
  } catch {
    const meeting: Meeting = {
      id: `mock-${Date.now()}`,
      title: data.title,
      description: data.description || null,
      status: 'created',
      participants: data.participants || [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    mockMeetings.unshift(meeting)
    return meeting
  }
}

export async function updateMeetingStatus(meetingId: string, status: string): Promise<Meeting> {
  if (isMockMode()) {
    const m = mockMeetings.find((m) => m.id === meetingId)
    if (m) m.status = status as Meeting['status']
    return m || mockMeetings[0]
  }
  try {
    const resp = await apiClient.patch(`/meetings/${meetingId}`, { status })
    return resp.data
  } catch {
    const m = mockMeetings.find((m) => m.id === meetingId)
    if (m) m.status = status as Meeting['status']
    return m || mockMeetings[0]
  }
}
