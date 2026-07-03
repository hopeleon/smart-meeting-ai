// Transcript segment returned by real-time WebSocket (compatible with TranscriptLine model)
export interface TranscriptSegment {
  id: string
  segment_id?: string
  meeting_id?: string
  speaker_id: string
  speaker_label?: string
  text: string
  source?: 'funasr' | 'qwen' | 'whisper' | 'hybrid' | string
  start_time?: number
  end_time?: number
  start_ms?: number
  end_ms?: number
  is_final?: boolean
  confidence?: number
  segment_reason?: string
  speaker_confidence?: number
  speaker_name?: string
  identified?: boolean
  best_guess_name?: string | null
  best_guess_score?: number | null
  speaker_candidates?: Array<{ speaker_id: string; name?: string | null; score: number }>
  registered_speaker_sims?: Record<string, number>
  recognized_role?: 'interviewer' | 'candidate' | string | null
  interviewer_sim?: number
  candidate_sim?: number
  uncertain_speaker?: boolean
  speaker_uncertain_reason?: string | null
  was_corrected?: boolean
  corrected_text?: string | null
  original_text?: string | null
  qwen_text?: string | null
  revision_status?: 'pending' | 'enhanced' | 'kept' | 'timeout_or_error' | string
  revision_accepted?: boolean
  stable?: boolean
  created_at?: string
}

// Flat WebSocket messages aligned with InsightEye old_version
export type TranscriptStatus = 'in-progress' | 'completed' | 'error'

export interface WebSocketMessage {
  type: string
  source?: string
  session_id?: string
  provider?: string
  message?: string
  status?: string
  text?: string
  segment_id?: string
  is_final?: boolean
  start_ms?: number
  end_ms?: number
  speaker_id?: string
  speaker_confidence?: number
  segment_reason?: string
  interviewer_sim?: number
  candidate_sim?: number
  recognized_role?: string
  speaker_name?: string
  speaker_candidates?: TranscriptSegment['speaker_candidates']
  registered_speaker_sims?: TranscriptSegment['registered_speaker_sims']
  uncertain_speaker?: boolean
  uncertain_reason?: string | null
  speaker_multi_window_stats?: Record<string, unknown> | null
  was_corrected?: boolean
  corrected_text?: string | null
  original_text?: string | null
  qwen_text?: string | null
  revision_status?: string
  revision_accepted?: boolean
  stable?: boolean
  correction_errors?: unknown[] | null
  timestamp?: number
  data?: unknown
  // end_meeting specific
  total_chunks?: number
  total_duration?: number
  streaming_phrases_count?: number
  final_transcript_count?: number
  error?: string
  supports_registration?: boolean
  supports_auto_registration?: boolean
  supports_multi_speaker?: boolean
  streaming?: boolean
  // audio_chunk
  audio?: string
  // period_summary
  id?: string
  bullet_points?: string[]
  generated_at?: string
  // speaker.identified
  confidence?: number
  // final_summary
  final_summary?: {
    text: string
    keywords?: string[]
    meeting_id?: string
    duration?: number
  }
  // status
  model_status?: {
    session_id: string
    message: string
    status: string
    asr_model: string
    vad_model: string
    speaker_model: string
  }
}

// TranscriptLine (matches backend model)
export interface TranscriptLine {
  id: string
  meeting_id: string
  speaker_id: string
  speaker_label?: string
  text: string
  start_time?: number
  end_time?: number
  start_ms?: number
  end_ms?: number
  is_final?: boolean
  confidence?: number
  segment_reason?: string
  speaker_confidence?: number
  speaker_name?: string
  speaker_candidates?: TranscriptSegment['speaker_candidates']
  registered_speaker_sims?: TranscriptSegment['registered_speaker_sims']
  recognized_role?: string
  interviewer_sim?: number
  candidate_sim?: number
  uncertain_speaker?: boolean
  speaker_uncertain_reason?: string
  was_corrected?: boolean
  corrected_text?: string | null
  created_at: string
}

// PeriodSummary (matches backend model)
export interface PeriodSummary {
  id: string
  meeting_id: string
  period_start: number
  period_end: number
  bullet_points: string[]
  generated_at: string
}

export interface FinalSummary {
  id: string
  meeting_id: string
  text: string
  keywords?: string[]
  meeting_duration?: number
  created_at: string
  // 扩展字段（用于 Summary 页面）
  markdown?: string
  full_text?: string
  overview?: string
  key_decisions?: string[]
  action_items?: Array<{ id: string; content: string; assignee?: string; due_date?: string; status?: string }>
}

export interface ActionItem {
  id: string
  content: string
  assignee?: string
  due_date?: string
  status?: 'pending' | 'in-progress' | 'done'
  meeting_id?: string
  created_at?: string
}

// Speaker database entry
export interface Speaker {
  id: string
  speaker_id: string
  name?: string
  role?: 'interviewer' | 'candidate'
  embedding_bytes?: string
  sample_count: number
  is_active: boolean
  notes?: string
  created_at: string
  updated_at: string
}

export type MeetingMode = 'realtime' | 'hybrid' | 'offline' | 'whisper' | 'qwen'

export interface Meeting {
  id: string
  title: string
  description?: string
  status: string
  participants: string[]
  mode: MeetingMode
  created_at: string
  updated_at: string
}

export interface MeetingCreate {
  title: string
  description?: string
  participants?: string[]
  mode?: MeetingMode
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  size: number
}
