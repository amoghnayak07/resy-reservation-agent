import { Routes, Route } from 'react-router-dom'
import { ChatPage } from './pages/ChatPage'
import { DashboardPage } from './pages/DashboardPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route path="/dashboard" element={<DashboardPage />} />
    </Routes>
  )
}

export default App
