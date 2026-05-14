import { useNavigate } from 'react-router-dom'

export default function Navbar() {
  const navigate = useNavigate()

  return (
    <nav className="glass sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
      <div
        className="flex items-center gap-3 cursor-pointer"
        onClick={() => navigate('/')}
      >
        <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center text-white font-bold text-sm">
          M
        </div>
        <span className="font-semibold">智能会议记录</span>
      </div>
      <div className="flex items-center gap-4 text-sm text-gray-400">
        <span>v0.1.0</span>
      </div>
    </nav>
  )
}
