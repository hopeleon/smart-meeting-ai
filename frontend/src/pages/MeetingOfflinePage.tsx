/**
 * 离线模式会议页面
 * 用于离线音频文件的处理和查看
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { getMeeting, getMeetingStatus } from '../api/meetings'
import { getFinalSummary, getPeriodSummaries } from '../api/summary'
import { getTranscripts } from '../api/transcript'
import AudioUploader from '../components/audio/AudioUploader'
import PeriodSummaryCard from '../components/summary/PeriodSummaryCard'
import TranscriptItem from '../components/transcript/TranscriptItem'
import type { Meeting, TranscriptSegment } from '../types/meeting'

export default function MeetingOfflinePage() {
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
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [isMeetingLoading, setIsMeetingLoading] = useState(true)
  const [processingStatus, setProcessingStatus] = useState('')

  const meetingStatus = currentMeeting?.status
  const isProcessing = meetingStatus === 'processing'
  const isCompleted = meetingStatus === 'ended' || meetingStatus === 'completed'

  const refreshMeetingData = async (id: string): Promise<Meeting | null> => {
    try {
      const [meeting, transcriptResp, periodResp] = await Promise.all([
        getMeeting(id),
        getTranscripts(id, 0, 1000),
        getPeriodSummaries(id),
      ])
      setCurrentMeeting(meeting)
      setTranscripts(transcriptResp.items)
      setPeriodSummaries(periodResp)
      return meeting
    } catch {
      return null
    } finally {
      setIsMeetingLoading(false)
    }
  }

  useEffect(() => {
    if (!meetingId) return

    clearCurrent()
    setTranscripts([])
    setSummaryLoading(true)
    setIsMeetingLoading(true)
    setProcessingStatus('')
    setFinalSummary(null)

    async function load() {
      if (!meetingId) return
      const meeting = await refreshMeetingData(meetingId)
      if (!meeting) return

      // 如果会议已完成，加载总结
      if (meeting.status === 'ended' || meeting.status === 'completed') {
        setProcessingStatus('')
        try {
          const summary = await getFinalSummary(meetingId)
          setFinalSummary(summary)
        } catch {
          // 继续轮询
        }
        setSummaryLoading(false)
      } else if (meeting.status === 'processing') {
        // 轮询处理状态
        try {
          const status = await getMeetingStatus(meetingId)
          setProcessingStatus(status.status)
        } catch {
          setProcessingStatus(meeting.status)
        }
      }
    }

    load()
    const timer = setInterval(load, 5000)
    return () => {
      clearInterval(timer)
    }
  }, [meetingId, clearCurrent, setCurrentMeeting, setFinalSummary, setPeriodSummaries])

  if (isMeetingLoading || !currentMeeting) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-slate-500 shadow-sm">
          加载中...
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      {/* 顶部 */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <button
            onClick={() => navigate('/')}
            className="text-sm text-gray-400 hover:text-white mb-1"
          >
            ← 返回会议列表
          </button>
          <h1 className="text-2xl font-bold">{currentMeeting.title}</h1>
          <span className="text-xs text-blue-400 mt-1">离线模式 · 批量音频处理</span>
        </div>
        <div className="flex items-center gap-3">
          <span className={`px-2 py-0.5 rounded-full text-xs ${
            isCompleted ? 'bg-green-600' : meetingStatus === 'failed' ? 'bg-red-600' : 'bg-blue-600'
          }`}>
            {isCompleted ? '已完成' : meetingStatus === 'failed' ? '失败' : meetingStatus === 'processing' ? '处理中' : '待处理'}
          </span>
          {isCompleted && (
            <button
              onClick={() => navigate(`/meeting/${meetingId}`)}
              className="px-4 py-2 bg-dark-200 hover:bg-dark-300 rounded-lg text-sm"
            >
              查看总结页面
            </button>
          )}
        </div>
      </div>

      {/* 上传区域：仅在未处理时显示 */}
      {!isCompleted && meetingStatus !== 'failed' && (
        <div className="mb-6 rounded-xl border border-slate-200 bg-white p-6 text-slate-900 shadow-sm">
          <h2 className="mb-2 text-lg font-semibold text-slate-950">上传音频文件</h2>
          <p className="mb-4 text-sm text-slate-600">
            支持 wav, mp3, m4a, ogg, webm, flac 格式，最大 500MB
          </p>
          <AudioUploader
            meetingId={meetingId!}
            onUploadStart={() => {
              setProcessingStatus('uploading')
            }}
            onProcessingComplete={async () => {
              setSummaryLoading(true)
              setProcessingStatus('completed')
              if (meetingId) {
                await refreshMeetingData(meetingId)
                try {
                  const summary = await getFinalSummary(meetingId)
                  setFinalSummary(summary)
                } catch {
                  // 继续轮询
                }
                setSummaryLoading(false)
              }
            }}
            onError={(err) => {
              setProcessingStatus('failed')
              console.error('处理失败:', err)
            }}
          />
        </div>
      )}

      {/* 处理失败提示 */}
      {meetingStatus === 'failed' && !isCompleted && (
        <div className="mb-6 rounded-xl border border-red-200 bg-white p-6 text-slate-900 shadow-sm">
          <h2 className="mb-2 text-lg font-semibold text-red-600">处理失败</h2>
          <p className="mb-4 text-sm text-slate-600">上一次处理失败了。你可以重新上传音频再次处理。</p>
          <AudioUploader
            meetingId={meetingId!}
            onUploadStart={() => setProcessingStatus('uploading')}
            onProcessingComplete={async () => {
              setProcessingStatus('completed')
              if (meetingId) await refreshMeetingData(meetingId)
            }}
            onError={(err) => {
              setProcessingStatus('failed')
              console.error('处理失败:', err)
            }}
          />
        </div>
      )}

      {/* 处理中提示 */}
      {isProcessing && (
        <div className="mb-6 rounded-xl border border-amber-200 bg-white p-6 text-slate-900 shadow-sm">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-5 h-5 border-2 border-yellow-500 border-t-transparent rounded-full animate-spin" />
            <h2 className="text-lg font-semibold text-amber-700">音频处理中</h2>
          </div>
          <p className="text-sm text-slate-600">
            当前状态：{processingStatus || meetingStatus}
          </p>
          <p className="mt-2 text-xs text-slate-500">
            刷新页面后会自动恢复状态，不会回到上传界面。
          </p>
        </div>
      )}

      {/* 阶段总结 */}
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

      {/* 最终总结 */}
      {(summaryLoading || finalSummary || isCompleted) && (
        <div className="mb-6 overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm">
          <div className="border-b border-slate-200 px-6 py-4">
            <h2 className="font-semibold text-slate-950">会议总结</h2>
          </div>
          <div className="space-y-4 px-6 py-5 text-sm leading-7 text-slate-700">
            {finalSummary ? (
              <>
                {(finalSummary.markdown || finalSummary.full_text || finalSummary.overview) && (
                  <pre className="overflow-x-auto whitespace-pre-wrap font-sans leading-7 text-slate-800">
                    {finalSummary.markdown || finalSummary.full_text || finalSummary.overview}
                  </pre>
                )}
                {(finalSummary.key_decisions?.length ?? 0) > 0 && (
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">关键决策</h3>
                    <ul className="space-y-2">
                      {finalSummary.key_decisions!.map((decision, index) => (
                        <li key={index} className="flex gap-2">
                          <span className="mt-[2px] text-sky-600">•</span>
                          <span className="whitespace-pre-wrap text-slate-700">{decision}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {(finalSummary.action_items?.length ?? 0) > 0 && (
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">待办事项</h3>
                    <ul className="space-y-2">
                      {finalSummary.action_items!.map((item) => (
                        <li key={item.id} className="flex gap-2">
                          <span className="mt-[2px] text-sky-600">•</span>
                          <span className="whitespace-pre-wrap text-slate-700">
                            {item.content}
                            {item.assignee ? `（负责人：${item.assignee}）` : ''}
                            {item.due_date ? `（截止：${item.due_date}）` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : summaryLoading ? (
              <div className="text-slate-500">AI 正在生成会议总结，请稍候...</div>
            ) : isCompleted ? (
              <div className="text-slate-500">会议已完成，但总结暂未生成。</div>
            ) : null}
          </div>
        </div>
      )}

      {/* 完整转写 */}
      {transcripts.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
            <h2 className="font-semibold text-slate-950">完整转写</h2>
            <span className="text-xs text-slate-500">{transcripts.length} 条</span>
          </div>
          <div className="max-h-96 space-y-2 overflow-y-auto px-6 pb-6 pt-2">
            {transcripts.map((t) => (
              <TranscriptItem key={t.id} item={t} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
