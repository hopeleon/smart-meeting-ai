import { BrowserRouter, Routes, Route } from 'react-router-dom'
import PageLayout from './components/layout/PageLayout'
import HomePage from './pages/HomePage'
import MeetingActivePage from './pages/MeetingActivePage'
import MeetingSummaryPage from './pages/MeetingSummaryPage'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<PageLayout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/meeting/:meetingId" element={<MeetingActivePage />} />
          <Route path="/meeting/:meetingId/summary" element={<MeetingSummaryPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
