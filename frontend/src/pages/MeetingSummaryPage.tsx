import { useEffect, useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { getMeeting } from '../api/meetings'
import { getFinalSummary, getPeriodSummaries } from '../api/summary'
import { getTranscripts } from '../api/transcript'
import FinalSummaryView from '../components/summary/FinalSummaryView'
import ActionItemList from '../components/summary/ActionItemList'
import PeriodSummaryCard from '../components/summary/PeriodSummaryCard'
import TranscriptItem from '../components/transcript/TranscriptItem'
import type { TranscriptSegment } from '../types/meeting'

export default function MeetingSummaryPage() {
  const { meetingId } = useParams<{ meetingId: string }>()
  const navigate = useNavigate()
  const {
    currentMeeting,
    setCurrentMeeting,
    clearCurrent,
    finalSummary,
    setFinalSummary,
    periodSummaries,
    setPeriodSummaries,
  } = useMeetingStore()

  const [transcripts, setTranscripts] = useState<TranscriptSegment[]>([])
  const [showTranscripts, setShowTranscripts] = useState(false)
  const [loading, setLoading] = useState(true)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [periodLoading, setPeriodLoading] = useState(true)
  const pollingDoneRef = useRef(false)

  useEffect(() => {
    if (!meetingId) return

    clearCurrent()
    setLoading(true)
    setSummaryLoading(true)
    setPeriodLoading(true)
    pollingDoneRef.current = false

    // 并行加载：会议信息、转写、阶段总结（已有数据）
    Promise.all([
      getMeeting(meetingId),
      getTranscripts(meetingId, 0, 1000),
      getPeriodSummaries(meetingId),
    ]).then(([meeting, transcriptResp, periodResp]) => {
      setCurrentMeeting(meeting)
      setTranscripts(transcriptResp.items)
      setPeriodSummaries(periodResp)
      setPeriodLoading(false)
    }).catch(() => {
      setPeriodLoading(false)
    })

    // 轮询最终总结（等待 LLM 生成）
    let attempts = 0
    const maxAttempts = 30
    const pollInterval = 5000

    function pollFinalSummary() {
      if (!meetingId || pollingDoneRef.current) return

      getFinalSummary(meetingId)
        .then((summary) => {
          if (summary) {
            setFinalSummary(summary)
            setSummaryLoading(false)
            setLoading(false)
            pollingDoneRef.current = true
          }
        })
        .catch(() => {
          // 404 或网络错误，继续轮询
        })
        .finally(() => {
          attempts++
          if (attempts >= maxAttempts && !pollingDoneRef.current) {
            setSummaryLoading(false)
            setLoading(false)
            pollingDoneRef.current = true
          }
        })
    }

    pollFinalSummary()
    const timer = setInterval(pollFinalSummary, pollInterval)

    return () => {
      clearInterval(timer)
      pollingDoneRef.current = true
    }
  }, [meetingId, setCurrentMeeting, setFinalSummary, setPeriodSummaries, clearCurrent])

  const showEmptyFinal = !loading && !summaryLoading && !finalSummary

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

      {/* 阶段总结（录制期间已有的总结卡片） */}
      {periodSummaries.length > 0 && (
        <div className="mb-6">
          <h2 className="text-lg font-semibold mb-3 text-gray-300">阶段总结</h2>
          <div className="space-y-3">
            {periodSummaries.map((s) => (
              <PeriodSummaryCard key={s.id} summary={s} />
            ))}
          </div>
        </div>
      )}

      {/* 总结内容 - 白底卡片风格 */}
      <div className="bg-white text-gray-900 rounded-xl p-8 mb-6">
        {finalSummary ? (
          <FinalSummaryView summary={finalSummary} />
        ) : summaryLoading ? (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-4 border-blue-500 border-t-transparent mb-4" />
            <p className="text-gray-500">AI 正在生成会议总结，请稍候...</p>
          </div>
        ) : (
          <div className="text-center text-gray-500 py-12">
            暂无总结数据（请确认 Ollama 服务已启动）
          </div>
        )}
      </div>

      {/* Action Items */}
      {finalSummary && finalSummary.action_items && finalSummary.action_items.length > 0 && (
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
