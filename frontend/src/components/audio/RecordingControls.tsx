import { useRef, useCallback, useEffect, useState } from 'react'
import { useAudioStore } from '../../stores/audioStore'
import type { MeetingWebSocket } from '../../api/websocket'


// ─── AudioWorklet Processor（内联 Blob，避免单独文件依赖）──────────────
// 输入：麦克风/系统音频 48kHz Float32 PCM
// 输出：通过 MessagePort 发送 Int16 PCM @ 16kHz（整数抽取降采样）
const PROCESSOR_CODE = `
class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.factor = Math.round(sampleRate / 16000)
    this.buf = []
    this.port.onmessage = (e) => {
      if (e.data === 'flush') this.flush()
    }
  }

  flush() {
    if (this.buf.length > 0) {
      const out = new Int16Array(this.buf)
      this.port.postMessage({ pcm: out.buffer }, [out.buffer])
      this.buf = []
    }
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const ch = input[0]
    if (!ch || ch.length === 0) return true

    for (let i = 0; i < ch.length; i++) {
      if (i % this.factor === 0) {
        const s = Math.max(-1, Math.min(1, ch[i]))
        this.buf.push(s < 0 ? s * 32768 : s * 32767)
      }
    }

    // 每 ~1 秒（约 16000 样本）发一次
    if (this.buf.length >= 16000) {
      const out = new Int16Array(this.buf)
      this.port.postMessage({ pcm: out.buffer }, [out.buffer])
      this.buf = []
    }
    return true
  }
}
registerProcessor('pcm-processor', PcmProcessor)
`

function makePcmWorkletUrl(): string {
  const blob = new Blob([PROCESSOR_CODE], { type: 'application/javascript' })
  return URL.createObjectURL(blob)
}

interface Props {
  meetingId: string
  ws?: MeetingWebSocket | null
}

type Source = 'mic' | 'system'

export default function RecordingControls({ meetingId: _meetingId, ws }: Props) {
  const workletRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const animFrameRef = useRef<number | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const pcmBufRef = useRef<Int16Array[]>([])
  const sendTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const workletUrlRef = useRef<string>('')
  const { isRecording, setRecording, setAudioSource, qualityMode, setQualityMode } = useAudioStore()
  const [selectedSource, setSelectedSource] = useState<Source>('mic')
  const [permission, setPermission] = useState<'idle' | 'granted' | 'denied'>('idle')
  const [error, setError] = useState<string | null>(null)

  // ─── 可视化（复用原逻辑，绑定到同一个 stream） ─────────────────────
  const startVisualizer = useCallback((stream: MediaStream) => {
    const canvas = document.createElement('canvas')
    canvas.width = 320
    canvas.height = 48
    canvas.style.cssText = 'border-radius:6px;background:#1e1e2e;display:block;'
    canvasRef.current = canvas

    const ctx = canvas.getContext('2d')!
    const audioCtx = new AudioContext()
    audioCtxRef.current = audioCtx
    const src = audioCtx.createMediaStreamSource(stream)
    const analyser = audioCtx.createAnalyser()
    analyser.fftSize = 2048
    analyserRef.current = analyser
    src.connect(analyser)

    const bufLen = analyser.frequencyBinCount
    const dataArr = new Uint8Array(bufLen)

    const draw = () => {
      animFrameRef.current = requestAnimationFrame(draw)
      analyser.getByteTimeDomainData(dataArr)
      ctx.fillStyle = '#1e1e2e'
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      let sum = 0
      for (let i = 0; i < bufLen; i++) {
        const v = (dataArr[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / bufLen)
      const barH = Math.min(canvas.height, rms * canvas.height * 2)
      const y = (canvas.height - barH) / 2

      const grad = ctx.createLinearGradient(0, y, 0, y + barH)
      grad.addColorStop(0, '#4ade80')
      grad.addColorStop(0.5, '#22d3ee')
      grad.addColorStop(1, '#4ade80')
      ctx.fillStyle = grad
      ctx.fillRect(0, y, canvas.width, Math.max(2, barH))

      ctx.fillStyle = '#e2e8f0'
      ctx.font = '11px monospace'
      ctx.fillText(`vol ${Math.round(rms * 300)}%`, 6, 14)
    }
    draw()

    const container = document.getElementById('waveform-container')
    if (container) container.appendChild(canvas)
  }, [])

  const stopVisualizer = useCallback(() => {
    if (animFrameRef.current !== null) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = null
    }
    analyserRef.current = null
    if (canvasRef.current) {
      canvasRef.current.remove()
      canvasRef.current = null
    }
    const container = document.getElementById('waveform-container')
    if (container) container.innerHTML = ''
  }, [])

  // ─── 音频采集入口 ─────────────────────────────────────────────────
  const startRecording = useCallback(async (source: Source) => {
    try {
      setError(null)
      setPermission('idle')

      const isSecure = window.isSecureContext
      const host = window.location.hostname
      const isLocalhost = host === 'localhost' || host === '127.0.0.1' || host === '[::1]'

      if (!isSecure && !isLocalhost) {
        const httpsUrl = window.location.href.replace(/^http:\/\//i, 'https://')
        throw new Error(
          `浏览器禁用了媒体设备 API（非 HTTPS 安全上下文）。\n` +
          `当前地址: ${window.location.origin}\n\n` +
          `【解决方案】请使用 HTTPS 访问：\n  ${httpsUrl}\n\n` +
          `（首次访问 HTTPS 链接时浏览器会提示「不安全」，点击「高级 → 继续前往」即可）`
        )
      }

      if (!navigator.mediaDevices) {
        throw new Error('navigator.mediaDevices 不可用（可能浏览器禁用了媒体设备 API）。')
      }

      let stream: MediaStream
      if (source === 'mic') {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
      } else {
        if (!navigator.mediaDevices.getDisplayMedia) {
          throw new Error('当前浏览器不支持 getDisplayMedia API。')
        }
        try {
          const raw = await navigator.mediaDevices.getDisplayMedia({
            audio: true,
            video: { width: 1, height: 1, frameRate: 1 },
          })
          raw.getVideoTracks().forEach((t) => t.stop())
          const audioTracks = raw.getAudioTracks()
          if (audioTracks.length === 0) {
            throw new Error(
              '未获取到系统音频轨道。\n\n' +
              '请在弹窗中选择要共享的窗口/屏幕，\n并勾选窗口底部的「🔊 共享系统音频」复选框。'
            )
          }
          stream = new MediaStream(audioTracks)
        } catch (err) {
          if (err instanceof Error && err.name === 'NotSupportedError') {
            throw new Error(
              '系统音频捕获不支持。\n\n请在弹窗中选择要共享的窗口/标签页，\n并勾选窗口底部「🔊 共享音频」复选框。'
            )
          }
          throw err
        }
      }

      streamRef.current = stream
      setPermission('granted')
      setAudioSource(source)
      setSelectedSource(source)

      // 复用可视化（它也会创建 AudioContext，这里等一下让两者共用）
      startVisualizer(stream)

      // AudioWorklet 采集原始 PCM
      const audioCtx = audioCtxRef.current || new AudioContext()
      audioCtxRef.current = audioCtx
      const workletUrl = makePcmWorkletUrl()
      workletUrlRef.current = workletUrl
      await audioCtx.audioWorklet.addModule(workletUrl)

      const worklet = new AudioWorkletNode(audioCtx, 'pcm-processor')
      workletRef.current = worklet
      pcmBufRef.current = []

      // 接收 worklet 发来的 Int16 PCM buffer
      worklet.port.onmessage = (e) => {
        const pcm = new Int16Array(e.data.pcm)
        pcmBufRef.current.push(pcm)
      }

      // 把麦克风流接入 worklet（音频从 input 到 output，worklet 在中间拦截）
      const micSrc = audioCtx.createMediaStreamSource(stream)
      micSrc.connect(worklet)
      // 必须 connect 到 destination 否则 Chrome 可能不处理（部分浏览器要求）
      worklet.connect(audioCtx.destination)

      setRecording(true)

      // 每 3 秒把累积的 PCM 发给后端
      sendTimerRef.current = setInterval(() => {
        if (!ws || ws.readyState !== WebSocket.OPEN || pcmBufRef.current.length === 0) return

        const all = pcmBufRef.current
        pcmBufRef.current = []

        let totalLen = 0
        for (const b of all) totalLen += b.length
        const merged = new Int16Array(totalLen)
        let offset = 0
        for (const b of all) { merged.set(b, offset); offset += b.length }

        const u8 = new Uint8Array(merged.buffer)
        const binary = String.fromCharCode(...u8)
        const base64 = btoa(binary)
        ws.sendAudioChunk(source, base64)
      }, 3000)

    } catch (err) {
      console.error('启动录制失败:', err)
      setError(err instanceof Error ? err.message : '启动录制失败')
      setPermission('denied')
      cleanup()
    }
  }, [ws, setRecording, setAudioSource, startVisualizer])

  const cleanup = useCallback(() => {
    stopVisualizer()

    if (sendTimerRef.current) {
      clearInterval(sendTimerRef.current)
      sendTimerRef.current = null
    }

    if (workletRef.current) {
      try {
        // 通知 processor 把剩余样本flush出来
        workletRef.current.port.postMessage('flush')
        workletRef.current.disconnect()
        workletRef.current.port.onmessage = null
      } catch {}
      workletRef.current = null
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }

    if (audioCtxRef.current) {
      try { audioCtxRef.current.close() } catch {}
      audioCtxRef.current = null
    }

    if (workletUrlRef.current) {
      URL.revokeObjectURL(workletUrlRef.current)
      workletUrlRef.current = ''
    }

    pcmBufRef.current = []
    setRecording(false)
  }, [setRecording, stopVisualizer])

  const stopRecording = useCallback(() => {
    cleanup()
  }, [cleanup])

  useEffect(() => () => {
    cleanup()
  }, [cleanup])

  useEffect(() => {
    if (!ws) return
    ws.onMessage(() => {})
  }, [ws])

  return (
    <div className="flex items-center gap-3">
      <div className="flex gap-2">
        <button
          onClick={() => startRecording('mic')}
          disabled={isRecording}
          className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
            isRecording && selectedSource === 'mic'
              ? 'bg-red-600 hover:bg-red-700'
              : 'bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed'
          }`}
          title="麦克风输入"
        >
          麦克风
        </button>
        <button
          onClick={() => startRecording('system')}
          disabled={isRecording}
          className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
            isRecording && selectedSource === 'system'
              ? 'bg-red-600 hover:bg-red-700'
              : 'bg-purple-600 hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed'
          }`}
          title="系统音频"
        >
          系统音频
        </button>
      </div>

      {isRecording && (
        <button
          onClick={stopRecording}
          className="px-4 py-1.5 bg-red-700 hover:bg-red-800 rounded-lg text-sm transition-colors"
        >
          停止录制
        </button>
      )}

      {error && (
        <span className="text-xs text-red-400 max-w-xs truncate" title={error}>
          {error}
        </span>
      )}

      {permission === 'denied' && !error && (
        <span className="text-xs text-yellow-400">
          麦克风权限被拒绝，请在浏览器设置中允许访问
        </span>
      )}

      {permission === 'granted' && !error && (
        <span className="text-xs text-green-400">
          {selectedSource === 'mic' ? '麦克风就绪' : '系统音频就绪'}
          {isRecording && ' · 录制中...'}
        </span>
      )}

      {/* 质量优先模式切换 */}
      <button
        onClick={() => {
          const next = !qualityMode
          setQualityMode(next)
          ws?.sendQualityMode(next)
        }}
        className={`px-2 py-1 rounded text-xs border transition-colors ${
          qualityMode
            ? 'bg-amber-600 border-amber-500 text-white'
            : 'bg-transparent border-gray-600 text-gray-400 hover:border-gray-400'
        }`}
        title={qualityMode ? '质量优先模式：积累更长音频，精度更高，延迟约15秒' : '实时模式：低延迟，但精度略低'}
      >
        {qualityMode ? '质量优先' : '实时'}
      </button>
    </div>
  )
}
