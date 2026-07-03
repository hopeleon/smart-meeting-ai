import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { getMeeting, getMeetingStatus } from '../api/meetings'
import { getFinalSummary, getPeriodSummaries, downloadSummaryDocument } from '../api/summary'
import { getTranscripts, downloadTranscriptDocument } from '../api/transcript'
import PeriodSummaryCard from '../components/summary/PeriodSummaryCard'
import TranscriptItem from '../components/transcript/TranscriptItem'
import AudioUploader from '../components/audio/AudioUploader'
import type { Meeting, TranscriptSegment } from '../types/meeting'

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
  const [showTranscripts, setShowTranscripts] = useState(true)
  const [showUploader, setShowUploader] = useState(false)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [isMeetingLoading, setIsMeetingLoading] = useState(true)
  const [processingStatus, setProcessingStatus] = useState('')

  const meetingStatus = currentMeeting?.status
  const isProcessing = meetingStatus === 'processing' || meetingStatus === 'recording' || meetingStatus === 'paused'
  const isCompleted = meetingStatus === 'ended' || meetingStatus === 'completed'
  const canUpload = meetingStatus === 'created' || meetingStatus === 'failed' || (!meetingStatus && !isMeetingLoading)
  const shouldShowUploader = showUploader || canUpload

  const saveBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }

  const handleDownloadSummary = async () => {
    if (!meetingId) return
    try {
      const { blob, filename } = await downloadSummaryDocument(meetingId)
      saveBlob(blob, filename)
    } catch (error) {
      console.error(error)
      alert('下载总结文档失败')
    }
  }

  const handleDownloadTranscript = async () => {
    if (!meetingId) return
    try {
      const { blob, filename } = await downloadTranscriptDocument(meetingId)
      saveBlob(blob, filename)
    } catch (error) {
      console.error(error)
      alert('下载转写文档失败')
    }
  }

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
    setShowUploader(false)
    setSummaryLoading(true)
    setIsMeetingLoading(true)
    setProcessingStatus('')
    setFinalSummary(null)

    async function pollMeetingState() {
      if (!meetingId) return

      const meeting = await refreshMeetingData(meetingId)
      if (!meeting) return

      // 会议仍在进行中 → 根据 mode 跳转到对应页面
      if (meeting.status === 'created' || meeting.status === 'recording' || meeting.status === 'processing') {
        if (meeting.mode === 'offline') {
          navigate(`/meeting/${meetingId}/offline`)
        } else {
          navigate(`/meeting/${meetingId}/active`)
        }
        return
      }

      if (meeting.status === 'paused') {
        if (meeting.mode === 'offline') {
          navigate(`/meeting/${meetingId}/offline`)
        } else {
          navigate(`/meeting/${meetingId}/active`)
        }
        return
      }

      if (meeting.status === 'ended' || meeting.status === 'completed') {
        setProcessingStatus('')
      }

      if (meeting.status === 'processing' || meeting.status === 'recording' || meeting.status === 'paused') {
        try {
          const status = await getMeetingStatus(meetingId)
          setProcessingStatus(status.status)
        } catch {
          setProcessingStatus(meeting.status)
        }
      } else {
        setProcessingStatus('')
      }

      try {
        const summary = await getFinalSummary(meetingId)
        setFinalSummary(summary)
        if (summary) {
          setSummaryLoading(false)
        }
      } catch {
        // 总结还没生成，继续轮询
      }
    }

    pollMeetingState()
    const timer = setInterval(pollMeetingState, 5000)

    return () => {
      clearInterval(timer)
    }
  }, [meetingId, clearCurrent, setCurrentMeeting, setFinalSummary, setPeriodSummaries])

  return (
    <div className="max-w-4xl mx-auto">
      {isCompleted && !finalSummary && summaryLoading && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/75 backdrop-blur-sm">
          <div className="w-14 h-14 border-4 border-primary-500 border-t-transparent rounded-full animate-spin mb-5" />
          <div className="text-xl font-semibold text-white">正在生成会议总结…</div>
          <div className="text-sm text-gray-300 mt-2">这可能需要一会儿，可以离开，稍后回来仍会继续等待</div>
        </div>
      )}
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
        <div className="flex items-center gap-3">
          {!isCompleted && (
            <button
              onClick={() => navigate(`/meeting/${meetingId}/active`)}
              className="px-4 py-2 bg-green-600 hover:bg-green-700 rounded-lg text-sm font-medium flex items-center gap-2"
            >
              <span className="w-2 h-2 bg-green-300 rounded-full animate-pulse" />
              进入实时转录
            </button>
          )}
          <button
            onClick={handleDownloadTranscript}
            disabled={!meetingId || transcripts.length === 0}
            className="px-4 py-2 bg-dark-200 hover:bg-dark-300 rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            下载转写文档
          </button>
          <button
            onClick={handleDownloadSummary}
            disabled={!meetingId || (!finalSummary && !isCompleted)}
            className="px-4 py-2 bg-dark-200 hover:bg-dark-300 rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            下载总结文档
          </button>
          {(canUpload || shouldShowUploader) && (
            <button
              onClick={() => setShowUploader(!showUploader)}
              className="px-4 py-2 bg-primary-600 hover:bg-primary-700 rounded-lg text-sm"
            >
              {showUploader ? '收起' : '+ 处理音频'}
            </button>
          )}
        </div>
      </div>

      {isMeetingLoading && (
        <div className="mb-6 rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500 shadow-sm">
          正在加载会议状态...
        </div>
      )}

      {!isMeetingLoading && isProcessing && (
        <div className="mb-6 rounded-xl border border-slate-200 bg-white p-6 text-slate-900 shadow-sm">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-5 h-5 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
            <h2 className="text-lg font-semibold text-slate-950">音频处理中</h2>
          </div>
          <p className="text-sm text-slate-600">
            当前会议正在服务器端处理中。刷新页面后会自动恢复这个状态，不会再回到初始提交界面。
          </p>
          <p className="mt-3 text-sm text-sky-700">
            当前状态：{processingStatus || meetingStatus || 'processing'}
          </p>
        </div>
      )}

      {!isMeetingLoading && shouldShowUploader && !isProcessing && (
        <div className="mb-6 rounded-xl border border-slate-200 bg-white p-6 text-slate-900 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-slate-950">音频处理</h2>
          <p className="mb-4 text-sm text-slate-600">
            上传音频文件进行转写和说话人识别。
          </p>
          {meetingStatus === 'failed' && (
            <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              上一次处理失败了。你可以重新上传音频再次处理。
            </div>
          )}
          <AudioUploader
            meetingId={meetingId!}
            onProcessingComplete={async () => {
              setShowUploader(false)
              setSummaryLoading(true)
              setProcessingStatus('completed')
              if (meetingId) {
                await refreshMeetingData(meetingId)
                try {
                  const summary = await getFinalSummary(meetingId)
                  setFinalSummary(summary)
                  if (summary) {
                    setSummaryLoading(false)
                  }
                } catch {
                  // 交给轮询继续拿最终总结
                }
              }
            }}
            onError={(err) => alert('处理失败: ' + err)}
          />
        </div>
      )}

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

      {(summaryLoading || finalSummary || isCompleted) && (
        <div className="mb-6 overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm">
          <div className="border-b border-slate-200 px-6 py-4">
            <h2 className="font-semibold text-slate-950">最终总结</h2>
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
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      关键决策
                    </h3>
                    <ul className="space-y-2">
                      {finalSummary.key_decisions!.map((decision, index) => (
                        <li key={`${index}-${decision}`} className="flex gap-2">
                          <span className="mt-[2px] text-sky-600">•</span>
                          <span className="whitespace-pre-wrap text-slate-700">{decision}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {(finalSummary.action_items?.length ?? 0) > 0 && (
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      待办事项
                    </h3>
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
              <div className="text-slate-500">会议已完成，但总结文件暂未生成，请稍后刷新重试。</div>
            ) : null}
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-sm">
        <button
          onClick={() => setShowTranscripts(!showTranscripts)}
          className="flex w-full items-center justify-between px-6 py-4 transition-colors hover:bg-slate-50"
        >
          <h2 className="font-semibold text-slate-950">完整转写记录</h2>
          <span className="text-sm text-slate-500">
            {showTranscripts ? '收起' : '展开'} · {transcripts.length} 条
          </span>
        </button>
        {showTranscripts && (
          <div className="max-h-96 space-y-2 overflow-y-auto px-6 pb-6">
            {transcripts.map((t) => (
              <TranscriptItem key={t.id} item={t} />
            ))}
            {transcripts.length === 0 && (
              <div className="py-8 text-center text-slate-500">暂无转写记录</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
