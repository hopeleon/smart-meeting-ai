import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { useAudioStore } from '../stores/audioStore'
import { getMeeting, updateMeetingStatus } from '../api/meetings'
import { WhisperWebSocket } from '../api/websocket'
import TranscriptList from '../components/transcript/TranscriptList'
import PeriodSummaryCard from '../components/summary/PeriodSummaryCard'
import type { TranscriptSegment } from '../types/meeting'

type AudioSource = 'mic' | 'system'

// AudioWorklet Processor — 替代已废弃的 ScriptProcessorNode
// 输入：16kHz Float32 PCM（AudioContext 设为 16kHz）
// 输出：Int16 PCM，经 MessagePort 发送到主线程
const PCM_WORKLET_CODE = `
class PcmW extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buf = []
    this.port.onmessage = () => this.flush()
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
      const s = Math.max(-1, Math.min(1, ch[i]))
      this.buf.push(s < 0 ? s * 32768 : s * 32767)
    }
    // 约 0.5s 满 8000 样本时 flush 一次（兼顾延迟和效率）
    if (this.buf.length >= 8000) this.flush()
    return true
  }
}
registerProcessor('pcm-w', PcmW)
`

function makeWorkletUrl(): string {
  return URL.createObjectURL(new Blob([PCM_WORKLET_CODE], { type: 'application/javascript' }))
}

// ─── 波形可视化 ─────────────────────────────────────────────────────
function startWaveformVisualizer(
  stream: MediaStream,
  container: HTMLDivElement,
  canvasRef: React.MutableRefObject<HTMLCanvasElement | null>,
  analyserRef: React.MutableRefObject<AnalyserNode | null>,
  animRef: React.MutableRefObject<number | null>,
) {
  const canvas = document.createElement('canvas')
  canvas.width = 320
  canvas.height = 48
  canvas.style.cssText = 'border-radius:6px;background:#1e1e2e;display:block;'
  canvasRef.current = canvas
  container.appendChild(canvas)

  const audioCtx = new AudioContext()
  const analyser = audioCtx.createAnalyser()
  analyser.fftSize = 512
  analyserRef.current = analyser
  const src = audioCtx.createMediaStreamSource(stream)
  src.connect(analyser)

  const bufLen = analyser.frequencyBinCount
  const dataArr = new Uint8Array(bufLen)

  const draw = () => {
    animRef.current = requestAnimationFrame(draw)
    analyser.getByteTimeDomainData(dataArr)

    const ctx = canvas.getContext('2d')!
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
}

function stopWaveformVisualizer(
  container: HTMLDivElement,
  canvasRef: React.MutableRefObject<HTMLCanvasElement | null>,
  analyserRef: React.MutableRefObject<AnalyserNode | null>,
  animRef: React.MutableRefObject<number | null>,
) {
  if (animRef.current !== null) {
    cancelAnimationFrame(animRef.current)
    animRef.current = null
  }
  analyserRef.current = null
  if (canvasRef.current) {
    canvasRef.current.remove()
    canvasRef.current = null
  }
  container.innerHTML = ''
}

export default function MeetingWhisperPage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const {
    currentMeeting,
    setCurrentMeeting,
    clearCurrent,
    transcripts,
    setTranscripts,
    setPeriodSummaries,
    upsertTranscript,
    periodSummaries,
  } = useMeetingStore()
  const { isRecording, setRecording } = useAudioStore()
  const wsRef = useRef<WhisperWebSocket | null>(null)
  const [wsReady, setWsReady] = useState(false)
  const [permission, setPermission] = useState<'idle' | 'granted' | 'denied'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [selectedSource, setSelectedSource] = useState<AudioSource>('mic')
  const [diar, setDiar] = useState<'sortformer' | 'camplus' | 'pyannote'>('sortformer')

  // AudioWorklet 相关 refs
  const audioCtxRef = useRef<AudioContext | null>(null)
  const workletRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const workletUrlRef = useRef<string>('')
  const pcmBufRef = useRef<Int16Array[]>([])
  const sendTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 波形可视化 refs
  const visCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const visAnalyserRef = useRef<AnalyserNode | null>(null)
  const visAnimRef = useRef<number | null>(null)
  const waveformContainerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!meetingId) return

    clearCurrent()
    setTranscripts([])
    setPeriodSummaries([])

    getMeeting(meetingId).then((meeting) => {
      setCurrentMeeting(meeting)
      if (meeting.status === 'ended') {
        navigate(`/meeting/${meetingId}/summary`)
        return
      }

      const ws = new WhisperWebSocket(meetingId, 'whisper', '?diar=' + diar)
      wsRef.current = ws

      ws.onMessage((msg: Record<string, unknown>) => {
        console.log('[WhisperWS-收到]', JSON.stringify(msg).slice(0, 300))

        if (msg.type === 'transcript.completed') {
          const segment: TranscriptSegment = {
            id: crypto.randomUUID(),
            meeting_id: meetingId,
            speaker_id: (msg.speaker_id as string) || 'unknown',
            speaker_label: (msg.speaker_id as string) || 'unknown',
            text: (msg.text as string) || '',
            start_time: (msg.start_time as number) ?? ((msg.start_ms as number) / 1000),
            end_time: (msg.end_time as number) ?? ((msg.end_ms as number) / 1000),
            start_ms: msg.start_ms as number | undefined,
            end_ms: msg.end_ms as number | undefined,
            is_final: true,
            confidence: (msg.speaker_confidence as number) || 1,
            speaker_name: (msg.speaker_name as string) || (msg.speaker_id as string),
            speaker_confidence: msg.speaker_confidence as number | undefined,
            identified: (msg.identified as boolean) ?? false,
            best_guess_name: (msg.best_guess_name as string | null) ?? null,
            best_guess_score: (msg.best_guess_score as number | null) ?? null,
            created_at: new Date().toISOString(),
          }
          upsertTranscript(segment)
        } else if (msg.type === 'ready_to_stop') {
          console.log('[WhisperWS] 收到 ready_to_stop，服务端处理完毕')
        } else if (msg.type === 'error') {
          setError((msg.message as string) || '服务端发生错误')
        }
      })

      ws.onStatusChange((connected) => {
        setWsReady(connected)
        if (!connected) {
          console.warn('[WhisperWS] 连接已断开')
        }
      })

      ws.connect()
    })

    return () => {
      wsRef.current?.disconnect()
      wsRef.current = null
      setWsReady(false)
      stopAudioCapture()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId, diar])

  const stopAudioCapture = () => {
    if (sendTimerRef.current) {
      clearInterval(sendTimerRef.current)
      sendTimerRef.current = null
    }
    if (waveformContainerRef.current) {
      stopWaveformVisualizer(waveformContainerRef.current, visCanvasRef, visAnalyserRef, visAnimRef)
    }
    if (workletRef.current) {
      workletRef.current.port.postMessage('flush')
      try { workletRef.current.disconnect() } catch {}
      workletRef.current.port.onmessage = null
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
  }

  const startRecording = async (source: AudioSource) => {
    setError(null)
    setSelectedSource(source)
    try {
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

      let stream: MediaStream
      if (source === 'mic') {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error('当前浏览器不支持 getUserMedia API。请使用 Chrome/Firefox/Edge/Safari')
        }
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
      } else {
        if (!navigator.mediaDevices?.getDisplayMedia) {
          throw new Error('当前浏览器不支持 getDisplayMedia API。请使用 Chrome 72+/Edge 79+/Firefox 66+/Safari 13+')
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
              '系统音频捕获不支持。\n\n' +
              '请在弹窗中选择要共享的窗口/标签页，\n并勾选窗口底部「🔊 共享音频」复选框。'
            )
          }
          throw err
        }
      }

      setPermission('granted')
      streamRef.current = stream

      // 波形可视化（共用同一个 stream）
      const visContainer = waveformContainerRef.current!
      startWaveformVisualizer(stream, visContainer, visCanvasRef, visAnalyserRef, visAnimRef)

      // AudioContext 设为 16kHz（Whisper 原生采样率，不需要降采样）
      const audioCtx = new AudioContext({ sampleRate: 16000 })
      audioCtxRef.current = audioCtx

      // 注册 AudioWorklet
      const workletUrl = makeWorkletUrl()
      workletUrlRef.current = workletUrl
      await audioCtx.audioWorklet.addModule(workletUrl)

      const worklet = new AudioWorkletNode(audioCtx, 'pcm-w')
      workletRef.current = worklet
      pcmBufRef.current = []

      worklet.port.onmessage = (e) => {
        pcmBufRef.current.push(new Int16Array(e.data.pcm))
      }

      const src = audioCtx.createMediaStreamSource(stream)
      src.connect(worklet)
      worklet.connect(audioCtx.destination)

      setRecording(true)

      // 每 3 秒把累积的 PCM 发给 Whisper WS
      sendTimerRef.current = setInterval(() => {
        if (!wsRef.current || pcmBufRef.current.length === 0) return
        const all = pcmBufRef.current
        pcmBufRef.current = []
        let totalLen = 0
        for (const b of all) totalLen += b.length
        const merged = new Int16Array(totalLen)
        let offset = 0
        for (const b of all) { merged.set(b, offset); offset += b.length }
        wsRef.current.sendAudioFrame(merged.buffer)
      }, 3000)

    } catch (err) {
      console.error('启动录制失败:', err)
      setError(err instanceof Error ? err.message : '启动录制失败')
      setPermission('denied')
    }
  }

  const stopRecording = () => {
    stopAudioCapture()
    setRecording(false)
  }

  const handleEndMeeting = async () => {
    if (!meetingId) return
    stopRecording()
    wsRef.current?.endMeeting()
    await updateMeetingStatus(meetingId, 'ended')
    navigate(`/meeting/${meetingId}/summary`)
  }

  if (!currentMeeting) {
    return <div className="text-center text-gray-500 py-16">加载中...</div>
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* 顶部信息栏 */}
      <div className="mb-4 flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-900 shadow-sm">
        <div>
          <h1 className="text-lg font-semibold text-slate-950">{currentMeeting.title}</h1>
          <span className="text-xs text-slate-500">
            {currentMeeting.participants.join('、')}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {isRecording && (
            <span className="flex items-center gap-1.5 text-orange-400 text-sm">
              <span className="w-2 h-2 bg-orange-500 rounded-full animate-pulse" />
              {selectedSource === 'mic' ? '🎤 ' : '🖥 '}
              Whisper 录制中
            </span>
          )}
          <button
            onClick={handleEndMeeting}
            className="px-4 py-1.5 bg-red-600 rounded-lg hover:bg-red-700 transition-colors text-sm"
          >
            结束会议
          </button>
        </div>
      </div>

      {/* 主内容区：左1/3转写 + 右2/3总结 */}
      <div className="flex-1 flex gap-4 min-h-0">
        <div className="flex w-1/3 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm">
          <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-950">实时转写 (Whisper)</h2>
            <select
              value={diar}
              onChange={(e) => setDiar(e.target.value as 'sortformer' | 'camplus' | 'pyannote')}
              className="rounded border border-slate-200 bg-white px-1.5 py-1 text-xs text-slate-700"
              title="说话人分离方式（切换会重连）"
            >
              <option value="sortformer">sortformer (≤4人)</option>
              <option value="camplus">CAM++聚类 (无上限)</option>
              <option value="pyannote">pyannote (滑窗)</option>
            </select>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <TranscriptList items={transcripts} />
          </div>
        </div>

        <div className="flex w-2/3 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm">
          <div className="border-b border-slate-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-950">阶段总结</h2>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {periodSummaries.length === 0 ? (
              <div className="py-12 text-center text-slate-500">
                暂无阶段总结，录制开始后每 60 秒自动生成
              </div>
            ) : (
              periodSummaries.map((s) => (
                <PeriodSummaryCard key={s.id} summary={s} />
              ))
            )}
          </div>
        </div>
      </div>

      {/* 底部控制栏 */}
      <div className="mt-4 flex items-center gap-6 rounded-xl border border-slate-200 bg-white px-6 py-4 text-slate-900 shadow-sm">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${wsReady ? 'bg-green-400 animate-pulse' : 'bg-yellow-400'}`} />
          <span className="text-xs text-slate-500">
            {wsReady ? 'Whisper 已连接' : '连接中...'}
          </span>
        </div>

        {/* 波形可视化容器 */}
        <div ref={waveformContainerRef} className="flex-1 min-w-0" />

        {/* 录制控制 */}
        <div className="flex items-center gap-3">
          {!isRecording ? (
            <>
              <button
                onClick={() => startRecording('mic')}
                disabled={!wsReady}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  wsReady
                    ? 'bg-blue-600 hover:bg-blue-700'
                    : 'bg-gray-600 cursor-not-allowed opacity-50'
                }`}
                title="麦克风输入"
              >
                🎤 麦克风
              </button>
              <button
                onClick={() => startRecording('system')}
                disabled={!wsReady}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  wsReady
                    ? 'bg-purple-600 hover:bg-purple-700'
                    : 'bg-gray-600 cursor-not-allowed opacity-50'
                }`}
                title="系统音频"
              >
                🖥 系统音频
              </button>
            </>
          ) : (
            <button
              onClick={stopRecording}
              className="px-4 py-1.5 bg-red-700 hover:bg-red-800 rounded-lg text-sm transition-colors"
            >
              ⏹ 停止录制
            </button>
          )}
        </div>

        {error && (
          <span className="text-xs text-red-400 max-w-xs truncate" title={error}>
            {error}
          </span>
        )}

        {permission === 'denied' && !error && (
          <span className="text-xs text-yellow-400">
            音频权限被拒绝，请在浏览器设置中允许访问
          </span>
        )}

        {permission === 'granted' && !error && (
          <span className="text-xs text-green-400">
            {selectedSource === 'mic' ? '🎤 麦克风就绪' : '🖥 系统音频就绪'}
            {isRecording && ' · 录制中...'}
          </span>
        )}
      </div>
    </div>
  )
}
