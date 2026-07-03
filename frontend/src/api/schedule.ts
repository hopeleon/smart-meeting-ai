import { apiClient } from './client'
import type {
  ScheduleEvent,
  ScheduleParseResult,
  ScheduleEventListResponse,
  ScheduleEventCreate,
  ScheduleEventUpdate,
} from '../types/schedule'

const LAOJI_API_PREFIX = '/laoji'

export async function listScheduleEvents(
  year?: number,
  month?: number,
): Promise<ScheduleEventListResponse> {
  const params = new URLSearchParams()
  if (year != null) params.set('year', String(year))
  if (month != null) params.set('month', String(month))
  const qs = params.toString()
  const { data } = await apiClient.get<ScheduleEventListResponse>(
    `${LAOJI_API_PREFIX}/events${qs ? `?${qs}` : ''}`,
  )
  return data
}

export async function getScheduleEvent(id: number): Promise<ScheduleEvent> {
  const { data } = await apiClient.get<ScheduleEvent>(`${LAOJI_API_PREFIX}/events/${id}`)
  return data
}

export async function createScheduleEvent(
  payload: ScheduleEventCreate,
): Promise<ScheduleEvent> {
  const { data } = await apiClient.post<ScheduleEvent>(`${LAOJI_API_PREFIX}/events`, payload)
  return data
}

export async function updateScheduleEvent(
  id: number,
  payload: ScheduleEventUpdate,
): Promise<ScheduleEvent> {
  const { data } = await apiClient.put<ScheduleEvent>(
    `${LAOJI_API_PREFIX}/events/${id}`,
    payload,
  )
  return data
}

export async function deleteScheduleEvent(id: number): Promise<void> {
  await apiClient.delete(`${LAOJI_API_PREFIX}/events/${id}`)
}

export async function parseScheduleText(text: string): Promise<ScheduleParseResult> {
  const { data } = await apiClient.post<ScheduleParseResult>(`${LAOJI_API_PREFIX}/parse`, { text })
  return data
}

export async function clarifyScheduleDraft(
  current: ScheduleParseResult,
  answer: string,
): Promise<ScheduleParseResult> {
  const { data } = await apiClient.post<ScheduleParseResult>(`${LAOJI_API_PREFIX}/clarify`, {
    current,
    answer,
  })
  return data
}

export async function parseScheduleAudio(
  audioBase64: string,
  filename: string,
): Promise<ScheduleParseResult> {
  const { data } = await apiClient.post<ScheduleParseResult>(`${LAOJI_API_PREFIX}/parse-audio`, {
    audio_base64: audioBase64,
    filename,
  })
  return data
}

export async function transcribeScheduleAudio(
  audioBase64: string,
  filename: string,
): Promise<{ text: string; duration_sec: number; provider: string }> {
  const { data } = await apiClient.post<{ text: string; duration_sec: number; provider: string }>(
    `${LAOJI_API_PREFIX}/asr/transcribe`,
    {
      audio_base64: audioBase64,
      filename,
    },
    { timeout: 60000 },
  )
  return data
}
