import { Routes, Route } from 'react-router-dom'
import Upload from './pages/Upload'
import Report from './pages/Report'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Upload />} />
      <Route path="/r/:id" element={<Report />} />
    </Routes>
  )
}
