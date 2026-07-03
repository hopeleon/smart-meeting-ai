import { useNavigate } from 'react-router-dom'

export default function Navbar() {
  const navigate = useNavigate()

  return (
    <nav className="glass sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
      <div
        className="flex items-center gap-3 cursor-pointer"
        onClick={() => navigate('/')}
      >
        <div className="w-8 h-8 bg-sky-600 rounded-lg flex items-center justify-center text-white font-bold text-sm">
          M
        </div>
        <span className="font-semibold text-slate-900">智能会议记录</span>
      </div>
      <div className="flex items-center gap-4 text-sm">
        <button
          onClick={() => navigate('/laoji')}
          className="text-slate-500 hover:text-sky-700 transition flex items-center gap-1.5"
        >
          <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          老记
        </button>
        <button
          onClick={() => navigate('/speakers')}
          className="text-slate-500 hover:text-sky-700 transition flex items-center gap-1.5"
        >
          <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
          声纹管理
        </button>
        <span className="text-slate-400">v0.1.0</span>
      </div>
    </nav>
  )
}
