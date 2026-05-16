import { Routes, Route } from 'react-router-dom'
import Upload from './pages/Upload'
import Loading from './pages/Loading'
import Report from './pages/Report'
import { Version } from './components/Version'

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<Upload />} />
        <Route path="/loading" element={<Loading />} />
        <Route path="/r/:id" element={<Report />} />
      </Routes>
      <Version />
    </>
  )
}
