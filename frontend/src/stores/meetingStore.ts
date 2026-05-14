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
  addTranscript: (segment) =>
    set((state) => ({ transcripts: [...state.transcripts, segment] })),
  setTranscripts: (segments) => set({ transcripts: segments }),
  addPeriodSummary: (summary) =>
    set((state) => ({ periodSummaries: [...state.periodSummaries, summary] })),
  setPeriodSummaries: (summaries) => set({ periodSummaries: summaries }),
  setFinalSummary: (summary) => set({ finalSummary: summary }),
  clearCurrent: () =>
    set({ currentMeeting: null, transcripts: [], periodSummaries: [], finalSummary: null }),
}))
