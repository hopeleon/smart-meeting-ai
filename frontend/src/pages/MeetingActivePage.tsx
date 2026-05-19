import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { useAudioStore } from '../stores/audioStore'
import { getMeeting, updateMeetingStatus } from '../api/meetings'
import { MeetingWebSocket } from '../api/websocket'
import TranscriptList from '../components/transcript/TranscriptList'
import PeriodSummaryCard from '../components/summary/PeriodSummaryCard'
import RecordingControls from '../components/audio/RecordingControls'
import WaveformVisualizer from '../components/audio/WaveformVisualizer'
import type { WebSocketMessage, TranscriptSegment } from '../types/meeting'

/** 同时兼容两种格式：data wrapper 和 flat（对齐 InsightEye） */
function extractTranscriptSegment(msg: WebSocketMessage): TranscriptSegment | null {
  const m = msg as Record<string, unknown>
  // 优先用 data wrapper
  if (m.data && typeof m.data === 'object') {
    return m.data as TranscriptSegment
  }
  // flat 格式：字段在顶层
  if ('speaker_id' in m || 'text' in m) {
    return {
      id: (m.id as string) || crypto.randomUUID(),
      meeting_id: m.meeting_id as string | undefined,
      speaker_id: (m.speaker_id as string) || 'unknown',
      speaker_label: (m.speaker_label as string) || (m.speaker_id as string) || 'unknown',
      text: (m.text as string) || '',
      start_time: m.start_time as number | undefined,
      end_time: m.end_time as number | undefined,
      start_ms: m.start_ms as number | undefined,
      end_ms: m.end_ms as number | undefined,
      is_final: m.is_final as boolean | undefined,
      confidence: (m.confidence as number) || (m.speaker_confidence as number) || 1,
      segment_reason: m.segment_reason as string | undefined,
      speaker_confidence: m.speaker_confidence as number | undefined,
      speaker_name: m.speaker_name as string | undefined,
      speaker_candidates: m.speaker_candidates as TranscriptSegment['speaker_candidates'],
      registered_speaker_sims: m.registered_speaker_sims as TranscriptSegment['registered_speaker_sims'],
      recognized_role: m.recognized_role as string | undefined,
      interviewer_sim: m.interviewer_sim as number | undefined,
      candidate_sim: m.candidate_sim as number | undefined,
      uncertain_speaker: m.uncertain_speaker as boolean | undefined,
      speaker_uncertain_reason: m.speaker_uncertain_reason as string | undefined,
      was_corrected: m.was_corrected as boolean | undefined,
      corrected_text: m.corrected_text as string | undefined,
      created_at: (m.created_at as string) || new Date().toISOString(),
    }
  }
  return null
}

export default function MeetingActivePage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const {
    currentMeeting,
    setCurrentMeeting,
    clearCurrent,
    transcripts,
    setTranscripts,
    setPeriodSummaries,
    addTranscript,
    periodSummaries,
    addPeriodSummary,
  } = useMeetingStore()
  const { isRecording, reset: resetAudio } = useAudioStore()
  const wsRef = useRef<MeetingWebSocket | null>(null)
  const [wsReady, setWsReady] = useState(false)

  useEffect(() => {
    if (!meetingId) return

    // 切换会议时，先清空之前的数据
    clearCurrent()
    setTranscripts([])
    setPeriodSummaries([])

    getMeeting(meetingId).then((meeting) => {
      setCurrentMeeting(meeting)
      if (meeting.status === 'ended') {
        navigate(`/meeting/${meetingId}/summary`)
        return
      }

      const ws = new MeetingWebSocket(meetingId)
      wsRef.current = ws

      ws.onMessage((msg: WebSocketMessage) => {
        const m = msg as Record<string, unknown>
        console.log('[WS-收到]', msg.type, JSON.stringify(m).slice(0, 200))

        if (msg.type === 'transcript.completed' || msg.type === 'transcript.delta' || msg.type === 'transcript') {
          const segment = extractTranscriptSegment(msg)
          if (segment) addTranscript(segment)
        } else if (msg.type === 'period_summary' || msg.type === 'period_summary_update') {
          addPeriodSummary((m.data || msg) as Parameters<typeof addPeriodSummary>[0])
        } else if (msg.type === 'meeting_status') {
          const status = (m.status as string) || ((m.data as Record<string, unknown>)?.status as string)
          console.log('[会议状态]', status)
        } else if (msg.type === 'speaker_update' || msg.type === 'speaker.identified') {
          console.log('[说话人更新]', m.data || m)
        } else if (msg.type === 'session.ready') {
          console.log('[会话就绪]', m.data || m)
        } else if (msg.type === 'pong') {
          console.log('[心跳回复] pong')
        } else if (msg.type === 'heartbeat') {
          // 后端心跳，不处理
        }
      })

      ws.onStatusChange((connected) => {
        setWsReady(connected)
        if (!connected) {
          console.warn('[WS] 连接已断开，正在重连...')
        }
      })

      ws.onopen = () => {
        console.log('[WS-页面] 连接就绪')
      }

      ws.connect()

      return () => {
        ws.disconnect()
        wsRef.current = null
        setWsReady(false)
      }
    })
  }, [meetingId, setCurrentMeeting, addTranscript, addPeriodSummary])

  // 检测到后端断开（非重连成功），弹窗提示并跳转
  useEffect(() => {
    if (!wsReady && wsRef.current !== null) {
      const timer = setTimeout(() => {
        // 确认仍然是断开的（不是重连中）
        if (!wsRef.current) return
        alert('服务器连接已断开，会议结束。')
        resetAudio()
        navigate('/')
      }, 2000)
      return () => clearTimeout(timer)
    }
  }, [wsReady, navigate, resetAudio])

  const handleEndMeeting = async () => {
    if (!meetingId) return
    wsRef.current?.endMeeting()
    await updateMeetingStatus(meetingId, 'ended')
    wsRef.current?.disconnect()
    navigate(`/meeting/${meetingId}/summary`)
  }

  if (!currentMeeting) {
    return <div className="text-center text-gray-500 py-16">加载中...</div>
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* 顶部信息栏 */}
      <div className="flex items-center justify-between px-4 py-3 glass rounded-xl mb-4">
        <div>
          <h1 className="text-lg font-semibold">{currentMeeting.title}</h1>
          <span className="text-xs text-gray-400">
            {currentMeeting.participants.join('、')}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {isRecording && (
            <span className="flex items-center gap-1.5 text-red-400 text-sm">
              <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
              录制中
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
        {/* 左侧：实时转写 */}
        <div className="w-1/3 glass rounded-xl flex flex-col overflow-hidden">
          <div className="px-4 py-3 border-b border-white/10">
            <h2 className="font-semibold text-sm">实时转写</h2>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <TranscriptList items={transcripts} />
          </div>
        </div>

        {/* 右侧：阶段总结 */}
        <div className="w-2/3 glass rounded-xl flex flex-col overflow-hidden">
          <div className="px-4 py-3 border-b border-white/10">
            <h2 className="font-semibold text-sm">阶段总结</h2>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {periodSummaries.length === 0 ? (
              <div className="text-center text-gray-500 py-12">
                暂无阶段总结，录制开始后每2分钟自动生成
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
      <div className="mt-4 glass rounded-xl px-6 py-4 flex items-center gap-6">
        {/* WebSocket 连接状态指示器 */}
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${wsReady ? 'bg-green-400 animate-pulse' : 'bg-yellow-400'}`} />
          <span className="text-xs text-gray-400">
            {wsReady ? '已连接' : '连接中...'}
          </span>
        </div>
        <RecordingControls meetingId={meetingId!} ws={wsRef.current} />
        <WaveformVisualizer />
      </div>
    </div>
  )
}
