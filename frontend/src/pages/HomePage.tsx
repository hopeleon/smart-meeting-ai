import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { listMeetings, createMeeting } from '../api/meetings'
import type { MeetingCreate } from '../types/meeting'

export default function HomePage() {
  const navigate = useNavigate()
  const { meetings, setMeetings, clearCurrent } = useMeetingStore()
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState<MeetingCreate>({ title: '', description: '' })

  useEffect(() => {
    // 进入首页时清空之前的数据
    clearCurrent()
    listMeetings().then((res) => setMeetings(res.items))
  }, [setMeetings, clearCurrent])

  const handleCreate = async () => {
    if (!form.title.trim()) return
    const meeting = await createMeeting(form)
    setMeetings([meeting, ...meetings])
    setShowCreate(false)
    setForm({ title: '', description: '' })
    navigate(`/meeting/${meeting.id}`)
  }

  const handleCardClick = (meeting: typeof meetings[0]) => {
    if (meeting.status === 'ended') {
      navigate(`/meeting/${meeting.id}/summary`)
    } else {
      navigate(`/meeting/${meeting.id}`)
    }
  }

  const handleDelete = async (e: React.MouseEvent, meetingId: string) => {
    e.stopPropagation()
    if (!confirm('确定要删除该会议吗？')) return
    await import('../api/meetings').then(m => m.deleteMeeting(meetingId))
    setMeetings(meetings.filter(m => m.id !== meetingId))
  }

  const statusLabel: Record<string, string> = {
    created: '未开始',
    recording: '录制中',
    paused: '已暂停',
    ended: '已结束',
  }

  const statusColor: Record<string, string> = {
    created: 'bg-gray-500',
    recording: 'bg-red-500',
    paused: 'bg-yellow-500',
    ended: 'bg-green-600',
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-bold">会议列表</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-primary rounded-lg hover:bg-blue-600 transition-colors"
        >
          新建会议
        </button>
      </div>

      {showCreate && (
        <div className="glass rounded-xl p-6 mb-6">
          <h2 className="text-lg font-semibold mb-4">创建新会议</h2>
          <input
            type="text"
            placeholder="会议标题"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            className="w-full px-4 py-2 bg-dark-100 border border-dark-200 rounded-lg mb-3 focus:outline-none focus:border-primary"
          />
          <input
            type="text"
            placeholder="会议描述（可选）"
            value={form.description || ''}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            className="w-full px-4 py-2 bg-dark-100 border border-dark-200 rounded-lg mb-4 focus:outline-none focus:border-primary"
          />
          <div className="flex gap-3">
            <button
              onClick={handleCreate}
              className="px-4 py-2 bg-primary rounded-lg hover:bg-blue-600 transition-colors"
            >
              创建
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 bg-dark-200 rounded-lg hover:bg-dark-300 transition-colors"
            >
              取消
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {meetings.map((meeting) => (
          <div
            key={meeting.id}
            onClick={() => handleCardClick(meeting)}
            className={`glass rounded-xl p-5 cursor-pointer transition-colors ${
              meeting.status === 'ended'
                ? 'hover:border-green-500/50 opacity-90 hover:opacity-100'
                : 'hover:border-primary/50'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-semibold">{meeting.title}</h3>
              <div className="flex items-center gap-2">
                <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[meeting.status]}`}>
                  {statusLabel[meeting.status]}
                </span>
                {meeting.status === 'ended' && (
                  <button
                    onClick={(e) => handleDelete(e, meeting.id)}
                    className="p-1 text-gray-400 hover:text-red-400 transition-colors"
                    title="删除会议"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
            {meeting.description && (
              <p className="text-sm text-gray-400 mb-2">{meeting.description}</p>
            )}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4 text-xs text-gray-500">
                <span>{meeting.participants.length} 位参与者</span>
                <span>{new Date(meeting.created_at).toLocaleString('zh-CN')}</span>
              </div>
              {meeting.status === 'ended' && (
                <span className="text-xs text-green-500 font-medium">查看总结 →</span>
              )}
            </div>
          </div>
        ))}

        {meetings.length === 0 && (
          <div className="text-center text-gray-500 py-16">暂无会议，点击"新建会议"开始</div>
        )}
      </div>
    </div>
  )
}
