import type { WebSocketMessage } from '../types/meeting'

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || `ws://${window.location.host}/ws`

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
  private _destroyed = false  // 标记实例已销毁，阻止重连

  constructor(meetingId: string) {
    this.meetingId = meetingId
  }

  connect(): void {
    if (this._destroyed) {
      console.log('[WS] 实例已销毁，跳过连接')
      return
    }

    console.log(`[WS] 连接中: ${this.meetingId} (重连次数=${this.reconnectAttempts})`)

    // 清理旧连接
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
      console.log(`[WS] ✓ 连接已建立 (meetingId=${this.meetingId})`)
      this.reconnectAttempts = 0  // 重连成功，重置计数
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
        const delay = Math.min(this.reconnectDelay * this.reconnectAttempts, 10000)  // 递增延迟，最多 10s
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
    this._destroyed = true  // 标记销毁，阻止重连

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
