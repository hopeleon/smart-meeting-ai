import { create } from 'zustand'

export type AudioSource = 'mic' | 'system'

interface AudioState {
  isRecording: boolean
  isPaused: boolean
  volume: number
  duration: number
  audioSource: AudioSource
  isModelLoading: boolean
  modelLoadingProgress: number
  /** 质量优先模式：积累更长音频后再处理，精度更高但延迟增加 */
  qualityMode: boolean

  setRecording: (recording: boolean) => void
  setPaused: (paused: boolean) => void
  setVolume: (volume: number) => void
  setDuration: (duration: number) => void
  setAudioSource: (source: AudioSource) => void
  setModelLoading: (loading: boolean) => void
  setModelLoadingProgress: (progress: number) => void
  setQualityMode: (enabled: boolean) => void
  reset: () => void
}

export const useAudioStore = create<AudioState>((set) => ({
  isRecording: false,
  isPaused: false,
  volume: 0,
  duration: 0,
  audioSource: 'mic',
  isModelLoading: false,
  modelLoadingProgress: 0,
  qualityMode: false,

  setRecording: (recording) => set({ isRecording: recording }),
  setPaused: (paused) => set({ isPaused: paused }),
  setVolume: (volume) => set({ volume }),
  setDuration: (duration) => set({ duration }),
  setAudioSource: (audioSource) => set({ audioSource }),
  setModelLoading: (loading) => set({ isModelLoading: loading }),
  setModelLoadingProgress: (progress) => set({ modelLoadingProgress: progress }),
  setQualityMode: (enabled) => set({ qualityMode: enabled }),
  reset: () => set({
    isRecording: false,
    isPaused: false,
    volume: 0,
    duration: 0,
    audioSource: 'mic',
    isModelLoading: false,
    modelLoadingProgress: 0,
    qualityMode: false,
  }),
}))
