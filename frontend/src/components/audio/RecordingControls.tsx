/**
 * 会议录音控制器
 *
 * 音频管线（Primary - MediaRecorder）:
 *   MediaStream → MediaRecorder → audio/webm blob → queue → decode → PCM 16bit base64 → WebSocket
 *
 * 音频管线（Fallback - createScriptProcessor）:
 *   MediaStream → AudioContext → createScriptProcessor → downsample 16kHz → PCM 16bit base64 → WebSocket
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { useAudioStore, type AudioSource } from '../../stores/audioStore'
import { MeetingWebSocket } from '../../api/websocket'

interface Props {
  meetingId: string
  ws: MeetingWebSocket | null
  onWsReady?: (ws: MeetingWebSocket) => void
}

const OUTPUT_SAMPLE_RATE = 16000

// ─── 诊断 ───────────────────────────────────────────────────────────────────

export async function listAudioDevices(): Promise<void> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    const audioInputs = devices.filter(d => d.kind === 'audioinput')
    const audioOutputs = devices.filter(d => d.kind === 'audiooutput')

    console.log(`[设备诊断] 音频输入设备 (${audioInputs.length}):`)
    audioInputs.forEach((d, i) => {
      console.log(`  [${i + 1}] ${d.label || '(未命名)'} | deviceId: ${d.deviceId} | groupId: ${d.groupId}`)
    })

    console.log(`[设备诊断] 音频输出设备 (${audioOutputs.length}):`)
    audioOutputs.forEach((d, i) => {
      console.log(`  [${i + 1}] ${d.label || '(未命名)'} | deviceId: ${d.deviceId}`)
    })

    if (audioInputs.length === 0) {
      alert('未检测到任何麦克风设备。\n\n请确认：\n1. 系统已连接麦克风\n2. 浏览器已获得麦克风权限\n3. 尝试在浏览器地址栏点击麦克风图标并选择"允许"')
    }
  } catch (e) {
    console.error('[设备诊断] 枚举设备失败:', e)
  }
}

// ─── 工具函数 ────────────────────────────────────────────────────────────────

function floatTo16BitPcm(floatBuffer: Float32Array): Int16Array {
  const pcm = new Int16Array(floatBuffer.length)
  for (let i = 0; i < floatBuffer.length; i++) {
    const sample = Math.max(-1, Math.min(1, floatBuffer[i]))
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
  }
  return pcm
}

function downsampleBuffer(buffer: Float32Array, inputRate: number, outputRate: number): Float32Array {
  if (inputRate === outputRate) return buffer
  const ratio = inputRate / outputRate
  const outputLength = Math.round(buffer.length / ratio)
  const output = new Float32Array(outputLength)
  let offsetResult = 0
  let offsetBuffer = 0
  while (offsetResult < output.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio)
    let accum = 0
    let count = 0
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i]
      count += 1
    }
    output[offsetResult] = count ? accum / count : 0
    offsetResult += 1
    offsetBuffer = nextOffsetBuffer
  }
  return output
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
  }
  return btoa(binary)
}

// WebM/Opus → PCM 16kHz mono Float32Array
async function decodeWebmToPcm16k(blob: Blob, _targetSampleRate: number): Promise<Float32Array | null> {
  const arrayBuffer = await blob.arrayBuffer()
  // AudioContext 不强制 sampleRate，由浏览器使用原生采样率解码
  const audioContext = new AudioContext()
  try {
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer)
    const nativeRate = audioBuffer.sampleRate
    const channelData = audioBuffer.getChannelData(0)
    // 解码后手动下采样到 16kHz
    const downsampled = downsampleBuffer(channelData, nativeRate, OUTPUT_SAMPLE_RATE)
    return downsampled
  } catch (err) {
    // 解码失败（通常是 WebM 数据不完整），跳过此 chunk
    console.warn('[Recording] WebM 解码失败，跳过:', (err as Error).message)
    return null
  } finally {
    await audioContext.close()
  }
}

// ─── 清理函数类型 ────────────────────────────────────────────────────────────

type CleanupFn = () => void

// ─── 主组件 ─────────────────────────────────────────────────────────────────

export default function RecordingControls({ meetingId, ws, onWsReady }: Props) {
  const { isRecording, isPaused, audioSource, setRecording, setPaused, setAudioSource, setVolume } = useAudioStore()

  const activeWsRef = useRef<MeetingWebSocket | null>(null)
  const [sourceLabel, setSourceLabel] = useState('麦克风')
  const [audioLevel, setAudioLevel] = useState(0)
  const [micError, setMicError] = useState<string | null>(null)
  const [localPaused, setLocalPaused] = useState(false)

  // 录制管线状态
  const cleanupRef = useRef<CleanupFn | null>(null)
  const pausedRef = useRef(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const micStreamRef = useRef<MediaStream | null>(null)
  const chunkCountRef = useRef(0)

  useEffect(() => {
    if (ws) {
      activeWsRef.current = ws
      console.log('[RecordingControls] ws 已设置，readyState=', ws.readyState)
      onWsReady?.(ws)
    } else {
      activeWsRef.current = null
      console.log('[RecordingControls] ws prop 为 null')
    }
  }, [ws, onWsReady])

  useEffect(() => {
    const labels: Record<AudioSource, string> = { mic: '麦克风', system: '系统音频' }
    setSourceLabel(labels[audioSource])
  }, [audioSource])

  // ─── 清理所有音频资源 ───────────────────────────────────────────────────
  const cleanupAll = useCallback(() => {
    if (cleanupRef.current) {
      cleanupRef.current()
      cleanupRef.current = null
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
      mediaRecorderRef.current = null
    }
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close().catch(() => {})
      audioContextRef.current = null
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
    }
    setAudioLevel(0)
  }, [])

  // ─── 获取麦克风 MediaStream ─────────────────────────────────────────────
  const getMicStream = async (): Promise<MediaStream> => {
    // 查询权限状态
    let permissionState: PermissionState = 'prompt'
    try {
      const result = await navigator.permissions.query({ name: 'microphone' as PermissionName })
      permissionState = result.state
      console.log('[Recording] 麦克风权限状态:', result.state)
      if (result.state === 'denied') {
        alert('麦克风权限已被拒绝。\n\n请在浏览器地址栏左侧点击🔒图标，\n将"麦克风"权限改为"允许"，然后刷新页面重试。')
        throw new Error('PermissionDenied')
      }
    } catch (permErr) {
      console.warn('[Recording] 无法查询权限状态，继续尝试获取:', permErr)
    }

    // 优先：带高级约束（回退到简单约束）
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
          channelCount: { ideal: 1, max: 1 },
          sampleRate: { ideal: 16000 },
        },
      })
      console.log('[Recording] 麦克风已获取（高级约束），轨道数:', stream.getAudioTracks().length)
    } catch {
      console.log('[Recording] 高级约束失败，尝试简化约束...')
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      console.log('[Recording] 麦克风已获取（简化约束），轨道数:', stream.getAudioTracks().length)
    }

    // 请求标签（需要用户已授权）
    const track = stream.getAudioTracks()[0]
    if (track) {
      try {
        const capabilities = track.getCapabilities?.() as MediaTrackCapabilities & { sampleRate?: { max: number } }
        console.log('[Recording] 麦克风能力:', JSON.stringify(capabilities))
        const settings = track.getSettings()
        console.log('[Recording] 麦克风设置:', JSON.stringify(settings))
      } catch {
        // 某些浏览器不支持 getCapabilities
      }
    }

    return stream
  }

  // ─── Modern: MediaRecorder 管线（queue-based，无 chunk 丢失）────────────
  const startModernPipeline = async (stream: MediaStream): Promise<void> => {
    console.log('[Recording] 启动 MediaRecorder 管线...')

    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : 'audio/ogg'
    console.log('[Recording] 使用 MIME 类型:', mimeType)

    const recorder = new MediaRecorder(stream, { mimeType })
    mediaRecorderRef.current = recorder

    let wsWrapper = activeWsRef.current
    if (!wsWrapper) throw new Error('WebSocket 未就绪')

    const audioContext = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE })
    audioContextRef.current = audioContext

    const chunkQueue: Blob[] = []
    let isProcessingQueue = false

    const processQueue = async () => {
      if (isProcessingQueue || pausedRef.current || chunkQueue.length === 0) return
      isProcessingQueue = true

      while (chunkQueue.length > 0 && !pausedRef.current) {
        const blob = chunkQueue.shift()!
        try {
          const floatSamples = await decodeWebmToPcm16k(blob, OUTPUT_SAMPLE_RATE)
          if (!floatSamples || floatSamples.length === 0) continue

          let sum = 0
          for (let i = 0; i < floatSamples.length; i++) sum += floatSamples[i] * floatSamples[i]
          const energy = sum / floatSamples.length
          const level = Math.min(1, Math.sqrt(energy) * 5)
          setAudioLevel(level)
          setVolume(level)

          const pcm = floatTo16BitPcm(floatSamples)
          const base64 = bytesToBase64(new Uint8Array(pcm.buffer))
          chunkCountRef.current++
          wsWrapper?.sendAudioChunk('mic', base64)
          if (chunkCountRef.current <= 5) {
            console.log(`[Recording] 发送 chunk #${chunkCountRef.current}, 大小=${base64.length}`)
          }
        } catch (err) {
          console.warn('[Recording] 处理 chunk 失败:', err)
        }
      }

      isProcessingQueue = false
      if (chunkQueue.length > 0) {
        setTimeout(processQueue, 50)
      }
    }

    // MediaRecorder 每次 ondataavailable 触发（~1秒一个 chunk）
    recorder.ondataavailable = async (event) => {
      if (!event.data || event.data.size === 0) return
      chunkQueue.push(event.data)
      processQueue()
    }

    recorder.start(1000) // 每 1 秒触发一次 ondataavailable
    console.log('[Recording] MediaRecorder 已启动，state=', recorder.state)

    cleanupRef.current = () => {
      console.log('[Recording] 清理 MediaRecorder 管线...')
      chunkQueue.length = 0
      recorder.ondataavailable = null
      if (recorder.state !== 'inactive') recorder.stop()
      mediaRecorderRef.current = null
      if (audioContext.state !== 'closed') audioContext.close()
      audioContextRef.current = null
      stream.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
      setAudioLevel(0)
    }
  }

  // ─── Legacy: createScriptProcessor 管线（备用）──────────────────────────
  const startLegacyPipeline = async (stream: MediaStream): Promise<void> => {
    console.log('[Recording] 启动 createScriptProcessor 管线（备用）...')

    let audioContext = audioContextRef.current
    if (!audioContext) audioContext = new AudioContext()
    else if (audioContext.state === 'suspended') await audioContext.resume()
    audioContextRef.current = audioContext

    const sourceNode = audioContext.createMediaStreamSource(stream)
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 2048
    sourceNode.connect(analyser)

    const processor = audioContext.createScriptProcessor(512, 1, 1)

    processor.onaudioprocess = (event) => {
      if (pausedRef.current) return

      const input = event.inputBuffer.getChannelData(0)

      let sum = 0
      for (let i = 0; i < input.length; i++) sum += input[i] * input[i]
      const energy = sum / input.length
      const level = Math.min(1, Math.sqrt(energy) * 5)
      setAudioLevel(level)
      setVolume(level)

      const downsampled = downsampleBuffer(input, audioContext.sampleRate, OUTPUT_SAMPLE_RATE)
      if (!downsampled.length) return

      const pcm = floatTo16BitPcm(downsampled)
      const base64 = bytesToBase64(new Uint8Array(pcm.buffer))

      chunkCountRef.current++
      activeWsRef.current?.sendAudioChunk('mic', base64)
      if (chunkCountRef.current <= 5) {
        console.log(`[Recording] 发送 chunk #${chunkCountRef.current}, 大小=${base64.length}`)
      }
    }

    sourceNode.connect(processor)
    processor.connect(audioContext.destination)

    cleanupRef.current = () => {
      console.log('[Recording] 清理 createScriptProcessor 管线...')
      try { sourceNode.disconnect(); analyser.disconnect(); processor.disconnect() } catch (_) {}
      processor.onaudioprocess = null
      if (audioContext.state !== 'closed') audioContext.close()
      audioContextRef.current = null
      stream.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
      setAudioLevel(0)
    }
  }

  // ─── 开始录制 ────────────────────────────────────────────────────────────
  const handleStart = async () => {
    setMicError(null)
    const currentAudioSource = useAudioStore.getState().audioSource
    console.log('[Recording] 开始录制，audioSource:', currentAudioSource)

    try {
      // ── 1. 获取麦克风 ──────────────────────────────────────────────
      let stream: MediaStream
      if (currentAudioSource === 'mic') {
        try {
          stream = await getMicStream()
          micStreamRef.current = stream
        } catch (err) {
          const e = err as Error
          if (e.message === 'PermissionDenied') return
          handleMicError(err as Error)
          return
        }
      } else {
        // 系统音频
        try {
          stream = await navigator.mediaDevices.getDisplayMedia({ audio: true }) as MediaStream
          const audioTracks = stream.getAudioTracks()
          if (!audioTracks.length) throw new Error('No system audio track was shared.')
          console.log('[Recording] 系统音频已获取，轨道数:', audioTracks.length, '| 设备:', audioTracks[0].label)
        } catch (sysErr) {
          const err = sysErr as Error & { name?: string }
          if (err?.name === 'NotAllowedError') {
            alert('系统音频权限被拒绝，请在弹窗中选择标签页/窗口并开启"分享音频"选项。')
          } else if (err?.name === 'NotFoundError') {
            alert('未找到音频设备，请确保选择了标签页（而非整个屏幕）并开启音频选项。')
          } else {
            alert(`系统音频获取失败: ${err?.message || err}`)
          }
          return
        }
      }

      if (stream.getAudioTracks().length === 0) {
        setMicError('没有可用的音频轨道，请检查麦克风权限。')
        return
      }

      // ── 2. 检查 WebSocket ─────────────────────────────────────────
      const currentWs = activeWsRef.current
      if (!currentWs) {
        stream.getTracks().forEach(t => t.stop())
        setMicError('WebSocket 尚未连接，请稍等...')
        return
      }
      if (currentWs.readyState !== WebSocket.OPEN) {
        stream.getTracks().forEach(t => t.stop())
        setMicError(`WebSocket 尚未就绪 (状态=${currentWs.readyState})，请等待连接建立后再试。`)
        return
      }

      console.log('[Recording] WebSocket 已就绪 (OPEN)，开始采集音频...')

      // ── 3. 尝试 MediaRecorder，不支持则降级到 createScriptProcessor ──
      if (typeof MediaRecorder !== 'undefined') {
        try {
          await startModernPipeline(stream)
          console.log('[Recording] 使用 MediaRecorder 管线')
        } catch (mrErr) {
          console.warn('[Recording] MediaRecorder 失败，降级到 createScriptProcessor:', mrErr)
          cleanupAll()
          try {
            await startLegacyPipeline(stream)
          } catch (legacyErr) {
            handleMicError(legacyErr as Error)
            return
          }
        }
      } else {
        // MediaRecorder 不可用，直接用 createScriptProcessor
        await startLegacyPipeline(stream)
      }

      setRecording(true)
      setPaused(false)
      setMicError(null)
    } catch (err) {
      handleMicError(err as Error)
    }
  }

  // ─── 麦克风错误处理 ──────────────────────────────────────────────────────
  const handleMicError = (err: Error) => {
    const errName = err?.name || 'UnknownError'
    const errMsg = err?.message || ''
    console.error('[Recording] 麦克风获取失败:', errName, errMsg)

    let hint = ''
    if (errName === 'NotAllowedError' || errName === 'PermissionDeniedError') {
      hint = '麦克风权限被拒绝。请在地址栏左侧点击🔒图标 → 权限 → 麦克风 → 设为"允许"，然后刷新。'
    } else if (errName === 'NotFoundError') {
      hint = '未找到麦克风设备。请确认系统已连接麦克风，且没有其他程序正在使用。'
    } else if (errName === 'NotReadableError' || errName === 'DeviceInUseError') {
      hint = '麦克风正被其他程序占用（微信、QQ、Zoom等）。请关闭占用程序后重试。'
    } else if (errName === 'NotSupportedError') {
      hint = '当前浏览器不支持麦克风录音。请使用 Chrome、Edge、Firefox 或 Safari。'
    } else if (errName === 'SecurityError') {
      hint = '麦克风功能需要在安全上下文（HTTPS 或 localhost）下运行。'
    } else if (errName === 'WebSocket 未就绪') {
      hint = errMsg
    } else {
      hint = `${errName}: ${errMsg}`
    }
    setMicError(hint)
  }

  // ─── 暂停 ────────────────────────────────────────────────────────────────
  const handlePause = () => {
    const nextPaused = !pausedRef.current
    pausedRef.current = nextPaused
    setLocalPaused(nextPaused)
    setPaused(nextPaused)
    setAudioLevel(0)
    console.log(`[Recording] ${nextPaused ? '暂停' : '继续'}`)
  }

  // ─── 停止 ────────────────────────────────────────────────────────────────
  const handleStop = () => {
    console.log('[Recording] 停止音频处理')
    pausedRef.current = false
    setLocalPaused(false)
    chunkCountRef.current = 0
    cleanupAll()
    setRecording(false)
    setPaused(false)
    setMicError(null)
  }

  const levelWidth = Math.round(audioLevel * 100)
  const [isAcquiringMic, setIsAcquiringMic] = useState(false)

  // ─── 预检查麦克风权限（不启动录制，仅获取权限）───────────────────────
  const handlePreCheckMic = async () => {
    setMicError(null)
    setIsAcquiringMic(true)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(t => t.stop())
      alert('麦克风正常！可以开始录制。')
    } catch (err) {
      handleMicError(err as Error)
    } finally {
      setIsAcquiringMic(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      {/* 错误提示（醒目展示） */}
      {micError && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-1.5 max-w-sm">
          <span className="text-red-400 text-sm font-medium">⚠</span>
          <span className="text-red-300 text-xs leading-relaxed">{micError}</span>
          <button
            onClick={() => setMicError(null)}
            className="text-red-400 hover:text-red-300 text-xs ml-1"
            title="关闭"
          >
            ×
          </button>
        </div>
      )}

      {/* 音频源选择（录制时隐藏） */}
      {!isRecording && (
        <div className={`flex items-center gap-1 bg-dark-200 rounded-full p-1 ${!ws ? 'opacity-50 pointer-events-none' : ''}`}>
          <button
            onClick={() => setAudioSource('mic')}
            className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
              audioSource === 'mic' ? 'bg-primary-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            麦克风
          </button>
          <button
            onClick={() => setAudioSource('system')}
            className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
              audioSource === 'system' ? 'bg-primary-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            系统音频
          </button>
          <button
            onClick={handlePreCheckMic}
            disabled={isAcquiringMic}
            title="先测试麦克风是否可用"
            className="px-3 py-1.5 rounded-full text-sm text-gray-400 hover:text-white hover:bg-dark-300 transition-colors disabled:opacity-50"
          >
            {isAcquiringMic ? '检查中...' : '测试麦克风'}
          </button>
          <button
            onClick={listAudioDevices}
            title="诊断音频设备"
            className="px-3 py-1.5 rounded-full text-sm text-gray-400 hover:text-white hover:bg-dark-300 transition-colors"
          >
            诊断
          </button>
        </div>
      )}

      {/* 音量电平 + 管线信息（录制时显示） */}
      {isRecording && (
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">采集: {sourceLabel}</span>
          <div className="w-24 h-2 bg-dark-300 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-75 ${
                audioLevel > 0.5 ? 'bg-red-500' : audioLevel > 0.05 ? 'bg-green-500' : 'bg-gray-500'
              }`}
              style={{ width: `${levelWidth}%` }}
            />
          </div>
          <span className="text-xs text-gray-600 font-mono">
            #{chunkCountRef.current}
          </span>
        </div>
      )}

      {/* 控制按钮 */}
      {!isRecording ? (
        <button
          onClick={handleStart}
          disabled={!ws || isAcquiringMic}
          title={!ws ? '等待 WebSocket 连接...' : '开始录制'}
          className={`flex items-center gap-2 px-5 py-2.5 rounded-full bg-red-600 hover:bg-red-700 transition-colors ${!ws || isAcquiringMic ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          <span className="w-3 h-3 bg-white rounded-full" />
          <span className="text-sm font-medium">开始录制</span>
        </button>
      ) : (
        <>
          <button
            onClick={handlePause}
            className="flex items-center gap-2 px-4 py-2.5 bg-dark-200 rounded-full hover:bg-dark-300 transition-colors"
          >
            {localPaused ? (
              <><span className="w-3 h-3 bg-white rounded-sm" /><span className="text-sm">继续</span></>
            ) : (
              <><span className="flex gap-0.5"><span className="w-1 h-3 bg-white rounded-sm" /><span className="w-1 h-3 bg-white rounded-sm" /></span><span className="text-sm">暂停</span></>
            )}
          </button>
          <button
            onClick={handleStop}
            className="flex items-center gap-2 px-4 py-2.5 bg-red-600 rounded-full hover:bg-red-700 transition-colors"
          >
            <span className="w-3 h-3 bg-white rounded-sm" />
            <span className="text-sm">停止</span>
          </button>
        </>
      )}
    </div>
  )
}
