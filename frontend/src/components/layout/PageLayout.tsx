import { Outlet } from 'react-router-dom'
import Navbar from './Navbar'

export default function PageLayout() {
  return (
    <div className="min-h-screen bg-dark">
      <Navbar />
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  )
}
