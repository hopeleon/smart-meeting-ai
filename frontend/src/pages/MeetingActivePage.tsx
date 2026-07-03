import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getMeeting, updateMeetingStatus, checkBackendHealth, type ModelStatus } from '../api/meetings'
import { HybridWebSocket } from '../api/websocket'
import { useMeetingStore } from '../stores/meetingStore'
import type { TranscriptSegment } from '../types/meeting'

type AudioSource = 'mic' | 'system'
type CaptureState = 'idle' | 'starting' | 'recording' | 'stopping'

const PROCESSOR_CODE = `
class HybridPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / 16000
    this.next = 0
    this.out = []
    this.port.onmessage = (event) => {
      if (event.data === 'flush') this.flush()
    }
  }

  flush() {
    if (!this.out.length) return
    const frame = new Int16Array(this.out)
    this.port.postMessage({ pcm: frame.buffer }, [frame.buffer])
    this.out = []
  }

  emitFrames() {
    while (this.out.length >= 1600) {
      const frame = new Int16Array(this.out.slice(0, 1600))
      this.out = this.out.slice(1600)
      this.port.postMessage({ pcm: frame.buffer }, [frame.buffer])
    }
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || !input[0]) return true
    const ch = input[0]
    let sum = 0
    for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i]
    const rms = Math.sqrt(sum / Math.max(1, ch.length))

    while (this.next < ch.length) {
      const sample = ch[Math.floor(this.next)] || 0
      const clipped = Math.max(-1, Math.min(1, sample))
      this.out.push(clipped < 0 ? clipped * 32768 : clipped * 32767)
      this.next += this.ratio
    }
    this.next -= ch.length
    this.emitFrames()
    this.port.postMessage({ level: rms })
    return true
  }
}
registerProcessor('hybrid-pcm-processor', HybridPcmProcessor)
`

function makeWorkletUrl(): string {
  return URL.createObjectURL(new Blob([PROCESSOR_CODE], { type: 'application/javascript' }))
}

function formatTime(ms?: number): string {
  if (ms == null || Number.isNaN(ms)) return '--:--'
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60).toString().padStart(2, '0')
  const s = (total % 60).toString().padStart(2, '0')
  return `${m}:${s}`
}

function segmentFromMessage(raw: Record<string, unknown>, meetingId: string): TranscriptSegment | null {
  const text = String(raw.text || '').trim()
  if (!text) return null
  const id = String(raw.segment_id || raw.id || crypto.randomUUID())
  const speakerName = String(raw.speaker_name || raw.speaker_label || raw.speaker_id || 'unknown')
  return {
    id,
    segment_id: id,
    meeting_id: meetingId,
    speaker_id: String(raw.speaker_id || 'unknown'),
    speaker_label: speakerName,
    speaker_name: speakerName,
    text,
    source: String(raw.source || ''),
    start_ms: Number(raw.start_ms || 0),
    end_ms: Number(raw.end_ms || 0),
    start_time: Number(raw.start_time ?? Number(raw.start_ms || 0) / 1000),
    end_time: Number(raw.end_time ?? Number(raw.end_ms || 0) / 1000),
    confidence: Number(raw.confidence || raw.speaker_confidence || 1),
    speaker_confidence: Number(raw.speaker_confidence || raw.confidence || 1),
    identified: Boolean(raw.identified),
    is_final: true,
    original_text: raw.original_text ? String(raw.original_text) : null,
    qwen_text: raw.qwen_text ? String(raw.qwen_text) : null,
    revision_status: raw.revision_status ? String(raw.revision_status) : undefined,
    revision_accepted: raw.revision_accepted as boolean | undefined,
    stable: raw.stable as boolean | undefined,
    created_at: new Date().toISOString(),
  }
}

export default function MeetingActivePage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const {
    currentMeeting,
    transcripts,
    clearCurrent,
    setCurrentMeeting,
    setTranscripts,
    upsertTranscript,
  } = useMeetingStore()

  const wsRef = useRef<HybridWebSocket | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const workletRef = useRef<AudioWorkletNode | null>(null)
  const workletUrlRef = useRef('')
  const analyserRef = useRef<AnalyserNode | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const animRef = useRef<number | null>(null)
  const startedAtRef = useRef<number | null>(null)

  const [connected, setConnected] = useState(false)
  const [captureState, setCaptureState] = useState<CaptureState>('idle')
  const [source, setSource] = useState<AudioSource>('mic')
  const [level, setLevel] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [statusLine, setStatusLine] = useState('正在连接实时引擎')
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)

  const enhancedCount = useMemo(
    () => transcripts.filter(t => t.revision_status === 'enhanced').length,
    [transcripts],
  )
  const pendingCount = useMemo(
    () => transcripts.filter(t => t.revision_status === 'pending').length,
    [transcripts],
  )

  const cleanupCapture = useCallback(() => {
    if (animRef.current != null) {
      cancelAnimationFrame(animRef.current)
      animRef.current = null
    }
    if (workletRef.current) {
      try { workletRef.current.port.postMessage('flush') } catch {}
      try { workletRef.current.disconnect() } catch {}
      workletRef.current.port.onmessage = null
      workletRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop())
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
    analyserRef.current = null
    startedAtRef.current = null
    setLevel(0)
    setElapsed(0)
    setCaptureState('idle')
  }, [])

  const drawWaveform = useCallback(() => {
    const canvas = canvasRef.current
    const analyser = analyserRef.current
    if (!canvas || !analyser) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const data = new Uint8Array(analyser.frequencyBinCount)
    const draw = () => {
      animRef.current = requestAnimationFrame(draw)
      analyser.getByteTimeDomainData(data)
      const w = canvas.width
      const h = canvas.height
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = '#f7f8fa'
      ctx.fillRect(0, 0, w, h)
      ctx.lineWidth = 2
      ctx.strokeStyle = '#2563eb'
      ctx.beginPath()
      for (let i = 0; i < data.length; i++) {
        const x = (i / (data.length - 1)) * w
        const y = (data[i] / 255) * h
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
      ctx.fillStyle = '#16a34a'
      ctx.fillRect(0, h - 4, Math.min(w, w * Math.min(1, level * 8)), 4)
    }
    draw()
  }, [level])

  useEffect(() => {
    if (!meetingId) return
    clearCurrent()
    setTranscripts([])
    getMeeting(meetingId).then(setCurrentMeeting)
    checkBackendHealth().then(setModelStatus)

    const ws = new HybridWebSocket(meetingId)
    wsRef.current = ws
    ws.onStatusChange((ok) => {
      setConnected(ok)
      setStatusLine(ok ? '实时引擎已连接' : '实时引擎连接中')
    })
    ws.onMessage((msg) => {
      const type = String(msg.type || '')
      if (type === 'session.ready') {
        setStatusLine('FunASR 实时初稿 + Qwen 增强已就绪')
        return
      }
      if (type === 'error') {
        setError(String(msg.message || '服务端错误'))
        return
      }
      if (type === 'transcript.completed' || type === 'transcript.revised') {
        const segment = segmentFromMessage(msg, meetingId)
        if (segment) upsertTranscript(segment)
        return
      }
      if (type === 'ready_to_stop') {
        setStatusLine('服务端已完成收尾')
      }
    })
    ws.connect()

    return () => {
      cleanupCapture()
      ws.disconnect()
      wsRef.current = null
    }
  }, [meetingId, clearCurrent, cleanupCapture, setCurrentMeeting, setTranscripts, upsertTranscript])

  useEffect(() => {
    if (captureState !== 'recording') return
    const timer = setInterval(() => {
      if (startedAtRef.current) setElapsed(Date.now() - startedAtRef.current)
    }, 500)
    return () => clearInterval(timer)
  }, [captureState])

  const startCapture = useCallback(async (nextSource: AudioSource) => {
    if (!meetingId || captureState !== 'idle') return
    setError(null)
    setCaptureState('starting')
    setSource(nextSource)
    try {
      if (!window.isSecureContext && !['localhost', '127.0.0.1'].includes(window.location.hostname)) {
        throw new Error('浏览器要求 HTTPS 才能启用麦克风。请使用 https 地址打开。')
      }
      if (!navigator.mediaDevices) {
        throw new Error('当前浏览器无法访问媒体设备。')
      }

      let stream: MediaStream
      if (nextSource === 'mic') {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
          },
        })
      } else {
        const raw = await navigator.mediaDevices.getDisplayMedia({
          audio: true,
          video: { width: 1, height: 1, frameRate: 1 },
        })
        raw.getVideoTracks().forEach(track => track.stop())
        const tracks = raw.getAudioTracks()
        if (!tracks.length) throw new Error('没有获取到系统音频，请在共享窗口时勾选共享音频。')
        stream = new MediaStream(tracks)
      }

      const audioCtx = new AudioContext()
      const workletUrl = makeWorkletUrl()
      await audioCtx.audioWorklet.addModule(workletUrl)

      const mediaSource = audioCtx.createMediaStreamSource(stream)
      const analyser = audioCtx.createAnalyser()
      analyser.fftSize = 2048
      const worklet = new AudioWorkletNode(audioCtx, 'hybrid-pcm-processor')
      const silentGain = audioCtx.createGain()
      silentGain.gain.value = 0

      worklet.port.onmessage = (event) => {
        if (event.data?.pcm) {
          wsRef.current?.sendAudioFrame(event.data.pcm)
        }
        if (typeof event.data?.level === 'number') {
          setLevel(event.data.level)
        }
      }

      mediaSource.connect(analyser)
      mediaSource.connect(worklet)
      worklet.connect(silentGain)
      silentGain.connect(audioCtx.destination)

      streamRef.current = stream
      audioCtxRef.current = audioCtx
      workletRef.current = worklet
      workletUrlRef.current = workletUrl
      analyserRef.current = analyser
      startedAtRef.current = Date.now()
      setCaptureState('recording')
      setStatusLine(nextSource === 'mic' ? '麦克风实时转录中' : '系统音频实时转录中')
      await updateMeetingStatus(meetingId, 'processing')
      drawWaveform()
    } catch (err) {
      cleanupCapture()
      setError(err instanceof Error ? err.message : '启动音频采集失败')
    }
  }, [captureState, cleanupCapture, drawWaveform, meetingId])

  const stopCapture = useCallback(() => {
    setCaptureState('stopping')
    cleanupCapture()
    setStatusLine('已停止采集，可继续查看增强结果')
  }, [cleanupCapture])

  const endMeeting = useCallback(async () => {
    if (!meetingId) return
    setCaptureState('stopping')
    wsRef.current?.endMeeting()
    cleanupCapture()
    await updateMeetingStatus(meetingId, 'ended')
    wsRef.current?.disconnect()
    navigate(`/meeting/${meetingId}/summary`)
  }, [cleanupCapture, meetingId, navigate])

  if (!currentMeeting) {
    return <div className="min-h-[60vh] grid place-items-center text-gray-500">加载会议中...</div>
  }

  const recording = captureState === 'recording'

  return (
    <div className="min-h-[calc(100vh-5.5rem)] bg-[#f4f6f8] text-[#1f2937] -m-6 p-5">
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#d9dee7] pb-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-semibold">{currentMeeting.title}</h1>
              <span className="rounded-md bg-[#e8f1ff] px-2 py-1 text-xs font-medium text-[#1d4ed8]">
                混合实时
              </span>
              <span className={`rounded-md px-2 py-1 text-xs font-medium ${connected ? 'bg-[#e7f7ed] text-[#15803d]' : 'bg-[#fff4d6] text-[#a16207]'}`}>
                {connected ? '已连接' : '连接中'}
              </span>
            </div>
            <p className="mt-1 text-sm text-[#64748b]">{statusLine}</p>
          </div>
          <button
            onClick={endMeeting}
            className="rounded-md bg-[#dc2626] px-4 py-2 text-sm font-medium text-white hover:bg-[#b91c1c]"
          >
            结束会议
          </button>
        </header>

        <section className="grid min-h-[68vh] gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <main className="flex min-h-0 flex-col rounded-md border border-[#d9dee7] bg-white">
            <div className="flex items-center justify-between border-b border-[#e5e7eb] px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold">实时转写</h2>
                <p className="text-xs text-[#64748b]">FunASR 先出字幕，Qwen 完成后原地增强</p>
              </div>
              <div className="text-xs text-[#64748b]">
                {transcripts.length} 句 · {enhancedCount} 句已增强
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {transcripts.length === 0 ? (
                <div className="grid h-full min-h-[320px] place-items-center text-center">
                  <div>
                    <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-md bg-[#e8f1ff] text-[#1d4ed8]">
                      <svg width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                        <path d="M12 18a6 6 0 0 0 6-6V7a6 6 0 0 0-12 0v5a6 6 0 0 0 6 6Z" />
                        <path d="M19 12a7 7 0 0 1-14 0M12 19v3" />
                      </svg>
                    </div>
                    <p className="font-medium text-[#334155]">点击下方麦克风开始实时转录</p>
                    <p className="mt-1 text-sm text-[#64748b]">建议先注册常用说话人声纹，以提升身份识别稳定性。</p>
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  {transcripts.map((item) => (
                    <article key={item.segment_id || item.id} className="rounded-md border border-[#e5e7eb] bg-[#fbfcfd] px-3 py-2">
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-sm font-semibold text-[#111827]">
                            {item.speaker_name || item.speaker_label || item.speaker_id}
                          </span>
                          <span className="text-xs text-[#64748b]">{formatTime(item.start_ms)} - {formatTime(item.end_ms)}</span>
                        </div>
                        <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                          item.revision_status === 'enhanced'
                            ? 'bg-[#e7f7ed] text-[#15803d]'
                            : item.revision_status === 'pending'
                              ? 'bg-[#fff4d6] text-[#a16207]'
                              : 'bg-[#eef2f7] text-[#475569]'
                        }`}>
                          {item.revision_status === 'enhanced' ? 'Qwen 已增强' : item.revision_status === 'pending' ? '增强中' : '已稳定'}
                        </span>
                      </div>
                      <p className="text-[15px] leading-7 text-[#1f2937]">{item.text}</p>
                      {item.original_text && item.original_text !== item.text && (
                        <p className="mt-1 text-xs text-[#94a3b8]">FunASR 初稿：{item.original_text}</p>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </div>
          </main>

          <aside className="flex flex-col gap-4">
            <section className="rounded-md border border-[#d9dee7] bg-white p-4">
              <h2 className="text-sm font-semibold">引擎状态</h2>
              <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                <div className="rounded-md bg-[#f7f8fa] p-3">
                  <div className="text-xs text-[#64748b]">实时字幕</div>
                  <div className="mt-1 font-semibold text-[#15803d]">FunASR</div>
                </div>
                <div className="rounded-md bg-[#f7f8fa] p-3">
                  <div className="text-xs text-[#64748b]">质量增强</div>
                  <div className="mt-1 font-semibold text-[#1d4ed8]">Qwen</div>
                </div>
                <div className="rounded-md bg-[#f7f8fa] p-3">
                  <div className="text-xs text-[#64748b]">待增强</div>
                  <div className="mt-1 font-semibold">{pendingCount}</div>
                </div>
                <div className="rounded-md bg-[#f7f8fa] p-3">
                  <div className="text-xs text-[#64748b]">已增强</div>
                  <div className="mt-1 font-semibold">{enhancedCount}</div>
                </div>
              </div>
              {modelStatus && (
                <div className="mt-3 text-xs text-[#64748b]">
                  ASR {modelStatus.models.funasr ? 'ready' : 'missing'} · VAD {modelStatus.models.vad ? 'ready' : 'missing'} · CAM++ {modelStatus.models.campplus ? 'ready' : 'missing'}
                </div>
              )}
            </section>

            <section className="rounded-md border border-[#d9dee7] bg-white p-4">
              <h2 className="text-sm font-semibold">音频输入</h2>
              <canvas ref={canvasRef} width={280} height={72} className="mt-3 h-[72px] w-full rounded-md border border-[#e5e7eb]" />
              <div className="mt-3 flex items-center justify-between text-sm">
                <span className="text-[#64748b]">时长</span>
                <span className="font-semibold">{formatTime(elapsed)}</span>
              </div>
              <div className="mt-1 flex items-center justify-between text-sm">
                <span className="text-[#64748b]">输入</span>
                <span className="font-semibold">{source === 'mic' ? '麦克风' : '系统音频'}</span>
              </div>
              {error && <p className="mt-3 rounded-md bg-[#fef2f2] p-2 text-sm text-[#b91c1c]">{error}</p>}
            </section>
          </aside>
        </section>

        <footer className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-[#d9dee7] bg-white px-4 py-3">
          <div className="flex items-center gap-3">
            <button
              onClick={() => startCapture('mic')}
              disabled={!connected || captureState !== 'idle'}
              className="inline-flex items-center gap-2 rounded-md bg-[#16a34a] px-4 py-2 text-sm font-medium text-white hover:bg-[#15803d] disabled:cursor-not-allowed disabled:bg-[#9ca3af]"
            >
              <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path d="M12 18a6 6 0 0 0 6-6V7a6 6 0 0 0-12 0v5a6 6 0 0 0 6 6Z" />
                <path d="M19 12a7 7 0 0 1-14 0M12 19v3" />
              </svg>
              开始麦克风
            </button>
            <button
              onClick={() => startCapture('system')}
              disabled={!connected || captureState !== 'idle'}
              className="inline-flex items-center gap-2 rounded-md border border-[#cbd5e1] px-4 py-2 text-sm font-medium text-[#334155] hover:bg-[#f8fafc] disabled:cursor-not-allowed disabled:text-[#9ca3af]"
            >
              <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <rect x="3" y="4" width="18" height="12" rx="2" />
                <path d="M8 20h8M12 16v4" />
              </svg>
              系统音频
            </button>
            {recording && (
              <button
                onClick={stopCapture}
                className="inline-flex items-center gap-2 rounded-md bg-[#dc2626] px-4 py-2 text-sm font-medium text-white hover:bg-[#b91c1c]"
              >
                <span className="h-2.5 w-2.5 rounded-sm bg-white" />
                停止
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 text-sm text-[#64748b]">
            <span className={`h-2 w-2 rounded-full ${recording ? 'bg-[#dc2626] animate-pulse' : connected ? 'bg-[#16a34a]' : 'bg-[#f59e0b]'}`} />
            {recording ? '正在实时推流' : connected ? '可开始录音' : '等待连接'}
          </div>
        </footer>
      </div>
    </div>
  )
}
