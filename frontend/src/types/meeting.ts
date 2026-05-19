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
  meeting_id?: string
  speaker_id: string
  speaker_label: string
  text: string
  start_time?: number
  end_time?: number
  start_ms?: number
  end_ms?: number
  is_final?: boolean
  confidence: number
  segment_reason?: string
  speaker_confidence?: number
  speaker_name?: string
  speaker_candidates?: Array<{ speaker_id: string; name: string; confidence: number }>
  registered_speaker_sims?: Record<string, number>
  recognized_role?: string
  interviewer_sim?: number
  candidate_sim?: number
  uncertain_speaker?: boolean
  speaker_uncertain_reason?: string
  was_corrected?: boolean
  corrected_text?: string
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

// 支持两种消息格式：
// 1. data wrapper: {type, data: {...}}
// 2. flat: {type, ...fields}（对齐 InsightEye）

export type WebSocketMessage =
  // session.ready
  | { type: 'session.ready'; data?: Record<string, unknown>; session_id?: string }
  // transcript（兼容 data wrapper）
  | { type: 'transcript'; data: TranscriptSegment }
  // transcript.completed / transcript.delta（支持两种格式）
  | { type: 'transcript.completed'; data: TranscriptSegment }
  | { type: 'transcript.completed'; speaker_id: string; text: string; start_ms?: number; end_ms?: number; [key: string]: unknown }
  | { type: 'transcript.delta'; data: TranscriptSegment }
  | { type: 'transcript.delta'; speaker_id: string; text: string; start_ms?: number; end_ms?: number; [key: string]: unknown }
  // period_summary
  | { type: 'period_summary'; data: PeriodSummary }
  | { type: 'period_summary_update'; data: PeriodSummary }
  // meeting_status
  | { type: 'meeting_status'; data?: { status: string; total_samples?: number }; status?: string }
  // speaker
  | { type: 'speaker_update'; data: Record<string, unknown> }
  | { type: 'speaker.identified'; data?: { speaker_id: string; confidence: number }; speaker_id?: string; confidence?: number; source?: string }
  // pong
  | { type: 'pong' }
  // heartbeat
  | { type: 'heartbeat'; data?: { timestamp: number }; timestamp?: number }
  // error
  | { type: 'error'; message: string }
