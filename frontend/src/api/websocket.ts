import type { WebSocketMessage, TranscriptSegment, PeriodSummary } from '../types/meeting'
import { isMockMode } from './client'

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || `ws://${window.location.host}/ws`

type MessageHandler = (message: WebSocketMessage) => void

export class MeetingWebSocket {
  private ws: WebSocket | null = null
  private meetingId: string
  private handlers: MessageHandler[] = []
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private mockTimer: ReturnType<typeof setInterval> | null = null

  constructor(meetingId: string) {
    this.meetingId = meetingId
  }

  connect(): void {
    if (isMockMode()) {
      this.startMockMode()
      return
    }

    this.ws = new WebSocket(`${WS_BASE}/meeting/${this.meetingId}`)

    this.ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data)
        this.handlers.forEach((h) => h(message))
      } catch {
        // ignore parse errors
      }
    }

    this.ws.onclose = () => {
      this.reconnectTimer = setTimeout(() => this.connect(), 3000)
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.push(handler)
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler)
    }
  }

  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
    }
    if (this.mockTimer) {
      clearInterval(this.mockTimer)
    }
    this.ws?.close()
    this.ws = null
  }

  private startMockMode(): void {
    // 模拟实时转写推送
    const mockTranscripts: TranscriptSegment[] = [
      {
        id: `mock-t-${Date.now()}`,
        meeting_id: this.meetingId,
        speaker_id: 'Speaker_1',
        speaker_label: '发言人A',
        text: '大家好，今天我们讨论一下Q2的产品规划。',
        start_time: 0,
        end_time: 3.5,
        confidence: 0.95,
        created_at: new Date().toISOString(),
      },
      {
        id: `mock-t-${Date.now() + 1}`,
        meeting_id: this.meetingId,
        speaker_id: 'Speaker_2',
        speaker_label: '发言人B',
        text: '好的，我先汇报一下上个季度的完成情况。',
        start_time: 3.5,
        end_time: 7.0,
        confidence: 0.92,
        created_at: new Date().toISOString(),
      },
      {
        id: `mock-t-${Date.now() + 2}`,
        meeting_id: this.meetingId,
        speaker_id: 'Speaker_1',
        speaker_label: '发言人A',
        text: '请说，我们都在听。',
        start_time: 7.0,
        end_time: 8.5,
        confidence: 0.97,
        created_at: new Date().toISOString(),
      },
      {
        id: `mock-t-${Date.now() + 3}`,
        meeting_id: this.meetingId,
        speaker_id: 'Speaker_3',
        speaker_label: '发言人C',
        text: '我想补充一下，性能优化方面还有提升空间。',
        start_time: 8.5,
        end_time: 12.0,
        confidence: 0.91,
        created_at: new Date().toISOString(),
      },
    ]

    let index = 0
    this.mockTimer = setInterval(() => {
      if (index < mockTranscripts.length) {
        const msg: WebSocketMessage = {
          type: 'transcript',
          data: { ...mockTranscripts[index], id: `mock-${Date.now()}-${index}` },
        }
        this.handlers.forEach((h) => h(msg))
        index++
      } else {
        // 模拟阶段总结
        const summary: WebSocketMessage = {
          type: 'period_summary',
          data: {
            id: `mock-s-${Date.now()}`,
            meeting_id: this.meetingId,
            period_start: 0,
            period_end: 120,
            bullet_points: [
              '讨论了Q2产品路线图，确定了三个核心功能模块',
              '前端团队将采用React 18 + TypeScript技术栈',
              '后端使用FastAPI，预计6月底完成第一版',
            ],
            generated_at: new Date().toISOString(),
          },
        }
        this.handlers.forEach((h) => h(summary))
        if (this.mockTimer) clearInterval(this.mockTimer)
      }
    }, 2000)
  }
}
