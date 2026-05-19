import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { getMeeting } from '../api/meetings'
import { getFinalSummary } from '../api/summary'
import { getTranscripts } from '../api/transcript'
import FinalSummaryView from '../components/summary/FinalSummaryView'
import ActionItemList from '../components/summary/ActionItemList'
import TranscriptItem from '../components/transcript/TranscriptItem'
import type { TranscriptSegment } from '../types/meeting'

export default function MeetingSummaryPage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const { currentMeeting, setCurrentMeeting, clearCurrent, finalSummary, setFinalSummary } = useMeetingStore()
  const [transcripts, setTranscripts] = useState<TranscriptSegment[]>([])
  const [showTranscripts, setShowTranscripts] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!meetingId) return

    // 切换会议时，先清空之前的数据
    clearCurrent()
    setLoading(true)

    Promise.all([
      getMeeting(meetingId),
      getFinalSummary(meetingId),
      getTranscripts(meetingId, 0, 1000),
    ]).then(([meeting, summary, transcriptResp]) => {
      setCurrentMeeting(meeting)
      setFinalSummary(summary)
      setTranscripts(transcriptResp.items)
      setLoading(false)
    })
  }, [meetingId, setCurrentMeeting, setFinalSummary, clearCurrent])

  if (loading) {
    return <div className="text-center text-gray-500 py-16">加载中...</div>
  }

  return (
    <div className="max-w-4xl mx-auto">
      {/* 顶部导航 */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <button
            onClick={() => navigate('/')}
            className="text-sm text-gray-400 hover:text-white mb-1"
          >
            ← 返回会议列表
          </button>
          <h1 className="text-2xl font-bold">{currentMeeting?.title || '会议总结'}</h1>
        </div>
      </div>

      {/* 总结内容 - 白底卡片风格 */}
      <div className="bg-white text-gray-900 rounded-xl p-8 mb-6">
        {finalSummary ? (
          <FinalSummaryView summary={finalSummary} />
        ) : (
          <div className="text-center text-gray-500 py-12">暂无总结数据</div>
        )}
      </div>

      {/* Action Items */}
      {finalSummary && finalSummary.action_items.length > 0 && (
        <div className="bg-white text-gray-900 rounded-xl p-8 mb-6">
          <h2 className="text-xl font-bold mb-4">待办事项</h2>
          <ActionItemList items={finalSummary.action_items} />
        </div>
      )}

      {/* 完整转写（可折叠） */}
      <div className="glass rounded-xl overflow-hidden">
        <button
          onClick={() => setShowTranscripts(!showTranscripts)}
          className="w-full px-6 py-4 flex items-center justify-between hover:bg-dark-100 transition-colors"
        >
          <h2 className="font-semibold">完整转写记录</h2>
          <span className="text-sm text-gray-400">
            {showTranscripts ? '收起' : '展开'} · {transcripts.length} 条
          </span>
        </button>
        {showTranscripts && (
          <div className="px-6 pb-6 space-y-2 max-h-96 overflow-y-auto">
            {transcripts.map((t) => (
              <TranscriptItem key={t.id} item={t} />
            ))}
            {transcripts.length === 0 && (
              <div className="text-center text-gray-500 py-8">暂无转写记录</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
