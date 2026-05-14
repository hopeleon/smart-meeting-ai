import { useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { useAudioStore } from '../stores/audioStore'
import { getMeeting, updateMeetingStatus } from '../api/meetings'
import { MeetingWebSocket } from '../api/websocket'
import TranscriptList from '../components/transcript/TranscriptList'
import PeriodSummaryCard from '../components/summary/PeriodSummaryCard'
import RecordingControls from '../components/audio/RecordingControls'
import WaveformVisualizer from '../components/audio/WaveformVisualizer'
import type { WebSocketMessage } from '../types/meeting'

export default function MeetingActivePage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const {
    currentMeeting,
    setCurrentMeeting,
    transcripts,
    addTranscript,
    periodSummaries,
    addPeriodSummary,
  } = useMeetingStore()
  const { isRecording } = useAudioStore()
  const wsRef = useRef<MeetingWebSocket | null>(null)

  useEffect(() => {
    if (!meetingId) return

    getMeeting(meetingId).then(setCurrentMeeting)

    const ws = new MeetingWebSocket(meetingId)
    wsRef.current = ws

    ws.onMessage((msg: WebSocketMessage) => {
      if (msg.type === 'transcript') {
        addTranscript(msg.data)
      } else if (msg.type === 'period_summary') {
        addPeriodSummary(msg.data)
      }
    })

    ws.connect()

    return () => {
      ws.disconnect()
      wsRef.current = null
    }
  }, [meetingId, setCurrentMeeting, addTranscript, addPeriodSummary])

  const handleEndMeeting = async () => {
    if (!meetingId) return
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
        <RecordingControls meetingId={meetingId!} />
        <WaveformVisualizer />
      </div>
    </div>
  )
}
