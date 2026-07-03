/**
 * 老记：一句话代办日历
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Calendar from 'react-calendar'
import 'react-calendar/dist/Calendar.css'
import {
  clarifyScheduleDraft,
  createScheduleEvent,
  deleteScheduleEvent,
  listScheduleEvents,
  parseScheduleText,
  transcribeScheduleAudio,
} from '../api/schedule'
import type { ScheduleEvent, ScheduleParseResult } from '../types/schedule'

type AudioWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: typeof AudioContext
  }

const EVENT_TYPE_LABELS: Record<string, string> = {
  once: '一次',
  daily: '每天',
  weekly: '每周',
  monthly: '每月',
  yearly: '每年',
}

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']
const EXAMPLE_COMMAND = '老记老记，下周三下午两点和周总开会，要讨论硬件相关问题。'
type MicStatus = 'idle' | 'requesting' | 'connected' | 'silent' | 'error'
type SpeechStatus = 'idle' | 'unsupported' | 'starting' | 'listening' | 'error'
type ListeningTarget = 'command' | 'clarification'
type SystemAsrState = 'idle' | 'starting' | 'recording' | 'stopping'

const SYSTEM_PROCESSOR_CODE = `
class LaojiHybridPcmProcessor extends AudioWorkletProcessor {
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
registerProcessor('laoji-hybrid-pcm-processor', LaojiHybridPcmProcessor)
`

function makeSystemWorkletUrl(): string {
  return URL.createObjectURL(new Blob([SYSTEM_PROCESSOR_CODE], { type: 'application/javascript' }))
}

function mergePcmChunks(chunks: Int16Array[]): Int16Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
  const merged = new Int16Array(total)
  let offset = 0
  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.length
  }
  return merged
}

function encodePcm16Wav(samples: Int16Array, sampleRate = 16000): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeString = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i))
  }
  writeString(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeString(8, 'WAVE')
  writeString(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeString(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i += 1) {
    view.setInt16(44 + i * 2, samples[i], true)
  }
  return buffer
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
  }
  return btoa(binary)
}

function normalizeLaojiTranscript(text: string): string {
  let cleaned = text.trim()
  const wakeWord = /(老记|老纪|老计|老季|牢记|小记)/g
  const prefixMatch = cleaned.match(/^((?:老记|老纪|老计|老季|牢记|小记)[，,。\s]*){1,2}/)
  if (!prefixMatch) return cleaned
  const prefix = prefixMatch[0].replace(wakeWord, '老记')
  return `${prefix}${cleaned.slice(prefixMatch[0].length)}`
}

function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function dateFromISO(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function formatDisplayDate(d: Date): string {
  const weekday = WEEKDAY_LABELS[d.getDay() === 0 ? 6 : d.getDay() - 1]
  return `${d.getMonth() + 1}月${d.getDate()}日 周${weekday}`
}

function formatEventTime(ev: Pick<ScheduleEvent, 'is_all_day' | 'start_time' | 'end_time'>): string {
  if (ev.is_all_day || !ev.start_time) return '全天'
  return `${ev.start_time}${ev.end_time ? ` - ${ev.end_time}` : ''}`
}

function getParseSourceLabel(source: string): string {
  if (source === 'local_llm') return '本地大模型'
  if (source === 'rules') return '规则解析'
  return '混合解析'
}

function parseApiError(err: unknown, fallback: string): string {
  if (typeof err === 'object' && err !== null && 'response' in err) {
    const response = (err as { response?: { data?: { detail?: string } } }).response
    if (response?.data?.detail) return response.data.detail
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

export default function SchedulePage() {
  const navigate = useNavigate()
  const waveformCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const micStreamRef = useRef<MediaStream | null>(null)
  const animationFrameRef = useRef<number | null>(null)
  const lowSignalSinceRef = useRef<number | null>(null)
  const systemWorkletRef = useRef<AudioWorkletNode | null>(null)
  const systemWorkletUrlRef = useRef('')
  const audioChunksRef = useRef<Int16Array[]>([])
  const recordingTargetRef = useRef<ListeningTarget>('command')
  const recordingSourceRef = useRef<'mic' | 'system'>('mic')

  const [focusedMonth, setFocusedMonth] = useState(new Date())
  const [selectedDate, setSelectedDate] = useState(new Date())
  const [events, setEvents] = useState<ScheduleEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [commandText, setCommandText] = useState('')
  const [draft, setDraft] = useState<ScheduleParseResult | null>(null)
  const [listening, setListening] = useState(false)
  const [listeningTarget, setListeningTarget] = useState<ListeningTarget | null>(null)
  const [systemAsrState, setSystemAsrState] = useState<SystemAsrState>('idle')
  const [parsing, setParsing] = useState(false)
  const [clarifying, setClarifying] = useState(false)
  const [saving, setSaving] = useState(false)
  const [clarificationAnswer, setClarificationAnswer] = useState('')
  const [audioLevel, setAudioLevel] = useState(0)
  const [micStatus, setMicStatus] = useState<MicStatus>('idle')
  const [speechStatus, setSpeechStatus] = useState<SpeechStatus>('idle')
  const [micMessage, setMicMessage] = useState('等待麦克风权限')
  const [speechMessage, setSpeechMessage] = useState('等待开始录音')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [lastSavedEvent, setLastSavedEvent] = useState<ScheduleEvent | null>(null)

  const refreshEvents = useCallback(
    async (monthDate = focusedMonth) => {
      setLoading(true)
      try {
        const res = await listScheduleEvents(monthDate.getFullYear(), monthDate.getMonth() + 1)
        setEvents(res.events)
      } finally {
        setLoading(false)
      }
    },
    [focusedMonth],
  )

  useEffect(() => {
    refreshEvents()
  }, [refreshEvents])

  const eventsByDate = useMemo(() => {
    const grouped = new Map<string, ScheduleEvent[]>()
    for (const ev of events) {
      const existing = grouped.get(ev.start_date) || []
      existing.push(ev)
      grouped.set(ev.start_date, existing)
    }
    return grouped
  }, [events])

  const selectedEvents = eventsByDate.get(formatDate(selectedDate)) || []

  const draftConflicts = useMemo(() => {
    if (!draft) return []
    const dayEvents = eventsByDate.get(draft.start_date) || []
    if (dayEvents.length === 0) return []
    if (draft.is_all_day || !draft.start_time) return dayEvents

    const draftStart = draft.start_time
    const draftEnd = draft.end_time || draft.start_time
    return dayEvents.filter((event) => {
      if (event.is_all_day || !event.start_time) return true
      const eventStart = event.start_time
      const eventEnd = event.end_time || event.start_time
      return eventStart < draftEnd && eventEnd > draftStart
    })
  }, [draft, eventsByDate])

  const drawWaveform = useCallback(() => {
    const canvas = waveformCanvasRef.current
    const analyser = analyserRef.current
    if (!canvas || !analyser) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const ratio = window.devicePixelRatio || 1
    const width = Math.max(320, Math.floor(canvas.clientWidth * ratio))
    const height = Math.max(88, Math.floor(canvas.clientHeight * ratio))
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width
      canvas.height = height
    }

    const bufferLength = analyser.frequencyBinCount
    const dataArray = new Uint8Array(bufferLength)

    const render = () => {
      analyser.getByteTimeDomainData(dataArray)

      const gradient = ctx.createLinearGradient(0, 0, width, height)
      gradient.addColorStop(0, '#f0f9ff')
      gradient.addColorStop(1, '#ecfdf5')
      ctx.fillStyle = gradient
      ctx.fillRect(0, 0, width, height)

      ctx.strokeStyle = '#bae6fd'
      ctx.lineWidth = 1 * ratio
      for (let y = height / 4; y < height; y += height / 4) {
        ctx.beginPath()
        ctx.moveTo(0, y)
        ctx.lineTo(width, y)
        ctx.stroke()
      }

      ctx.lineWidth = 3 * ratio
      ctx.strokeStyle = '#0284c7'
      ctx.beginPath()
      const sliceWidth = width / bufferLength
      let x = 0
      let sum = 0

      for (let i = 0; i < bufferLength; i += 1) {
        const v = dataArray[i] / 128
        const y = (v * height) / 2
        const deviation = Math.abs(dataArray[i] - 128) / 128
        sum += deviation
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
        x += sliceWidth
      }

      ctx.lineTo(width, height / 2)
      ctx.stroke()

      const nextLevel = Math.min(100, Math.round((sum / bufferLength) * 260))
      if (nextLevel >= 3) {
        lowSignalSinceRef.current = null
        setMicStatus((current) => (current === 'silent' ? 'connected' : current))
        setMicMessage('麦克风已连接，检测到输入')
      } else {
        lowSignalSinceRef.current = lowSignalSinceRef.current || performance.now()
        if (performance.now() - lowSignalSinceRef.current > 2500) {
          setMicStatus('silent')
          setMicMessage('麦克风已连接，但暂未检测到明显声音')
        }
      }
      setAudioLevel((prev) => (Math.abs(prev - nextLevel) > 2 ? nextLevel : prev))
      animationFrameRef.current = requestAnimationFrame(render)
    }

    render()
  }, [])

  const stopAudioMonitor = useCallback(() => {
    if (animationFrameRef.current != null) {
      cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }

    audioSourceRef.current?.disconnect()
    audioSourceRef.current = null
    analyserRef.current = null

    micStreamRef.current?.getTracks().forEach((track) => track.stop())
    micStreamRef.current = null

    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      void audioContextRef.current.close()
    }
    audioContextRef.current = null
    setAudioLevel(0)
    lowSignalSinceRef.current = null

    const canvas = waveformCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
  }, [])

  const cleanupSystemAudioCapture = useCallback(() => {
    if (systemWorkletRef.current) {
      try { systemWorkletRef.current.port.postMessage('flush') } catch {}
      try { systemWorkletRef.current.disconnect() } catch {}
      systemWorkletRef.current.port.onmessage = null
      systemWorkletRef.current = null
    }

    if (systemWorkletUrlRef.current) {
      URL.revokeObjectURL(systemWorkletUrlRef.current)
      systemWorkletUrlRef.current = ''
    }
    stopAudioMonitor()
  }, [stopAudioMonitor])

  const closeRecordingSession = useCallback((message = '语音采集已停止') => {
    cleanupSystemAudioCapture()
    audioChunksRef.current = []
    setListening(false)
    setListeningTarget(null)
    setSystemAsrState('idle')
    setMicStatus('idle')
    setMicMessage(message)
    setSpeechStatus('idle')
    setSpeechMessage(message)
  }, [cleanupSystemAudioCapture])

  const setupAudioCapture = useCallback(async (
    stream: MediaStream,
    options: {
      source: 'mic' | 'system'
      target: ListeningTarget
      message: string
    },
  ) => {
    stopAudioMonitor()
    const audioWindow = window as AudioWindow
    const AudioContextCtor = audioWindow.AudioContext || audioWindow.webkitAudioContext
    if (!AudioContextCtor) {
      stream.getTracks().forEach((track) => track.stop())
      throw new Error('当前浏览器不支持音频采集')
    }

    const audioContext = new AudioContextCtor()
    const workletUrl = makeSystemWorkletUrl()
    await audioContext.audioWorklet.addModule(workletUrl)

    const source = audioContext.createMediaStreamSource(stream)
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.78
    const worklet = new AudioWorkletNode(audioContext, 'laoji-hybrid-pcm-processor')
    const silentGain = audioContext.createGain()
    silentGain.gain.value = 0

    audioChunksRef.current = []
    recordingTargetRef.current = options.target
    recordingSourceRef.current = options.source

    worklet.port.onmessage = (event) => {
      if (event.data?.pcm) {
        audioChunksRef.current.push(new Int16Array(event.data.pcm).slice())
      }
      if (typeof event.data?.level === 'number') {
        setAudioLevel(Math.min(100, Math.round(event.data.level * 420)))
      }
    }

    source.connect(analyser)
    source.connect(worklet)
    worklet.connect(silentGain)
    silentGain.connect(audioContext.destination)

    micStreamRef.current = stream
    audioContextRef.current = audioContext
    audioSourceRef.current = source
    analyserRef.current = analyser
    systemWorkletRef.current = worklet
    systemWorkletUrlRef.current = workletUrl
    setMicStatus('connected')
    setMicMessage(options.message)
    setSpeechStatus('listening')
    setSpeechMessage(options.message)
    drawWaveform()
  }, [drawWaveform, stopAudioMonitor])

  const finishCapturedAsr = useCallback(async (message = '正在转写语音') => {
    if (!systemWorkletRef.current) return
    const source = recordingSourceRef.current
    const target = recordingTargetRef.current

    if (source === 'system') {
      setSystemAsrState('stopping')
    } else {
      setListening(false)
      setListeningTarget(null)
    }
    setMicMessage(message)
    setSpeechStatus('starting')
    setSpeechMessage(message)
    setNotice(message)

    try { systemWorkletRef.current.port.postMessage('flush') } catch {}
    await new Promise((resolve) => window.setTimeout(resolve, 220))

    const chunks = audioChunksRef.current
    cleanupSystemAudioCapture()

    try {
      const samples = mergePcmChunks(chunks)
      const duration = samples.length / 16000
      if (duration < 0.25) {
        throw new Error('录音太短，没有可识别的语音')
      }
      const wav = encodePcm16Wav(samples, 16000)
      const audioBase64 = arrayBufferToBase64(wav)
      const result = await transcribeScheduleAudio(
        audioBase64,
        `${source}-${target}-${Date.now()}.wav`,
      )
      const text = normalizeLaojiTranscript(result.text)
      if (!text) throw new Error('没有识别到有效语音')

      if (target === 'command') {
        setCommandText((current) => `${current.trim()}${current.trim() ? '，' : ''}${text}`.trim())
      } else {
        setClarificationAnswer((current) => `${current.trim()}${current.trim() ? '，' : ''}${text}`.trim())
      }
      setNotice(`已转写 ${result.duration_sec.toFixed(1)} 秒语音：${text}`)
      setMicMessage('语音转写完成')
      setSpeechStatus('idle')
      setSpeechMessage('语音转写完成')
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '语音转写失败'
      setError(errorMessage)
      setMicStatus('error')
      setMicMessage(errorMessage)
      setSpeechStatus('error')
      setSpeechMessage(errorMessage)
    } finally {
      audioChunksRef.current = []
      setListening(false)
      setListeningTarget(null)
      setSystemAsrState('idle')
    }
  }, [cleanupSystemAudioCapture])

  useEffect(() => {
    return () => {
      closeRecordingSession()
      stopAudioMonitor()
    }
  }, [closeRecordingSession, stopAudioMonitor])

  const handleListen = async (target: ListeningTarget = 'command') => {
    if (listening) {
      await finishCapturedAsr('麦克风采集已停止，正在转写')
      return
    }
    if (systemAsrState !== 'idle') return

    setError(null)
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('浏览器没有开放麦克风接口')
      setMicStatus('requesting')
      setMicMessage('正在请求浏览器麦克风权限')
      setSpeechStatus('starting')
      setSpeechMessage(target === 'command' ? '正在准备一句话录音' : '正在准备补充录音')
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      await setupAudioCapture(stream, {
        source: 'mic',
        target,
        message: target === 'command' ? '正在录音，说完后点停止转写' : '正在录制补充，说完后点停止补充',
      })
      setListening(true)
      setListeningTarget(target)
    } catch (err) {
      const message = err instanceof Error ? err.message : '请检查浏览器权限'
      setMicStatus('error')
      setMicMessage(message)
      setSpeechStatus('error')
      setSpeechMessage('录音未能启动')
      setError(`麦克风打开失败：${message}`)
      setListeningTarget(null)
      stopAudioMonitor()
    }
  }

  const handleSystemAsr = async () => {
    if (systemAsrState === 'recording' || systemAsrState === 'starting') {
      await finishCapturedAsr('系统音频采集已停止，正在转写')
      return
    }
    if (systemAsrState === 'stopping') return

    if (listening) {
      await finishCapturedAsr('已停止麦克风录音，正在转写')
    }

    if (!window.isSecureContext && !['localhost', '127.0.0.1'].includes(window.location.hostname)) {
      setError('浏览器要求 HTTPS 才能捕获系统音频，请使用 https 地址打开。')
      return
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setError('当前浏览器不支持系统音频捕获，请使用 Chrome 或 Edge。')
      return
    }

    setError(null)
    setNotice(null)
    setSystemAsrState('starting')
    setMicStatus('requesting')
    setMicMessage('正在请求系统音频共享权限')
    setSpeechStatus('starting')
    setSpeechMessage('正在准备系统音频录制')

    try {
      const rawStream = await navigator.mediaDevices.getDisplayMedia({
        audio: true,
        video: { width: 1, height: 1, frameRate: 1 },
      })
      rawStream.getVideoTracks().forEach((track) => track.stop())
      const audioTracks = rawStream.getAudioTracks()
      if (audioTracks.length === 0) {
        throw new Error('没有获取到系统音频，请在共享弹窗中勾选“共享音频”。')
      }

      const stream = new MediaStream(audioTracks)
      audioTracks.forEach((track) => {
        track.onended = () => void finishCapturedAsr('系统音频共享已结束，正在转写')
      })
      await setupAudioCapture(stream, {
        source: 'system',
        target: 'command',
        message: '正在录制系统音频，播放完成后点停止系统音频',
      })
      setSystemAsrState('recording')
    } catch (err) {
      closeRecordingSession('系统音频采集未启动')
      setError(err instanceof Error ? err.message : '系统音频 ASR 启动失败')
    }
  }

  const handleParse = async () => {
    const text = commandText.trim()
    if (!text) {
      setError('先说一句或输入一句代办内容。')
      return
    }

    setParsing(true)
    setError(null)
    setNotice(null)
    try {
      const result = await parseScheduleText(text)
      setDraft(result)
      setClarificationAnswer('')
      const parsedDate = dateFromISO(result.start_date)
      setSelectedDate(parsedDate)
      setFocusedMonth(parsedDate)
    } catch (err) {
      setError(parseApiError(err, '没有识别到明确的时间和事项，请补充日期或时间。'))
    } finally {
      setParsing(false)
    }
  }

  const handleDraftChange = <K extends keyof ScheduleParseResult>(
    key: K,
    value: ScheduleParseResult[K],
  ) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current))
  }

  const handleClarify = async () => {
    if (!draft) return
    const answer = clarificationAnswer.trim()
    if (!answer) {
      setError('先补一句，例如“7月25日下午三点”或“就按7月31日全天”。')
      return
    }

    setClarifying(true)
    setError(null)
    setNotice(null)
    try {
      const result = await clarifyScheduleDraft(draft, answer)
      setDraft(result)
      setClarificationAnswer('')
      const parsedDate = dateFromISO(result.start_date)
      setSelectedDate(parsedDate)
      setFocusedMonth(parsedDate)
    } catch (err) {
      setError(parseApiError(err, '没有理解这次补充，请直接填写具体日期或时间。'))
    } finally {
      setClarifying(false)
    }
  }

  const handleSave = async () => {
    if (!draft) return
    if (!draft.title.trim() || !draft.start_date) {
      setError('标题和日期不能为空。')
      return
    }

    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const saved = await createScheduleEvent({
        title: draft.title.trim(),
        event_type: draft.event_type,
        start_date: draft.start_date,
        start_time: draft.is_all_day ? null : draft.start_time,
        end_time: draft.is_all_day ? null : draft.end_time,
        is_all_day: draft.is_all_day,
        description: draft.description?.trim() || null,
        raw_text: draft.raw_text || commandText,
      })
      const parsedDate = dateFromISO(draft.start_date)
      setSelectedDate(parsedDate)
      setFocusedMonth(parsedDate)
      setCommandText('')
      setDraft(null)
      setLastSavedEvent(saved)
      setNotice(`已保存到 ${formatDisplayDate(parsedDate)} ${formatEventTime(saved)}`)
      await refreshEvents(parsedDate)
    } catch (err) {
      setError(parseApiError(err, '保存失败，请稍后重试。'))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (ev: ScheduleEvent) => {
    if (!confirm(`确定删除「${ev.title}」吗？`)) return
    await deleteScheduleEvent(ev.id)
    await refreshEvents()
  }

  const handleUndoLastSave = async () => {
    if (!lastSavedEvent) return
    try {
      await deleteScheduleEvent(lastSavedEvent.id)
      setNotice(`已撤销「${lastSavedEvent.title}」`)
      const savedDate = dateFromISO(lastSavedEvent.start_date)
      await refreshEvents(savedDate)
      setLastSavedEvent(null)
    } catch (err) {
      setError(parseApiError(err, '撤销失败，请稍后重试。'))
    }
  }

  const tileContent = ({ date }: { date: Date }) => {
    const dayEvents = eventsByDate.get(formatDate(date)) || []
    if (dayEvents.length === 0) return null
    return (
      <div className="mt-1 flex justify-center gap-1">
        {dayEvents.slice(0, 3).map((ev) => (
          <span key={ev.id} className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
        ))}
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#f7fbff] text-slate-900">
      <header className="border-b border-sky-100 bg-white/90 px-5 py-4 shadow-sm backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="rounded-full border border-slate-200 bg-white p-2 text-slate-600 transition hover:border-sky-300 hover:text-sky-700"
              title="返回首页"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <div>
              <h1 className="text-2xl font-semibold tracking-normal text-slate-950">老记</h1>
              <p className="mt-1 text-sm text-slate-500">一句话代办 · 自动识别时间 · 写入日历</p>
            </div>
          </div>
          <button
            onClick={() => {
              setCommandText(EXAMPLE_COMMAND)
              setDraft(null)
              setError(null)
            }}
            className="hidden rounded-full border border-sky-200 bg-sky-50 px-4 py-2 text-sm font-medium text-sky-700 transition hover:bg-sky-100 sm:inline-flex"
          >
            填入示例
          </button>
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_380px] lg:p-6">
        <section className="space-y-5">
          <div className="rounded-2xl border border-sky-100 bg-white p-5 shadow-sm">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">一句话创建</h2>
                <p className="mt-1 text-sm text-slate-500">说出事项、时间和补充说明，老记会先整理成可确认的日程。</p>
              </div>
              <div className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                {systemAsrState === 'recording' || systemAsrState === 'stopping'
                  ? systemAsrState === 'stopping'
                    ? '系统音频转写中'
                    : '系统音频录制中'
                  : listening
                  ? listeningTarget === 'clarification'
                    ? '正在录制补充'
                    : '正在录制代办'
                  : '麦克风可用'}
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-[1fr_auto]">
              <textarea
                value={commandText}
                onChange={(event) => {
                  setCommandText(event.target.value)
                  setError(null)
                }}
                rows={4}
                placeholder={EXAMPLE_COMMAND}
                className="min-h-[120px] resize-none rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-base leading-7 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-sky-400 focus:bg-white focus:ring-4 focus:ring-sky-100"
              />
              <div className="flex gap-3 md:w-44 md:flex-col">
                <button
                  onClick={() => void handleListen('command')}
                  disabled={systemAsrState === 'recording' || systemAsrState === 'starting' || systemAsrState === 'stopping'}
                  className={`flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-white shadow-sm transition ${
                    listening && listeningTarget === 'command'
                      ? 'bg-rose-500 hover:bg-rose-600'
                      : 'bg-sky-600 hover:bg-sky-700'
                  }`}
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18a6 6 0 006-6m-12 0a6 6 0 006 6m0 0v4m-4 0h8m-4-4a3 3 0 003-3V6a3 3 0 10-6 0v9a3 3 0 003 3z" />
                  </svg>
                  {listening && listeningTarget === 'command' ? '停止转写' : '录音'}
                </button>
                <button
                  onClick={() => void handleSystemAsr()}
                  disabled={listening}
                  className={`flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-white shadow-sm transition disabled:cursor-not-allowed disabled:opacity-60 ${
                    systemAsrState === 'recording' || systemAsrState === 'starting' || systemAsrState === 'stopping'
                      ? 'bg-rose-500 hover:bg-rose-600'
                      : 'bg-violet-600 hover:bg-violet-700'
                  }`}
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 9v6m4-9v12m4-16v20m4-14v12m4-9v6" />
                  </svg>
                  {systemAsrState === 'recording'
                    ? '停止系统音频'
                    : systemAsrState === 'stopping'
                    ? '收尾中'
                    : systemAsrState === 'starting'
                    ? '连接中'
                    : '系统音频'}
                </button>
                <button
                  onClick={handleParse}
                  disabled={parsing || systemAsrState === 'starting' || systemAsrState === 'stopping'}
                  className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {parsing ? (
                    <span className="h-4 w-4 rounded-full border-2 border-white/80 border-t-transparent animate-spin" />
                  ) : (
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                  )}
                  {parsing ? '整理中' : '生成'}
                </button>
              </div>
            </div>

            <div
              className={`mt-4 overflow-hidden rounded-2xl border transition ${
                listening || systemAsrState === 'recording' || systemAsrState === 'stopping'
                  ? 'border-sky-200 bg-sky-50/70 shadow-inner'
                  : 'border-slate-200 bg-slate-50'
              }`}
            >
              <div className="flex items-center justify-between gap-3 border-b border-white/70 px-4 py-2.5">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                  <span
                    className={`h-2.5 w-2.5 rounded-full ${
                      listening || systemAsrState === 'recording' || systemAsrState === 'stopping' ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300'
                    }`}
                  />
                  {systemAsrState === 'recording'
                    ? '正在接收系统音频'
                    : systemAsrState === 'stopping'
                    ? '正在等待 ASR 最终结果'
                    : listening
                    ? listeningTarget === 'clarification'
                      ? '正在接收追问补充'
                      : '正在接收一句话代办'
                    : '麦克风波形'}
                </div>
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span>音量</span>
                  <div className="h-2 w-24 overflow-hidden rounded-full bg-white ring-1 ring-slate-200">
                    <div
                      className="h-full rounded-full bg-emerald-500 transition-all duration-100"
                      style={{ width: `${audioLevel}%` }}
                    />
                  </div>
                </div>
              </div>
              <canvas
                ref={waveformCanvasRef}
                className="block h-24 w-full"
                aria-label="麦克风实时音频波形"
              />
              <div className="grid gap-2 border-t border-white/70 px-4 py-3 text-xs text-slate-600 md:grid-cols-2">
                <div className="flex items-start gap-2">
                  <span
                    className={`mt-1 h-2 w-2 rounded-full ${
                      micStatus === 'connected'
                        ? 'bg-emerald-500'
                        : micStatus === 'requesting'
                        ? 'bg-sky-500 animate-pulse'
                        : micStatus === 'silent'
                        ? 'bg-amber-500'
                        : micStatus === 'error'
                        ? 'bg-rose-500'
                        : 'bg-slate-300'
                    }`}
                  />
                  <span>麦克风：{micMessage}</span>
                </div>
                <div className="flex items-start gap-2">
                  <span
                    className={`mt-1 h-2 w-2 rounded-full ${
                      speechStatus === 'listening'
                        ? 'bg-emerald-500'
                        : speechStatus === 'starting'
                        ? 'bg-sky-500 animate-pulse'
                        : speechStatus === 'unsupported'
                        ? 'bg-amber-500'
                        : speechStatus === 'error'
                        ? 'bg-rose-500'
                        : 'bg-slate-300'
                    }`}
                  />
                  <span>转写：{speechMessage}</span>
                </div>
              </div>
            </div>

            {listening && (
              <div className="mt-3 flex items-center gap-2 text-sm text-rose-600">
                <span className="h-2 w-2 rounded-full bg-rose-500 animate-pulse" />
                {listeningTarget === 'clarification'
                  ? '正在录制补充回答，讲完后点“停止补充”。'
                  : '正在录音，讲完后点“停止转写”。'}
              </div>
            )}
            {(systemAsrState === 'recording' || systemAsrState === 'stopping') && (
              <div className="mt-3 flex items-center gap-2 text-sm text-violet-700">
                <span className="h-2 w-2 rounded-full bg-violet-500 animate-pulse" />
                {systemAsrState === 'recording'
                  ? '正在录制系统音频，播放测试音频后点“停止系统音频”进行转写。'
                  : '已停止采集，正在把系统音频转成文字。'}
              </div>
            )}
            {notice && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <span>{notice}</span>
                {lastSavedEvent && (
                  <button
                    onClick={() => void handleUndoLastSave()}
                    className="rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200 transition hover:bg-emerald-100"
                  >
                    撤销
                  </button>
                )}
              </div>
            )}
            {error && (
              <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                {error}
              </div>
            )}
          </div>

          {draft && (
            <div className="rounded-2xl border border-emerald-100 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-slate-950">确认日程</h2>
                  <p className="mt-1 text-sm text-slate-500">识别结果可以直接修改，补充描述后再保存。</p>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-semibold ${
                      draft.parse_source === 'local_llm'
                        ? 'bg-violet-50 text-violet-700'
                        : 'bg-sky-50 text-sky-700'
                    }`}
                  >
                    {getParseSourceLabel(draft.parse_source)}
                  </span>
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-semibold ${
                      draft.needs_clarification
                        ? 'bg-amber-50 text-amber-700'
                        : 'bg-emerald-50 text-emerald-700'
                    }`}
                  >
                    {draft.needs_clarification ? '需确认' : `置信度 ${Math.round(draft.confidence * 100)}%`}
                  </span>
                </div>
              </div>

              {draft.needs_clarification && draft.clarification_question && (
                <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  <p>{draft.clarification_question}</p>
                  <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto_auto]">
                    <input
                      value={clarificationAnswer}
                      onChange={(event) => {
                        setClarificationAnswer(event.target.value)
                        setError(null)
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') void handleClarify()
                      }}
                      placeholder="例如：7月25日下午三点 / 就按7月31日全天"
                      className="w-full rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-amber-400 focus:ring-4 focus:ring-amber-100"
                    />
                    <button
                      onClick={() => void handleListen('clarification')}
                      className={`rounded-lg px-4 py-2 text-sm font-semibold text-white transition ${
                        listening && listeningTarget === 'clarification'
                          ? 'bg-rose-500 hover:bg-rose-600'
                          : 'bg-sky-600 hover:bg-sky-700'
                      }`}
                    >
                      {listening && listeningTarget === 'clarification' ? '停止补充' : '语音补充'}
                    </button>
                    <button
                      onClick={handleClarify}
                      disabled={clarifying || (listening && listeningTarget === 'clarification')}
                      className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {clarifying ? '应用中...' : '应用补充'}
                    </button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    {['7月25日下午三点', '就按7月31日全天', '明天上午九点'].map((sample) => (
                      <button
                        key={sample}
                        onClick={() => setClarificationAnswer(sample)}
                        className="rounded-full bg-white px-2.5 py-1 text-amber-700 ring-1 ring-amber-200 transition hover:bg-amber-100"
                      >
                        {sample}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid gap-4 md:grid-cols-2">
                <label className="space-y-1.5">
                  <span className="text-sm font-medium text-slate-600">标题</span>
                  <input
                    value={draft.title}
                    onChange={(event) => handleDraftChange('title', event.target.value)}
                    className="w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-sky-400 focus:ring-4 focus:ring-sky-100"
                  />
                </label>
                <label className="space-y-1.5">
                  <span className="text-sm font-medium text-slate-600">重复</span>
                  <select
                    value={draft.event_type}
                    onChange={(event) => handleDraftChange('event_type', event.target.value)}
                    className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-sky-400 focus:ring-4 focus:ring-sky-100"
                  >
                    {Object.entries(EVENT_TYPE_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1.5">
                  <span className="text-sm font-medium text-slate-600">日期</span>
                  <input
                    type="date"
                    value={draft.start_date}
                    onChange={(event) => handleDraftChange('start_date', event.target.value)}
                    className="w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-sky-400 focus:ring-4 focus:ring-sky-100"
                  />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1.5">
                    <span className="text-sm font-medium text-slate-600">开始</span>
                    <input
                      type="time"
                      value={draft.start_time || ''}
                      disabled={draft.is_all_day}
                      onChange={(event) => handleDraftChange('start_time', event.target.value || null)}
                      className="w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-sky-400 focus:ring-4 focus:ring-sky-100 disabled:bg-slate-100"
                    />
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-sm font-medium text-slate-600">结束</span>
                    <input
                      type="time"
                      value={draft.end_time || ''}
                      disabled={draft.is_all_day}
                      onChange={(event) => handleDraftChange('end_time', event.target.value || null)}
                      className="w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-sky-400 focus:ring-4 focus:ring-sky-100 disabled:bg-slate-100"
                    />
                  </label>
                </div>
                <label className="flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={draft.is_all_day}
                    onChange={(event) => handleDraftChange('is_all_day', event.target.checked)}
                    className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
                  />
                  全天事项
                </label>
                <label className="space-y-1.5 md:col-span-2">
                  <span className="text-sm font-medium text-slate-600">补充描述</span>
                  <textarea
                    value={draft.description || ''}
                    onChange={(event) => handleDraftChange('description', event.target.value || null)}
                    rows={3}
                    placeholder="例如：带上方案，重点确认预算、硬件参数、交付时间。"
                    className="w-full resize-none rounded-xl border border-slate-200 px-3 py-2.5 text-sm leading-6 outline-none focus:border-sky-400 focus:ring-4 focus:ring-sky-100"
                  />
                </label>
              </div>

              {draftConflicts.length > 0 && (
                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  <p className="font-medium">这个时间附近已有安排</p>
                  <div className="mt-2 space-y-1">
                    {draftConflicts.slice(0, 3).map((event) => (
                      <p key={`${event.id}-${event.start_date}`}>
                        {formatEventTime(event)} · {event.title}
                      </p>
                    ))}
                  </div>
                  <p className="mt-2 text-xs text-amber-700">可以继续保存，也可以用上面的补充输入改一个时间。</p>
                </div>
              )}

              <div className="mt-4 flex flex-wrap justify-end gap-3">
                <button
                  onClick={() => setDraft(null)}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="rounded-xl bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {saving ? '保存中...' : '保存到日历'}
                </button>
              </div>
            </div>
          )}

          <div className="rounded-2xl border border-sky-100 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">{formatDisplayDate(selectedDate)}</h2>
                <p className="mt-1 text-sm text-slate-500">
                  {selectedEvents.length === 0 ? '这一天还没有安排' : `${selectedEvents.length} 个安排`}
                </p>
              </div>
            </div>

            {loading ? (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500">
                <span className="h-4 w-4 rounded-full border-2 border-sky-500 border-t-transparent animate-spin" />
                正在加载日程
              </div>
            ) : selectedEvents.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 py-12 text-center text-sm text-slate-500">
                说一句“老记老记”，把下一件事放进来。
              </div>
            ) : (
              <div className="space-y-3">
                {selectedEvents.map((ev) => (
                  <article
                    key={ev.id}
                    className="group flex gap-3 rounded-xl border border-slate-100 bg-slate-50 p-4 transition hover:border-sky-200 hover:bg-sky-50/50"
                  >
                    <div className="mt-1 h-10 w-1 rounded-full bg-emerald-500" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-semibold text-slate-950">{ev.title}</h3>
                        <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-sky-700 ring-1 ring-sky-100">
                          {formatEventTime(ev)}
                        </span>
                        {ev.event_type !== 'once' && (
                          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                            {EVENT_TYPE_LABELS[ev.event_type] || ev.event_type}
                          </span>
                        )}
                      </div>
                      {ev.description && <p className="mt-2 text-sm leading-6 text-slate-600">{ev.description}</p>}
                      {ev.raw_text && <p className="mt-2 text-xs text-slate-400">原话：{ev.raw_text}</p>}
                    </div>
                    <button
                      onClick={() => handleDelete(ev)}
                      className="h-9 w-9 rounded-full text-slate-300 opacity-100 transition hover:bg-white hover:text-rose-500 md:opacity-0 md:group-hover:opacity-100"
                      title="删除"
                    >
                      <svg className="mx-auto h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>

        <aside className="space-y-5">
          <div className="rounded-2xl border border-sky-100 bg-white p-4 shadow-sm">
            <Calendar
              calendarType="gregory"
              className="laoji-calendar"
              locale="zh-CN"
              next2Label={null}
              prev2Label={null}
              value={selectedDate}
              formatDay={(_locale: string | undefined, date: Date) => String(date.getDate())}
              formatLongDate={(_locale: string | undefined, date: Date) =>
                `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
              }
              formatMonth={(_locale: string | undefined, date: Date) => `${date.getMonth() + 1}月`}
              onChange={(value) => {
                if (value instanceof Date) setSelectedDate(value)
              }}
              onActiveStartDateChange={({ activeStartDate }) => {
                if (activeStartDate) setFocusedMonth(activeStartDate)
              }}
              tileContent={tileContent}
            />
          </div>

          <div className="rounded-2xl border border-amber-100 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
            <p className="font-semibold">可这样说</p>
            <p className="mt-2">“明天下午三点提醒我发项目周报”</p>
            <p>“下周三下午两点和周总开会，要讨论硬件相关问题”</p>
            <p>“每周一上午十点团队例会”</p>
          </div>
        </aside>
      </main>
    </div>
  )
}
