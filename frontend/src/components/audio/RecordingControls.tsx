/**
 * 会议录音控制器
 * 对齐 InsightEye 模式二音频管线:
 *   MediaStream → AudioContext → createScriptProcessor → downsample to 16kHz → PCM 16bit base64
 *
 * 使用 createScriptProcessor（对齐 InsightEye），在 onaudioprocess 中直接处理音频数据。
 */

import { useState, useRef, useEffect } from 'react'
import { useAudioStore, type AudioSource } from '../../stores/audioStore'
import { MeetingWebSocket } from '../../api/websocket'

interface Props {
  meetingId: string
  ws: MeetingWebSocket | null
  onWsReady?: (ws: MeetingWebSocket) => void
}

const OUTPUT_SAMPLE_RATE = 16000

// 诊断函数：列出可用的音频设备
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

export default function RecordingControls({ meetingId, ws, onWsReady }: Props) {
  const { isRecording, isPaused, audioSource, setRecording, setPaused, setAudioSource, setVolume } = useAudioStore()

  const micStreamRef = useRef<MediaStream | null>(null)
  const systemStreamRef = useRef<MediaStream | null>(null)
  const activeWsRef = useRef<MeetingWebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const [sourceLabel, setSourceLabel] = useState('麦克风')
  const [audioLevel, setAudioLevel] = useState(0)  // 本地显示用
  const cleanupRef = useRef<(() => void) | null>(null)
  // 保存 processor/analyser 节点用于清理
  const nodesRef = useRef<{ source?: MediaStreamAudioSourceNode; processor?: ScriptProcessorNode; analyser?: AnalyserNode }>({})
  // 暂停标记 ref，避免闭包延迟问题
  const pausedRef = useRef(false)
  // 是否已暂停（控制按钮文字）
  const [localPaused, setLocalPaused] = useState(false)

  useEffect(() => {
    if (ws) {
      activeWsRef.current = ws
      console.log('[RecordingControls] ws 已设置，readyState=', ws.readyState)
      onWsReady?.(ws)
    } else {
      console.log('[RecordingControls] ws prop 为 null')
    }
  }, [ws, onWsReady])

  useEffect(() => {
    const labels: Record<AudioSource, string> = { mic: '麦克风', system: '系统音频' }
    setSourceLabel(labels[audioSource])
  }, [audioSource])

  // 对齐 InsightEye 的下采样函数
  const downsampleBuffer = (buffer: Float32Array, inputRate: number, outputRate: number): Float32Array => {
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

  // 对齐 InsightEye 的 floatTo16BitPCM + bytesToBase64
  const bytesToBase64 = (bytes: Uint8Array): string => {
    let binary = ''
    const chunkSize = 0x8000
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
    }
    return btoa(binary)
  }

  const floatTo16BitPCM = (floatBuffer: Float32Array): Uint8Array => {
    const pcm = new Int16Array(floatBuffer.length)
    for (let i = 0; i < floatBuffer.length; i++) {
      const sample = Math.max(-1, Math.min(1, floatBuffer[i]))
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
    }
    return new Uint8Array(pcm.buffer)
  }

  // 对齐 InsightEye 的 getRequestedAudioStreams：等 WebSocket OPEN 后才采集
  const handleStart = async () => {
    // 获取最新的 audioSource 状态（不从闭包获取）
    const currentAudioSource = useAudioStore.getState().audioSource
    console.log('[Recording] 开始录制，当前 audioSource:', currentAudioSource)

    try {
      // 先获取麦克风权限（不等待 WebSocket），让用户能立即看到权限弹窗
      const combinedStream = new MediaStream()

      // 只有在选择麦克风时才检查/请求麦克风权限
      if (currentAudioSource === 'mic') {
        // 先检查麦克风权限状态
        let permissionState: PermissionState = 'prompt'
        try {
          const result = await navigator.permissions.query({ name: 'microphone' as PermissionName })
          permissionState = result.state
          console.log('[Recording] 麦克风权限状态:', result.state)
          if (result.state === 'denied') {
            alert('麦克风权限已被拒绝。\n\n请在浏览器地址栏左侧点击🔒或🔒图标，\n将"麦克风"权限改为"允许"，然后刷新页面重试。')
            return
          }
          if (result.state === 'prompt') {
            // 权限需要用户授权，会自动弹出提示
          }
        } catch (permErr) {
          console.warn('[Recording] 无法查询权限状态，继续尝试获取:', permErr)
        }

        try {
          const constraints: MediaStreamConstraints = {
            audio: {
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            },
          }
          console.log('[Recording] 请求麦克风约束:', JSON.stringify(constraints.audio))
          const micStream = await navigator.mediaDevices.getUserMedia(constraints)
          micStream.getAudioTracks().forEach((t) => combinedStream.addTrack(t))
          micStreamRef.current = micStream
          console.log('[Recording] 麦克风已获取，轨道数:', micStream.getAudioTracks().length)
        } catch (micErr: unknown) {
          const err = micErr as Error & { name?: string; message?: string }
          const errName = err?.name || 'UnknownError'
          const errMsg = err?.message || ''
          console.error('[Recording] 麦克风获取失败:', errName, errMsg)

          let title = '麦克风获取失败'
          let hint = ''
          if (errName === 'NotAllowedError' || errName === 'PermissionDeniedError') {
            hint = '\n\n解决方法：\n1. 检查浏览器地址栏左侧的麦克风/摄像头图标，点击"允许"\n2. 或者点击地址栏左侧的🔒图标 → 权限 → 麦克风 → 设为"允许"\n3. 刷新页面后重试'
          } else if (errName === 'NotFoundError') {
            hint = '\n\n解决方法：\n1. 确认系统已连接麦克风（可在系统设置中测试）\n2. 检查是否有其他程序正在使用麦克风\n3. 如果是浏览器标签页正在使用，关闭该标签页'
          } else if (errName === 'NotReadableError' || errName === 'DeviceInUseError') {
            hint = '\n\n解决方法：\n麦克风正被其他程序占用。请关闭占用麦克风的程序后重试：\n- 视频通话软件（微信、QQ、Zoom等）\n- 其他使用麦克风的浏览器标签页\n- 录音软件'
          } else if (errName === 'OverconstrainedError') {
            hint = '\n\n解决方法：\n当前浏览器不支持这些音频参数。正在尝试简化参数重试...'
            try {
              const micStreamSimple = await navigator.mediaDevices.getUserMedia({ audio: true })
              micStreamSimple.getAudioTracks().forEach((t) => combinedStream.addTrack(t))
              micStreamRef.current = micStreamSimple
              console.log('[Recording] 简化参数麦克风获取成功，轨道数:', micStreamSimple.getAudioTracks().length)
            } catch {
              alert(title + hint)
              return
            }
            return
          } else if (errName === 'NotSupportedError') {
            hint = '\n\n解决方法：\n请使用支持的浏览器：Chrome、Edge、Firefox、Safari'
          } else if (errName === 'SecurityError') {
            hint = '\n\n解决方法：\n麦克风功能需要在安全上下文（HTTPS）或 localhost 下运行\n如果您使用的是 HTTP，请切换到 HTTPS 或使用 localhost'
          } else {
            hint = `\n\n技术信息: ${errName}\n${errMsg}`
          }
          alert(title + hint)
          return
        }
      } else if (currentAudioSource === 'system') {
        try {
          const displayStream = await navigator.mediaDevices.getDisplayMedia({ audio: true })
          const audioTracks = displayStream.getAudioTracks()
          if (!audioTracks.length) throw new Error('No system audio track was shared.')
          const systemStream = new MediaStream(audioTracks)
          systemStreamRef.current = systemStream
          audioTracks.forEach((t) => combinedStream.addTrack(t))
          console.log('[Recording] 系统音频已获取，轨道数:', audioTracks.length, '| 设备:', audioTracks[0].label)
        } catch (sysErr: unknown) {
          const err = sysErr as Error & { name?: string }
          if (err?.name === 'NotAllowedError') alert('系统音频权限被拒绝，请在弹窗中选择标签页/窗口并开启"分享音频"选项。')
          else if (err?.name === 'NotFoundError') alert('未找到音频设备，请确保选择了标签页（而非整个屏幕）并开启音频选项。')
          else alert(`系统音频获取失败: ${err?.message || err}`)
          if (micStreamRef.current) { micStreamRef.current.getAudioTracks().forEach((t) => t.stop()); micStreamRef.current = null }
          return
        }
      }

      if (combinedStream.getAudioTracks().length === 0) {
        alert('没有可用的音频轨道，请检查麦克风权限。')
        return
      }

      // 获取权限后再检查 WebSocket 状态
      const currentWs = activeWsRef.current
      if (!currentWs) {
        // 释放已获取的麦克风
        combinedStream.getAudioTracks().forEach((t) => t.stop())
        alert('WebSocket 尚未连接，请稍等...')
        return
      }
      if (currentWs.readyState !== WebSocket.OPEN) {
        combinedStream.getAudioTracks().forEach((t) => t.stop())
        alert(`WebSocket 尚未就绪 (状态=${currentWs.readyState})，请等待连接建立后再试。`)
        return
      }

      console.log('[Recording] WebSocket 已就绪 (OPEN)，开始采集音频...')

      // 对齐 InsightEye：使用 AudioContext（不过度约束），确保 resumed 状态
      let audioContext = audioContextRef.current
      if (!audioContext) audioContext = new AudioContext()
      else if (audioContext.state === 'suspended') await audioContext.resume()
      audioContextRef.current = audioContext
      console.log('[Recording] AudioContext 状态:', audioContext.state, '采样率:', audioContext.sampleRate)

      // 对齐 InsightEye：createMediaStreamSource → createAnalyser → createScriptProcessor
      const sourceNode = audioContext.createMediaStreamSource(combinedStream)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 2048
      sourceNode.connect(analyser)

      const processor = audioContext.createScriptProcessor(512, 1, 1)

      let chunkNum = 0
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0)

        // 实时计算能量，更新音量电平（暂停时也显示静音量表）
        let sum = 0
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i]
        const energy = sum / input.length
        const level = Math.min(1, Math.sqrt(energy) * 5)
        setAudioLevel(level)
        setVolume(level)

        // 暂停时不再发送音频
        if (pausedRef.current) return

        chunkNum++
        const downsampled = downsampleBuffer(input, audioContext.sampleRate, OUTPUT_SAMPLE_RATE)
        if (!downsampled.length) return

        // 对齐 InsightEye：downsampled → PCM 16bit → base64 → WebSocket
        const pcmBytes = floatTo16BitPCM(downsampled)
        const base64 = bytesToBase64(pcmBytes)

        const wsWrapper = activeWsRef.current
        if (!wsWrapper) return

        // 使用 currentAudioSource 而不是闭包中的 audioSource
        wsWrapper.sendAudioChunk(currentAudioSource, base64)
      }

      // 对齐 InsightEye：source → analyser → processor → destination
      sourceNode.connect(processor)
      processor.connect(audioContext.destination)

      // 保存节点引用用于清理
      nodesRef.current = { source: sourceNode, processor, analyser }

      cleanupRef.current = () => {
        console.log('[Recording] 清理音频管线...')
        try {
          sourceNode.disconnect()
          analyser.disconnect()
          processor.disconnect()
          processor.onaudioprocess = null
        } catch (_) {}
        if (audioContext.state !== 'closed') audioContext.close()
        audioContextRef.current = null
        nodesRef.current = {}
        setAudioLevel(0)
      }

      if (!activeWsRef.current) console.warn('[Recording] WebSocket 未就绪，音频将无法发送')

      setRecording(true)
      setPaused(false)
    } catch (err) {
      const error = err as Error
      console.error('[Recording] 无法访问音频设备:', error.name, error.message)
      alert(`无法访问音频设备 (${error.name}): ${error.message}`)
    }
  }

  const handlePause = () => {
    const nextPaused = !pausedRef.current
    pausedRef.current = nextPaused
    setLocalPaused(nextPaused)
    setPaused(nextPaused)
    console.log(`[Recording] ${nextPaused ? '暂停' : '继续'}`)
  }

  const handleStop = () => {
    console.log('[Recording] 停止音频处理')
    pausedRef.current = false
    setLocalPaused(false)
    if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null }
    if (micStreamRef.current) { micStreamRef.current.getAudioTracks().forEach((t) => t.stop()); micStreamRef.current = null }
    if (systemStreamRef.current) { systemStreamRef.current.getAudioTracks().forEach((t) => t.stop()); systemStreamRef.current = null }
    setRecording(false)
    setPaused(false)
  }

  const levelWidth = Math.round(audioLevel * 100)

  return (
    <div className="flex items-center gap-3">
      {!isRecording && (
        <div className={`flex items-center gap-1 bg-dark-200 rounded-full p-1 ${!ws ? 'opacity-50 pointer-events-none' : ''}`}>
          <button
            onClick={() => {
              console.log('[RecordingControls] 点击了麦克风按钮, 当前 audioSource:', useAudioStore.getState().audioSource)
              setAudioSource('mic')
            }}
            className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
              audioSource === 'mic' ? 'bg-primary-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            麦克风
          </button>
          <button
            onClick={() => {
              console.log('[RecordingControls] 点击了系统音频按钮, 当前 audioSource:', useAudioStore.getState().audioSource)
              setAudioSource('system')
            }}
            className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
              audioSource === 'system' ? 'bg-primary-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            系统音频
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

      {isRecording && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-400">采集: {sourceLabel}</span>
          <div className="flex items-center gap-1">
            <div className="w-24 h-2 bg-dark-300 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-75 ${
                  audioLevel > 0.5 ? 'bg-red-500' : audioLevel > 0.05 ? 'bg-green-500' : 'bg-gray-500'
                }`}
                style={{ width: `${levelWidth}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {!isRecording ? (
        <button
          onClick={handleStart}
          className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-red-600 hover:bg-red-700 transition-colors"
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
