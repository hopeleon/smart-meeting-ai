import { create } from 'zustand'

interface AudioState {
  isRecording: boolean
  isPaused: boolean
  volume: number
  duration: number

  setRecording: (recording: boolean) => void
  setPaused: (paused: boolean) => void
  setVolume: (volume: number) => void
  setDuration: (duration: number) => void
  reset: () => void
}

export const useAudioStore = create<AudioState>((set) => ({
  isRecording: false,
  isPaused: false,
  volume: 0,
  duration: 0,

  setRecording: (recording) => set({ isRecording: recording }),
  setPaused: (paused) => set({ isPaused: paused }),
  setVolume: (volume) => set({ volume }),
  setDuration: (duration) => set({ duration }),
  reset: () => set({ isRecording: false, isPaused: false, volume: 0, duration: 0 }),
}))
