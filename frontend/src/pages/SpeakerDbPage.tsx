/**
 * 声纹数据库管理页面
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  listSpeakers,
  searchSpeakers,
  registerSpeaker,
  deleteSpeaker,
  getSpeakerStats,
  type SpeakerProfile,
  type SpeakerStats,
  type SpeakerSimilarityPair,
} from '../api/speakers'

// 角色选项
const ROLE_OPTIONS = [
  { value: '', label: '请选择' },
  { value: '工程师', label: '工程师' },
  { value: '产品经理', label: '产品经理' },
  { value: '设计师', label: '设计师' },
  { value: '运营', label: '运营' },
  { value: 'HR', label: 'HR' },
  { value: '访客', label: '访客' },
  { value: '其他', label: '其他' },
]

// 注册提示文本
const REGISTRATION_TEXTS = [
  '我是张三，我的声音独一无二',
  '今天天气真不错',
  '智能会议让工作更高效',
  '声纹识别技术真的很神奇',
  '欢迎使用智能会议系统',
]

// 推荐录音时长范围（秒）
const MIN_RECORDING_SECONDS = 3
const MAX_RECORDING_SECONDS = 10

// 声纹注册质量要求
const QUALITY_REQUIREMENTS = {
  MIN_SAMPLES: 2,           // 最少样本数量
  MAX_SAMPLES: 5,            // 最多样本数量
  MIN_SAMPLE_DURATION: 3,   // 单个样本最短时长（秒）
  MAX_SAMPLE_DURATION: 10,  // 单个样本最长时长（秒）
  MIN_TOTAL_DURATION: 6,    // 最短总录音时长（秒）
  MIN_QUALITY_SCORE: 0.4,   // 最低质量分数
}

// 质量等级描述
const QUALITY_LEVELS = [
  { min: 0.8, label: '优秀', color: 'text-green-600', bg: 'bg-green-100' },
  { min: 0.6, label: '良好', color: 'text-blue-600', bg: 'bg-blue-100' },
  { min: 0.4, label: '一般', color: 'text-amber-600', bg: 'bg-amber-100' },
  { min: 0, label: '较差', color: 'text-red-600', bg: 'bg-red-100' },
]

function getQualityLevel(score: number) {
  return QUALITY_LEVELS.find(level => score >= level.min) || QUALITY_LEVELS[QUALITY_LEVELS.length - 1]
}

// 计算总录音时长
function getTotalRecordingDuration(samples: File[], currentTime: number): number {
  const completedDuration = samples.length * ((MIN_RECORDING_SECONDS + MAX_RECORDING_SECONDS) / 2)
  return completedDuration + currentTime
}

export default function SpeakerDbPage() {
  const navigate = useNavigate()

  // 状态
  const [speakers, setSpeakers] = useState<SpeakerProfile[]>([])
  const [stats, setStats] = useState<SpeakerStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [selectedSpeaker, setSelectedSpeaker] = useState<SpeakerProfile | null>(null)
  const [similarityPairs, setSimilarityPairs] = useState<SpeakerSimilarityPair[]>([])
  const [showSimilarity, setShowSimilarity] = useState(false)

  // 注册表单状态
  const [regName, setRegName] = useState('')
  const [regSpeakerId, setRegSpeakerId] = useState('')
  const [regRole, setRegRole] = useState('')
  const [regDepartment, setRegDepartment] = useState('')
  const [regAudioFile, setRegAudioFile] = useState<File | null>(null)
  const [regMode, setRegMode] = useState<'mic' | 'file'>('mic')
  const [regRecording, setRegRecording] = useState(false)
  const [regRecordingTime, setRegRecordingTime] = useState(0)
  const [regSamples, setRegSamples] = useState<File[]>([])
  const [regSubmitting, setRegSubmitting] = useState(false)
  const [regMessage, setRegMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const recordingTimerRef = useRef<number | null>(null)
  const currentTextIndexRef = useRef(0)
  const audioPreviewRef = useRef<HTMLAudioElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)

  // 加载数据
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [listData, statsData] = await Promise.all([
        searchKeyword ? searchSpeakers(searchKeyword) : listSpeakers(),
        getSpeakerStats(),
      ])
      setSpeakers(listData.speakers)
      setStats(statsData)
      setSimilarityPairs(statsData.similarity_distribution || [])
    } catch (err) {
      console.error('加载声纹数据失败:', err)
    } finally {
      setLoading(false)
    }
  }, [searchKeyword])

  useEffect(() => {
    loadData()
  }, [loadData])

  // 录音功能
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const audioContext = new AudioContext()
      const source = audioContext.createMediaStreamSource(stream)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
      analyserRef.current = analyser

      const recorder = new MediaRecorder(stream)
      mediaRecorderRef.current = recorder
      audioChunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data)
        }
      }

      recorder.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        const file = new File([audioBlob], `voice_${Date.now()}.webm`, { type: 'audio/webm' })
        setRegSamples(prev => [...prev, file])
        stream.getTracks().forEach(track => track.stop())
        audioContext.close()
      }

      recorder.start()
      setRegRecording(true)
      setRegRecordingTime(0)

      recordingTimerRef.current = window.setInterval(() => {
        setRegRecordingTime(t => t + 0.1)
      }, 100)

      drawWaveform()
    } catch (err) {
      console.error('无法访问麦克风:', err)
      setRegMessage({ type: 'error', text: '无法访问麦克风，请检查权限设置' })
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current)
    }
    setRegRecording(false)

    // 检查录音时长是否足够
    if (regRecordingTime < MIN_RECORDING_SECONDS) {
      setRegMessage({
        type: 'error',
        text: `录音时间过短（${regRecordingTime.toFixed(1)}s），请至少录制 ${MIN_RECORDING_SECONDS} 秒`
      })
    }

    if (canvasRef.current) {
      const ctx = canvasRef.current.getContext('2d')
      if (ctx) ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)
    }
  }

  const drawWaveform = () => {
    if (!canvasRef.current || !analyserRef.current) return
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const analyser = analyserRef.current
    const bufferLength = analyser.frequencyBinCount
    const dataArray = new Uint8Array(bufferLength)

    const draw = () => {
      if (!regRecording) return
      requestAnimationFrame(draw)
      analyser.getByteTimeDomainData(dataArray)

      ctx.fillStyle = 'rgb(30, 30, 46)'
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      ctx.lineWidth = 2
      ctx.strokeStyle = 'rgb(99, 102, 241)'
      ctx.beginPath()

      const sliceWidth = canvas.width / bufferLength
      let x = 0
      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0
        const y = (v * canvas.height) / 2
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
        x += sliceWidth
      }

      ctx.lineTo(canvas.width, canvas.height / 2)
      ctx.stroke()
    }

    draw()
  }

  const playPreview = () => {
    if (regSamples.length === 0) return
    const latestFile = regSamples[regSamples.length - 1]
    const url = URL.createObjectURL(latestFile)
    if (audioPreviewRef.current) {
      audioPreviewRef.current.src = url
      audioPreviewRef.current.play()
    }
  }

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) setRegAudioFile(file)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!regName.trim()) {
      setRegMessage({ type: 'error', text: '请输入姓名' })
      return
    }

    if (regSamples.length < QUALITY_REQUIREMENTS.MIN_SAMPLES) {
      setRegMessage({
        type: 'error',
        text: `样本数量不足：当前${regSamples.length}段，需要至少${QUALITY_REQUIREMENTS.MIN_SAMPLES}段`
      })
      return
    }

    const audioToSend = regAudioFile || (regSamples.length > 0 ? regSamples[regSamples.length - 1] : null)
    if (!audioToSend) {
      setRegMessage({ type: 'error', text: '请录制或上传音频' })
      return
    }

    setRegSubmitting(true)
    setRegMessage(null)

    try {
      const result = await registerSpeaker(
        regName.trim(),
        audioToSend,
        regSpeakerId || undefined,
        regRole || undefined,
        regDepartment || undefined,
        regSamples.length
      )

      // 检查是否有质量警告
      if (result.message.includes('建议') || result.quality_level) {
        const qualityLevel = getQualityLevel(result.quality)
        setRegMessage({
          type: 'success',
          text: `${result.message} | 质量等级：${result.quality_level || qualityLevel.label}`
        })
      } else {
        setRegMessage({ type: 'success', text: result.message })
      }

      // 重置表单
      setRegName('')
      setRegSpeakerId('')
      setRegRole('')
      setRegDepartment('')
      setRegAudioFile(null)
      setRegSamples([])
      // 重新加载列表
      await loadData()
    } catch (err: any) {
      // 显示详细错误信息
      const errorMsg = err.message || '注册失败'
      setRegMessage({ type: 'error', text: errorMsg.split('\n')[0] }) // 只显示第一行作为简要提示

      // 同时在控制台输出详细信息
      console.error('声纹注册详细错误：', errorMsg)
    } finally {
      setRegSubmitting(false)
    }
  }

  const handleDelete = async (speakerId: string) => {
    if (!confirm('确定要删除该说话人吗？')) return
    try {
      await deleteSpeaker(speakerId)
      setSelectedSpeaker(null)
      await loadData()
    } catch (err) {
      console.error('删除失败:', err)
    }
  }

  const resetForm = () => {
    setRegName('')
    setRegSpeakerId('')
    setRegRole('')
    setRegDepartment('')
    setRegAudioFile(null)
    setRegSamples([])
    setRegMessage(null)
  }

  const currentRegText = REGISTRATION_TEXTS[currentTextIndexRef.current % REGISTRATION_TEXTS.length]

  return (
    <div className="min-h-screen bg-gray-50">
      {/* 顶部导航 */}
      <div className="bg-white border-b px-6 py-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="p-2 hover:bg-gray-100 rounded-lg transition"
            title="返回首页"
          >
            <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">声纹数据库管理</h1>
            <p className="text-sm text-gray-500 mt-1">管理已注册的说话人声纹，支持新增、查询和删除</p>
          </div>
        </div>
      </div>

      <div className="flex h-[calc(100vh-140px)]">
        {/* 左侧：说话人列表 */}
        <aside className="w-80 bg-white border-r flex flex-col">
          <div className="p-4 border-b">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-medium text-gray-900">已注册人员</h2>
              <span className="text-xs bg-indigo-100 text-indigo-700 px-2 py-1 rounded-full">
                {stats?.active || 0} 人
              </span>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="搜索姓名..."
                value={searchKeyword}
                onChange={(e) => setSearchKeyword(e.target.value)}
                className="flex-1 px-3 py-2 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <button
                onClick={() => loadData()}
                className="px-3 py-2 bg-gray-100 rounded-lg hover:bg-gray-200 text-sm"
              >
                刷新
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-4 text-center text-gray-500 text-sm">加载中...</div>
            ) : speakers.length === 0 ? (
              <div className="p-4 text-center text-gray-500 text-sm">暂无数据</div>
            ) : (
              <div className="divide-y">
                {speakers.map((speaker) => (
                  <button
                    key={speaker.speaker_id}
                    onClick={() => setSelectedSpeaker(speaker)}
                    className={`w-full text-left p-4 hover:bg-gray-50 transition ${
                      selectedSpeaker?.speaker_id === speaker.speaker_id ? 'bg-indigo-50' : ''
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 bg-indigo-100 rounded-full flex items-center justify-center text-indigo-600 font-medium">
                        {speaker.name?.[0] || '?'}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-gray-900 truncate">{speaker.name || '未知'}</div>
                        <div className="text-xs text-gray-500">
                          {speaker.role || '未设置角色'} · {speaker.department || '未设置部门'}
                        </div>
                      </div>
                    </div>
                    <div className="mt-2 flex items-center gap-3 text-xs text-gray-400">
                      <span>样本 {speaker.sample_count}</span>
                      <span>质量 {Math.round(speaker.quality * 100)}%</span>
                      <span>识别 {speaker.total_identifications}次</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 统计信息 */}
          {stats && (
            <div className="p-4 border-t bg-gray-50">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="bg-white p-2 rounded">
                  <div className="text-gray-500">总人数</div>
                  <div className="font-semibold text-gray-900">{stats.total}</div>
                </div>
                <div className="bg-white p-2 rounded">
                  <div className="text-gray-500">在职</div>
                  <div className="font-semibold text-green-600">{stats.active}</div>
                </div>
                <div className="bg-white p-2 rounded">
                  <div className="text-gray-500">今日识别</div>
                  <div className="font-semibold text-gray-900">{stats.today_identifications}</div>
                </div>
                <div className="bg-white p-2 rounded">
                  <div className="text-gray-500">平均质量</div>
                  <div className="font-semibold text-gray-900">{Math.round(stats.quality_avg * 100)}%</div>
                </div>
              </div>
              <button
                onClick={() => setShowSimilarity(!showSimilarity)}
                className="mt-2 w-full text-xs text-indigo-600 hover:text-indigo-700"
              >
                {showSimilarity ? '收起' : '查看'}声纹相似度分析
              </button>
            </div>
          )}
        </aside>

        {/* 右侧：详情 + 注册表单 */}
        <main className="flex-1 overflow-y-auto p-6">
          {/* 选中说话人详情 */}
          {selectedSpeaker && (
            <div className="mb-6 bg-white rounded-xl shadow-sm border p-6">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-4">
                  <div className="w-16 h-16 bg-indigo-100 rounded-full flex items-center justify-center text-2xl font-medium text-indigo-600">
                    {selectedSpeaker.name?.[0] || '?'}
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-gray-900">{selectedSpeaker.name}</h3>
                    <p className="text-gray-500 text-sm">
                      {selectedSpeaker.role || '未设置角色'} · {selectedSpeaker.department || '未设置部门'}
                    </p>
                    <p className="text-gray-400 text-xs mt-1">
                      ID: {selectedSpeaker.speaker_id}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(selectedSpeaker.speaker_id)}
                  className="text-red-600 hover:text-red-700 text-sm px-3 py-1 border border-red-200 rounded-lg hover:bg-red-50"
                >
                  删除
                </button>
              </div>

              <div className="grid grid-cols-4 gap-4">
                <div className="bg-gray-50 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">注册质量</div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-indigo-500 rounded-full"
                        style={{ width: `${selectedSpeaker.quality * 100}%` }}
                      />
                    </div>
                    <span className="text-sm font-medium">{Math.round(selectedSpeaker.quality * 100)}%</span>
                  </div>
                </div>
                <div className="bg-gray-50 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">音频样本</div>
                  <div className="text-lg font-semibold">{selectedSpeaker.sample_count} 段</div>
                </div>
                <div className="bg-gray-50 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">识别次数</div>
                  <div className="text-lg font-semibold">{selectedSpeaker.total_identifications} 次</div>
                </div>
                <div className="bg-gray-50 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">平均置信度</div>
                  <div className="text-lg font-semibold">
                    {selectedSpeaker.avg_confidence ? `${Math.round(selectedSpeaker.avg_confidence * 100)}%` : '-'}
                  </div>
                </div>
              </div>

              {selectedSpeaker.registered_at && (
                <div className="mt-4 pt-4 border-t text-xs text-gray-400">
                  注册时间：{new Date(selectedSpeaker.registered_at).toLocaleString()}
                  {selectedSpeaker.last_recognized_at && (
                    <> · 最后识别：{new Date(selectedSpeaker.last_recognized_at).toLocaleString()}</>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 相似度分析 */}
          {showSimilarity && similarityPairs.length > 0 && (
            <div className="mb-6 bg-white rounded-xl shadow-sm border p-6">
              <h4 className="font-medium text-gray-900 mb-4">声纹相似度分析</h4>
              <div className="space-y-3">
                {similarityPairs.map((pair, idx) => (
                  <div key={idx} className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">{pair.speaker_a_name}</span>
                      <span className="text-gray-400">↔</span>
                      <span className="font-medium text-gray-900">{pair.speaker_b_name}</span>
                    </div>
                    <div className="flex-1 flex items-center gap-2">
                      <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${
                            pair.similarity > 0.7 ? 'bg-red-500' : pair.similarity > 0.5 ? 'bg-amber-500' : 'bg-green-500'
                          }`}
                          style={{ width: `${pair.similarity * 100}%` }}
                        />
                      </div>
                      <span className="text-sm font-medium w-20 text-right">
                        {Math.round(pair.similarity * 100)}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 注册表单 */}
          <div className="bg-white rounded-xl shadow-sm border p-6">
            <h4 className="font-medium text-gray-900 mb-4">新员工声纹注册</h4>

            {/* 质量要求说明 */}
            <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
              <h5 className="font-medium text-blue-900 mb-2 flex items-center gap-2">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                声纹注册质量要求
              </h5>
              <ul className="text-sm text-blue-800 space-y-1">
                <li className="flex items-center gap-2">
                  <span className="w-5 h-5 rounded-full bg-blue-200 text-blue-700 text-xs flex items-center justify-center font-medium">1</span>
                  最少录制 <strong>{QUALITY_REQUIREMENTS.MIN_SAMPLES} 段</strong>音频，建议 <strong>{QUALITY_REQUIREMENTS.MAX_SAMPLES} 段</strong>
                </li>
                <li className="flex items-center gap-2">
                  <span className="w-5 h-5 rounded-full bg-blue-200 text-blue-700 text-xs flex items-center justify-center font-medium">2</span>
                  每段录音时长 <strong>{QUALITY_REQUIREMENTS.MIN_SAMPLE_DURATION}-{QUALITY_REQUIREMENTS.MAX_SAMPLE_DURATION}秒</strong>
                </li>
                <li className="flex items-center gap-2">
                  <span className="w-5 h-5 rounded-full bg-blue-200 text-blue-700 text-xs flex items-center justify-center font-medium">3</span>
                  总录音时长至少 <strong>{QUALITY_REQUIREMENTS.MIN_TOTAL_DURATION}秒</strong>
                </li>
                <li className="flex items-center gap-2">
                  <span className="w-5 h-5 rounded-full bg-blue-200 text-blue-700 text-xs flex items-center justify-center font-medium">4</span>
                  请在安静环境下录音，朗读提示文字效果更佳
                </li>
              </ul>
              <div className="mt-2 pt-2 border-t border-blue-200">
                <p className="text-xs text-blue-600">
                  <strong>提示</strong>：声纹质量直接影响识别准确率。质量分数需达到 <strong>{Math.round(QUALITY_REQUIREMENTS.MIN_QUALITY_SCORE * 100)}%</strong> 以上才能成功注册。
                </p>
              </div>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    姓名 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={regName}
                    onChange={(e) => setRegName(e.target.value)}
                    placeholder="输入员工姓名"
                    className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    声纹ID（可选）
                  </label>
                  <input
                    type="text"
                    value={regSpeakerId}
                    onChange={(e) => setRegSpeakerId(e.target.value)}
                    placeholder="留空自动生成"
                    className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    角色（可选）
                  </label>
                  <select
                    value={regRole}
                    onChange={(e) => setRegRole(e.target.value)}
                    className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    {ROLE_OPTIONS.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    部门（可选）
                  </label>
                  <input
                    type="text"
                    value={regDepartment}
                    onChange={(e) => setRegDepartment(e.target.value)}
                    placeholder="如：研发部"
                    className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>

              {/* 音频采集 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  音频采集 <span className="text-red-500">*</span>
                </label>
                <div className="flex gap-4 mb-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="regMode"
                      checked={regMode === 'mic'}
                      onChange={() => setRegMode('mic')}
                      className="text-indigo-600"
                    />
                    <span className="text-sm">麦克风录音</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="regMode"
                      checked={regMode === 'file'}
                      onChange={() => setRegMode('file')}
                      className="text-indigo-600"
                    />
                    <span className="text-sm">上传音频文件</span>
                  </label>
                </div>

                {regMode === 'mic' ? (
                  <div className="border rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-sm text-gray-500">
                        样本进度：{regSamples.length}/{QUALITY_REQUIREMENTS.MAX_SAMPLES} 段
                        {regSamples.length < QUALITY_REQUIREMENTS.MIN_SAMPLES && (
                          <span className="text-amber-600 ml-1">（还需 {QUALITY_REQUIREMENTS.MIN_SAMPLES - regSamples.length} 段）</span>
                        )}
                        {regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES && (
                          <span className="text-green-600 ml-1">✓ 已满足最低要求</span>
                        )}
                      </span>
                      {regSamples.length > 0 && (
                        <button
                          type="button"
                          onClick={() => setRegSamples([])}
                          className="text-xs text-gray-500 hover:text-gray-700"
                        >
                          清空
                        </button>
                      )}
                    </div>
                    <div className="flex gap-1 mb-4">
                      {[...Array(QUALITY_REQUIREMENTS.MAX_SAMPLES)].map((_, i) => (
                        <div
                          key={i}
                          className={`h-2 flex-1 rounded-full transition-colors ${
                            i < regSamples.length ? 'bg-indigo-500' : 'bg-gray-200'
                          }`}
                        />
                      ))}
                    </div>

                    {/* 质量提示面板 */}
                    <div className={`mb-4 p-3 rounded-lg border ${
                      regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES 
                        ? 'bg-green-50 border-green-200' 
                        : 'bg-amber-50 border-amber-200'
                    }`}>
                      <div className="flex items-start gap-3">
                        {regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES ? (
                          <svg className="w-5 h-5 text-green-600 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        ) : (
                          <svg className="w-5 h-5 text-amber-600 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                          </svg>
                        )}
                        <div className="text-sm">
                          <p className={`font-medium ${
                            regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES 
                              ? 'text-green-800' 
                              : 'text-amber-800'
                          }`}>
                            {regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES 
                              ? '样本数量已满足要求' 
                              : `还需录制 ${QUALITY_REQUIREMENTS.MIN_SAMPLES - regSamples.length} 段音频`}
                          </p>
                          <p className={`mt-1 ${
                            regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES 
                              ? 'text-green-700' 
                              : 'text-amber-700'
                          }`}>
                            {regSamples.length >= QUALITY_REQUIREMENTS.MIN_SAMPLES
                              ? '多段录音可以提高声纹识别的准确性和稳定性'
                              : `建议录制 ${QUALITY_REQUIREMENTS.MAX_SAMPLES} 段不同内容的音频以获得最佳效果`}
                          </p>
                        </div>
                      </div>
                    </div>

                    {regRecording ? (
                      <div className="text-center">
                        <canvas
                          ref={canvasRef}
                          width={400}
                          height={60}
                          className="mx-auto mb-3 rounded bg-gray-900"
                        />
                        <div className="flex items-center justify-center gap-3 mb-3">
                          <div className="w-3 h-3 bg-red-500 rounded-full animate-pulse" />
                          <span className="text-lg font-mono">{regRecordingTime.toFixed(1)}s</span>
                          {regRecordingTime < MIN_RECORDING_SECONDS && (
                            <span className="text-sm text-gray-400">
                              (建议 {MIN_RECORDING_SECONDS}-{MAX_RECORDING_SECONDS}s)
                            </span>
                          )}
                          {regRecordingTime >= MIN_RECORDING_SECONDS && regRecordingTime <= MAX_RECORDING_SECONDS && (
                            <span className="text-sm text-green-500">✓ 时长合适</span>
                          )}
                          {regRecordingTime > MAX_RECORDING_SECONDS && (
                            <span className="text-sm text-amber-500">时长较长，可停止</span>
                          )}
                        </div>
                        <p className="text-sm text-gray-600 mb-3">
                          请朗读 <strong>"{currentRegText}"</strong>
                        </p>
                        <button
                          type="button"
                          onClick={stopRecording}
                          className="px-6 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600"
                        >
                          停止录音
                        </button>
                      </div>
                    ) : regSamples.length > 0 ? (
                      <div className="text-center">
                        <audio ref={audioPreviewRef} className="hidden" />
                        <p className="text-sm text-green-600 mb-3">
                          已录制 {regSamples.length} 段音频
                        </p>
                        <div className="flex justify-center gap-3">
                          <button
                            type="button"
                            onClick={playPreview}
                            className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 text-sm"
                          >
                            播放预览
                          </button>
                          {regSamples.length < QUALITY_REQUIREMENTS.MAX_SAMPLES && (
                            <button
                              type="button"
                              onClick={startRecording}
                              className="px-4 py-2 bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 text-sm"
                            >
                              继续录制
                            </button>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="text-center">
                        <div className="w-16 h-16 mx-auto mb-3 bg-gray-100 rounded-full flex items-center justify-center">
                          <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                          </svg>
                        </div>
                        <p className="text-sm text-gray-500 mb-3">
                          点击开始录音，朗读提示文字
                        </p>
                        <button
                          type="button"
                          onClick={startRecording}
                          className="px-6 py-2 bg-indigo-500 text-white rounded-lg hover:bg-indigo-600"
                        >
                          开始录音
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="border rounded-lg p-4">
                    <label className="block">
                      <input
                        type="file"
                        accept="audio/*"
                        onChange={handleFileUpload}
                        className="hidden"
                      />
                      <div className="text-center cursor-pointer hover:bg-gray-50 py-4">
                        {regAudioFile ? (
                          <div>
                            <svg className="w-8 h-8 mx-auto text-green-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <p className="text-sm text-green-600">{regAudioFile.name}</p>
                            <p className="text-xs text-gray-400 mt-1">
                              {(regAudioFile.size / 1024).toFixed(1)} KB
                            </p>
                          </div>
                        ) : (
                          <div>
                            <svg className="w-8 h-8 mx-auto text-gray-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                            </svg>
                            <p className="text-sm text-gray-500">点击或拖拽上传音频文件</p>
                            <p className="text-xs text-gray-400 mt-1">支持 WAV / MP3 / OGG / FLAC</p>
                          </div>
                        )}
                      </div>
                    </label>
                  </div>
                )}
              </div>

              {/* 消息提示 */}
              {regMessage && (
                <div className={`px-4 py-3 rounded-lg text-sm ${
                  regMessage.type === 'success'
                    ? 'bg-green-50 text-green-700 border border-green-200'
                    : 'bg-red-50 text-red-700 border border-red-200'
                }`}>
                  {regMessage.text}
                </div>
              )}

              {/* 提交按钮 */}
              <div className="flex gap-3 pt-2">
                <button
                  type="submit"
                  disabled={regSubmitting || regSamples.length < QUALITY_REQUIREMENTS.MIN_SAMPLES}
                  className={`px-6 py-2 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed ${
                    regSamples.length < QUALITY_REQUIREMENTS.MIN_SAMPLES
                      ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                      : 'bg-indigo-500 text-white hover:bg-indigo-600'
                  }`}
                  title={regSamples.length < QUALITY_REQUIREMENTS.MIN_SAMPLES
                    ? `请至少录制 ${QUALITY_REQUIREMENTS.MIN_SAMPLES} 段音频`
                    : ''}
                >
                  {regSubmitting ? '注册中...' : '注册声纹'}
                </button>
                <button
                  type="button"
                  onClick={resetForm}
                  className="px-6 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200"
                >
                  重置
                </button>
              </div>
              {regSamples.length < QUALITY_REQUIREMENTS.MIN_SAMPLES && (
                <p className="text-sm text-amber-600 mt-2">
                  <svg className="w-4 h-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  请至少录制 {QUALITY_REQUIREMENTS.MIN_SAMPLES} 段音频，当前 {regSamples.length} 段
                </p>
              )}
            </form>
          </div>
        </main>
      </div>
    </div>
  )
}
