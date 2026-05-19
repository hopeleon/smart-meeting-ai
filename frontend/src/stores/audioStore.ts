import { create } from 'zustand'

export type AudioSource = 'mic' | 'system'

interface AudioState {
  isRecording: boolean
  isPaused: boolean
  volume: number
  duration: number
  audioSource: AudioSource

  setRecording: (recording: boolean) => void
  setPaused: (paused: boolean) => void
  setVolume: (volume: number) => void
  setDuration: (duration: number) => void
  setAudioSource: (source: AudioSource) => void
  reset: () => void
}

export const useAudioStore = create<AudioState>((set) => ({
  isRecording: false,
  isPaused: false,
  volume: 0,
  duration: 0,
  audioSource: 'mic',

  setRecording: (recording) => set({ isRecording: recording }),
  setPaused: (paused) => set({ isPaused: paused }),
  setVolume: (volume) => set({ volume }),
  setDuration: (duration) => set({ duration }),
  setAudioSource: (audioSource) => set({ audioSource }),
  reset: () => set({ isRecording: false, isPaused: false, volume: 0, duration: 0, audioSource: 'mic' }),
}))
