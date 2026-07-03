import { BrowserRouter, Routes, Route } from 'react-router-dom'
import PageLayout from './components/layout/PageLayout'
import HomePage from './pages/HomePage'
import MeetingSummaryPage from './pages/MeetingSummaryPage'
import MeetingActivePage from './pages/MeetingActivePage'
import MeetingWhisperPage from './pages/MeetingWhisperPage'
import MeetingQwenPage from './pages/MeetingQwenPage'
import MeetingOfflinePage from './pages/MeetingOfflinePage'
import SpeakerDbPage from './pages/SpeakerDbPage'
import SchedulePage from './pages/SchedulePage'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<PageLayout />}>
          <Route path="/" element={<HomePage />} />
        <Route path="/meeting/:meetingId" element={<MeetingActivePage />} />
        <Route path="/meeting/:meetingId/summary" element={<MeetingSummaryPage />} />
          <Route path="/meeting/:meetingId/active" element={<MeetingActivePage />} />
          <Route path="/meeting/:meetingId/offline" element={<MeetingOfflinePage />} />
          <Route path="/meeting/:meetingId/whisper" element={<MeetingWhisperPage />} />
          <Route path="/meeting/:meetingId/qwen" element={<MeetingQwenPage />} />
          <Route path="/laoji" element={<SchedulePage />} />
          <Route path="/schedule" element={<SchedulePage />} />
        </Route>
        <Route path="/speakers" element={<SpeakerDbPage />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
