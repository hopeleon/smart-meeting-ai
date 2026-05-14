export interface Meeting {
  id: string
  title: string
  description: string | null
  status: 'created' | 'recording' | 'paused' | 'ended'
  participants: string[]
  created_at: string
  updated_at: string
}

export interface MeetingCreate {
  title: string
  description?: string
  participants?: string[]
}

export interface MeetingUpdate {
  title?: string
  description?: string
  status?: string
  participants?: string[]
}

export interface TranscriptSegment {
  id: string
  meeting_id: string
  speaker_id: string
  speaker_label: string
  text: string
  start_time: number
  end_time: number
  confidence: number
  created_at: string
}

export interface PeriodSummary {
  id: string
  meeting_id: string
  period_start: number
  period_end: number
  bullet_points: string[]
  generated_at: string
}

export interface ActionItem {
  id: string
  content: string
  assignee: string | null
  due_date: string | null
  status: 'pending' | 'in-progress' | 'done'
}

export interface FinalSummary {
  id: string
  meeting_id: string
  overview: string
  key_decisions: string[]
  action_items: ActionItem[]
  generated_at: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page?: number
  size?: number
}

export type WebSocketMessage =
  | { type: 'transcript'; data: TranscriptSegment }
  | { type: 'period_summary'; data: PeriodSummary }
  | { type: 'meeting_status'; data: { status: string } }
