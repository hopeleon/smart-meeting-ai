import { useAudioStore } from '../../stores/audioStore'

export default function WaveformVisualizer() {
  const { isRecording, volume } = useAudioStore()

  // 模拟波形数据
  const bars = 32

  return (
    <div className="flex-1 flex items-center gap-1 h-10 px-4">
      {Array.from({ length: bars }).map((_, i) => {
        const height = isRecording
          ? Math.random() * 60 + 20
          : 10
        return (
          <div
            key={i}
            className="flex-1 bg-primary/60 rounded-full transition-all duration-150"
            style={{ height: `${height}%` }}
          />
        )
      })}
    </div>
  )
}
