import { useRef, useEffect } from 'react'
import { useAudioStore } from '../../stores/audioStore'

export default function WaveformVisualizer() {
  const { isRecording, volume } = useAudioStore()

  const bars = 48

  // 用 ref 累积历史电平（滚动窗口），避免每次渲染重置
  const historyRef = useRef<number[]>(Array(bars).fill(0))

  useEffect(() => {
    if (!isRecording) {
      historyRef.current = Array(bars).fill(0)
      return
    }
    // 每次 volume 更新：左移一位，末尾填入当前电平
    const hist = historyRef.current
    hist.shift()
    hist.push(volume)
  }, [volume, isRecording, bars])

  return (
    <div className="flex-1 flex items-center gap-px h-10 px-4">
      {historyRef.current.map((level, i) => {
        // 以 bar 索引为中心衰减，两端渐低，形成中间高两边低的波形
        const center = (bars - 1) / 2
        const dist = Math.abs(i - center) / center
        const decay = 1 - dist * 0.6
        const h = isRecording
          ? Math.max(4, (Math.random() * 30 + 10 + level * 60) * decay)
          : 4
        return (
          <div
            key={i}
            className="flex-1 bg-primary/70 rounded-full transition-all duration-100"
            style={{ height: `${h}%` }}
          />
        )
      })}
    </div>
  )
}
