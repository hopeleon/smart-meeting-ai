import type { WebSocketMessage } from '../types/meeting'

const isHttps = typeof window !== 'undefined' && window.location.protocol === 'https:'
const WS_SCHEME = isHttps ? 'wss' : 'ws'
const WS_BASE = import.meta.env.VITE_WS_BASE_URL || `${WS_SCHEME}://${window.location.host}/ws`

type MessageHandler = (message: WebSocketMessage) => void
type StatusHandler = (connected: boolean) => void

export class MeetingWebSocket {
  private ws: WebSocket | null = null
  private meetingId: string
  private handlers: MessageHandler[] = []
  private statusHandlers: StatusHandler[] = []
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectAttempts = 0
  private readonly maxReconnectAttempts = 5
  private readonly reconnectDelay = 3000
  private audioCount = 0
  private transcriptCount = 0
  private _onopenHandlers: (() => void)[] = []
  private _destroyed = false

  constructor(meetingId: string) {
    this.meetingId = meetingId
  }

  connect(): void {
    if (this._destroyed) {
      console.log('[WS] 实例已销毁，跳过连接')
      return
    }

    console.log(`[WS] 连接中: ${this.meetingId} (重连次数=${this.reconnectAttempts})`)

    if (this.ws) {
      this.ws.onopen = null
      this.ws.onmessage = null
      this.ws.onclose = null
      this.ws.onerror = null
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close()
      }
      this.ws = null
    }

    this.ws = new WebSocket(`${WS_BASE}/meeting/${this.meetingId}`)

    this.ws.onopen = () => {
      console.log(`[WS] 连接已建立 (meetingId=${this.meetingId})`)
      this.reconnectAttempts = 0
      this.statusHandlers.forEach((h) => h(true))
      this._onopenHandlers.forEach((h) => h())
    }

    this.ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data)
        this.handlers.forEach((h) => h(message))
      } catch (err) {
        console.warn('[WS] 消息解析失败:', err, event.data)
      }
    }

    this.ws.onclose = (e) => {
      this.ws = null
      this.statusHandlers.forEach((h) => h(false))

      if (this._destroyed) {
        console.log('[WS] 实例已销毁，停止重连')
        return
      }

      if (e.code === 1000) {
        console.log(`[WS] 连接已关闭 (code=1000, 用户主动断开)`)
        return
      }

      console.log(`[WS] 连接异常断开 (code=${e.code})，尝试重连...`)

      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++
        const delay = Math.min(this.reconnectDelay * this.reconnectAttempts, 10000)
        console.log(`[WS] ${delay / 1000}s 后重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})`)
        this.reconnectTimer = setTimeout(() => this.connect(), delay)
      } else {
        console.error(`[WS] 重连次数已达上限 (${this.maxReconnectAttempts})，停止重连`)
      }
    }

    this.ws.onerror = (err) => {
      console.error('[WS] 连接错误:', err)
    }
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.push(handler)
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler)
    }
  }

  onStatusChange(handler: StatusHandler): () => void {
    this.statusHandlers.push(handler)
    return () => {
      this.statusHandlers = this.statusHandlers.filter((h) => h !== handler)
    }
  }

  set onopen(handler: () => void) {
    this._onopenHandlers.push(handler)
  }

  disconnect(): void {
    this._destroyed = true

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }

    if (this.ws) {
      this.ws.onopen = null
      this.ws.onmessage = null
      this.ws.onclose = null
      this.ws.onerror = null
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close(1000, '用户主动断开')
      }
      this.ws = null
    }

    this.statusHandlers.forEach((h) => h(false))
    console.log(`[WS] 已断开 (meetingId=${this.meetingId})，音频包=${this.audioCount}, 转写=${this.transcriptCount}`)
  }

  sendAudioChunk(source: string, audioBase64: string): void {
    if (this._destroyed) return

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.audioCount++
      this.ws.send(JSON.stringify({ type: 'audio_chunk', source, audio: audioBase64 }))
    } else {
      console.warn(`[WS] 无法发送音频: readyState=${this.ws?.readyState}`)
    }
  }

  /** 切换质量优先模式（通知后端调整 buffer 和 ASR 参数） */
  sendQualityMode(enabled: boolean): void {
    if (this._destroyed) return
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'quality_mode', enabled }))
      console.log(`[WS] 发送 quality_mode=${enabled}`)
    }
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CONNECTING
  }

  endMeeting(): void {
    if (this._destroyed) return
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('[WS] 发送 end_meeting')
      this.ws.send(JSON.stringify({ type: 'end_meeting' }))
    }
  }
}

/** WhisperLiveKit 专用 WebSocket 客户端
 *  - 发送二进制 PCM 16kHz 单声道音频帧（而非 base64 JSON）
 *  - 路由: /ws/meeting/{id}/whisper
 */
export class WhisperWebSocket {
  private ws: WebSocket | null = null
  private meetingId: string
  private endpoint: string
  private query: string = ''
  private handlers: ((msg: Record<string, unknown>) => void)[] = []
  private statusHandlers: StatusHandler[] = []
  private _destroyed = false

  constructor(meetingId: string, endpoint: string = 'whisper', query: string = '') {
    this.meetingId = meetingId
    this.endpoint = endpoint
    this.query = query
  }

  connect(): void {
    if (this._destroyed) {
      console.log('[WhisperWS] 实例已销毁，跳过连接')
      return
    }

    console.log(`[WhisperWS] 连接中: ${this.meetingId}`)

    if (this.ws) {
      this.ws.onopen = null
      this.ws.onmessage = null
      this.ws.onclose = null
      this.ws.onerror = null
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close()
      }
      this.ws = null
    }

    this.ws = new WebSocket(`${WS_BASE}/meeting/${this.meetingId}/${this.endpoint}${this.query}`)
    this.ws.binaryType = 'arraybuffer'

    this.ws.onopen = () => {
      console.log(`[WhisperWS] 连接已建立 (meetingId=${this.meetingId})`)
      this.statusHandlers.forEach(h => h(true))
    }

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string)
        this.handlers.forEach(h => h(msg as Record<string, unknown>))
      } catch (err) {
        console.warn('[WhisperWS] 消息解析失败:', err)
      }
    }

    this.ws.onclose = (e) => {
      this.ws = null
      this.statusHandlers.forEach(h => h(false))
      if (this._destroyed) {
        console.log('[WhisperWS] 实例已销毁，停止重连')
        return
      }
      if (e.code === 1000) {
        console.log(`[WhisperWS] 连接已关闭 (code=1000, 用户主动断开)`)
      }
    }

    this.ws.onerror = (err) => {
      console.error('[WhisperWS] 连接错误:', err)
    }
  }

  onMessage(handler: (msg: Record<string, unknown>) => void): () => void {
    this.handlers.push(handler)
    return () => { this.handlers = this.handlers.filter(h => h !== handler) }
  }

  onStatusChange(handler: StatusHandler): () => void {
    this.statusHandlers.push(handler)
    return () => { this.statusHandlers = this.statusHandlers.filter(h => h !== handler) }
  }

  /** 发送二进制音频帧（WhisperLiveKit 原生 int16 PCM 格式） */
  sendAudioFrame(frame: ArrayBuffer): void {
    if (this._destroyed) return
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(frame)
    } else {
      console.warn(`[WhisperWS] 无法发送音频: readyState=${this.ws?.readyState}`)
    }
  }

  disconnect(): void {
    this._destroyed = true
    if (this.ws) {
      // 发送空帧告知服务端结束
      try { this.ws.send(new ArrayBuffer(0)) } catch {}
      this.ws.close(1000, '用户主动断开')
      this.ws = null
    }
    this.statusHandlers.forEach(h => h(false))
    console.log(`[WhisperWS] 已断开 (meetingId=${this.meetingId})`)
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CONNECTING
  }

  endMeeting(): void {
    if (this._destroyed) return
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('[WhisperWS] 发送 end_meeting')
      // WhisperLiveKit 用空帧结束音频流，但 end_meeting 需要发送 JSON
      try { this.ws.send(new ArrayBuffer(0)) } catch {}
      this.ws.send(JSON.stringify({ type: 'end_meeting' }))
    }
  }
}

/** FunASR + Qwen 混合实时端点。
 *  - 发送二进制 PCM 16kHz 单声道 int16
 *  - 接收 FunASR transcript.completed 和 Qwen transcript.revised
 */
export class HybridWebSocket {
  private ws: WebSocket | null = null
  private meetingId: string
  private query: string
  private handlers: ((msg: Record<string, unknown>) => void)[] = []
  private statusHandlers: StatusHandler[] = []
  private _destroyed = false

  constructor(meetingId: string, query = '') {
    this.meetingId = meetingId
    this.query = query
  }

  connect(): void {
    if (this._destroyed) return
    if (this.ws) {
      this.ws.onopen = null
      this.ws.onmessage = null
      this.ws.onclose = null
      this.ws.onerror = null
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close()
      }
      this.ws = null
    }

    this.ws = new WebSocket(`${WS_BASE}/meeting/${this.meetingId}/hybrid${this.query}`)
    this.ws.binaryType = 'arraybuffer'

    this.ws.onopen = () => {
      this.statusHandlers.forEach(h => h(true))
    }

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string)
        this.handlers.forEach(h => h(msg as Record<string, unknown>))
      } catch (err) {
        console.warn('[HybridWS] 消息解析失败:', err)
      }
    }

    this.ws.onclose = () => {
      this.ws = null
      this.statusHandlers.forEach(h => h(false))
    }

    this.ws.onerror = (err) => {
      console.error('[HybridWS] 连接错误:', err)
    }
  }

  onMessage(handler: (msg: Record<string, unknown>) => void): () => void {
    this.handlers.push(handler)
    return () => { this.handlers = this.handlers.filter(h => h !== handler) }
  }

  onStatusChange(handler: StatusHandler): () => void {
    this.statusHandlers.push(handler)
    return () => { this.statusHandlers = this.statusHandlers.filter(h => h !== handler) }
  }

  sendAudioFrame(frame: ArrayBuffer): void {
    if (this._destroyed) return
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(frame)
    }
  }

  endMeeting(): void {
    if (this._destroyed) return
    if (this.ws?.readyState === WebSocket.OPEN) {
      try { this.ws.send(new ArrayBuffer(0)) } catch {}
      try { this.ws.send(JSON.stringify({ type: 'end_meeting' })) } catch {}
    }
  }

  disconnect(): void {
    this._destroyed = true
    if (this.ws) {
      try { this.ws.send(new ArrayBuffer(0)) } catch {}
      this.ws.close(1000, '用户主动断开')
      this.ws = null
    }
    this.statusHandlers.forEach(h => h(false))
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CONNECTING
  }
}
