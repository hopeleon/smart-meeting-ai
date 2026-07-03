export interface ScheduleEvent {
  id: number
  title: string
  event_type: string
  start_date: string
  start_time: string | null
  end_time: string | null
  is_all_day: boolean
  description: string | null
  raw_text: string | null
  created_at: string
  updated_at: string
}

export interface ScheduleParseResult {
  title: string
  event_type: string
  start_date: string
  start_time: string | null
  end_time: string | null
  is_all_day: boolean
  description: string | null
  raw_text: string
  parse_source: 'rules' | 'local_llm' | string
  confidence: number
  needs_clarification: boolean
  clarification_question: string | null
}

export interface ScheduleEventListResponse {
  events: ScheduleEvent[]
  total: number
}

export interface ScheduleEventCreate {
  title: string
  event_type?: string
  start_date: string
  start_time?: string | null
  end_time?: string | null
  is_all_day?: boolean
  description?: string | null
  raw_text?: string | null
}

export interface ScheduleEventUpdate {
  title?: string
  event_type?: string
  start_date?: string
  start_time?: string | null
  end_time?: string | null
  is_all_day?: boolean
  description?: string | null
}
