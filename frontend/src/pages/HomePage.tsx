import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMeetingStore } from '../stores/meetingStore'
import { listMeetings, createMeeting } from '../api/meetings'
import type { MeetingCreate } from '../types/meeting'

export default function HomePage() {
  const navigate = useNavigate()
  const { meetings, setMeetings } = useMeetingStore()
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState<MeetingCreate>({ title: '', description: '' })

  useEffect(() => {
    listMeetings().then((res) => setMeetings(res.items))
  }, [setMeetings])

  const handleCreate = async () => {
    if (!form.title.trim()) return
    const meeting = await createMeeting(form)
    setMeetings([meeting, ...meetings])
    setShowCreate(false)
    setForm({ title: '', description: '' })
    navigate(`/meeting/${meeting.id}`)
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
            onClick={() => navigate(`/meeting/${meeting.id}`)}
            className="glass rounded-xl p-5 cursor-pointer hover:border-primary/50 transition-colors"
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-semibold">{meeting.title}</h3>
              <span className={`px-2 py-0.5 rounded-full text-xs ${statusColor[meeting.status]}`}>
                {statusLabel[meeting.status]}
              </span>
            </div>
            {meeting.description && (
              <p className="text-sm text-gray-400 mb-2">{meeting.description}</p>
            )}
            <div className="flex items-center gap-4 text-xs text-gray-500">
              <span>{meeting.participants.length} 位参与者</span>
              <span>{new Date(meeting.created_at).toLocaleString('zh-CN')}</span>
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
