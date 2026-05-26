import { create } from 'zustand'
import type { Meeting, TranscriptSegment, PeriodSummary, FinalSummary } from '../types/meeting'

interface MeetingState {
  meetings: Meeting[]
  currentMeeting: Meeting | null
  transcripts: TranscriptSegment[]
  periodSummaries: PeriodSummary[]
  finalSummary: FinalSummary | null

  setMeetings: (meetings: Meeting[]) => void
  setCurrentMeeting: (meeting: Meeting | null) => void
  addTranscript: (segment: TranscriptSegment) => void
  setTranscripts: (segments: TranscriptSegment[]) => void
  addPeriodSummary: (summary: PeriodSummary) => void
  setPeriodSummaries: (summaries: PeriodSummary[]) => void
  setFinalSummary: (summary: FinalSummary | null) => void
  clearCurrent: () => void
}

export const useMeetingStore = create<MeetingState>((set) => ({
  meetings: [],
  currentMeeting: null,
  transcripts: [],
  periodSummaries: [],
  finalSummary: null,

  setMeetings: (meetings) => set({ meetings }),
  setCurrentMeeting: (meeting) => set({ currentMeeting: meeting }),
  addTranscript: (segment: TranscriptSegment) =>
    set((state) => {
      // 基于 (speaker_id, text, start_ms) 去重
      const key = `${segment.speaker_id}|${segment.text}|${segment.start_ms}`
      const exists = state.transcripts.some(
        (s) => `${s.speaker_id}|${s.text}|${s.start_ms}` === key,
      )
      if (exists) return state
      return { transcripts: [...state.transcripts, segment] }
    }),
  setTranscripts: (segments) => set({ transcripts: segments }),
  addPeriodSummary: (summary) =>
    set((state) => {
      const key = summary.id || summary.period_start
      if (state.periodSummaries.some((s) => (s.id || s.period_start) === key)) {
        return state
      }
      return { periodSummaries: [...state.periodSummaries, summary] }
    }),
  setPeriodSummaries: (summaries) => set({ periodSummaries: summaries }),
  setFinalSummary: (summary) => set({ finalSummary: summary }),
  clearCurrent: () =>
    set({ currentMeeting: null, transcripts: [], periodSummaries: [], finalSummary: null }),
}))
