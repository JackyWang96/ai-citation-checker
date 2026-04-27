import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadEssay } from '../lib/api'

type State = 'idle' | 'uploading' | 'error'

export default function Upload() {
  const [state, setState] = useState<State>('idle')
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const navigate = useNavigate()

  const handleFile = useCallback(async (file: File) => {
    if (!file.name.endsWith('.docx')) {
      setError('Only .docx files are supported')
      setState('error')
      return
    }
    setState('uploading')
    setError('')
    try {
      const { report_id } = await uploadEssay(file)
      navigate(`/r/${report_id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
      setState('error')
    }
  }, [navigate])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  const onInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-md p-10 w-full max-w-lg text-center">
        <h1 className="text-2xl font-bold mb-2">AI Citation Checker</h1>
        <p className="text-gray-500 mb-6 text-sm">
          Upload your .docx essay — we'll verify every APA citation in ~5 seconds.
        </p>

        <label
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`block border-2 border-dashed rounded-xl p-10 cursor-pointer transition
            ${dragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400'}`}
        >
          <input type="file" accept=".docx" className="hidden" onChange={onInput} />
          {state === 'uploading' ? (
            <div className="flex flex-col items-center gap-3">
              <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-gray-500">Analysing citations…</p>
            </div>
          ) : (
            <>
              <p className="text-4xl mb-3">📄</p>
              <p className="font-medium text-gray-700">Drop your .docx here</p>
              <p className="text-sm text-gray-400 mt-1">or click to browse</p>
            </>
          )}
        </label>

        {state === 'error' && (
          <p className="mt-4 text-red-500 text-sm">{error}</p>
        )}

        <p className="mt-6 text-xs text-gray-400">
          Files are analysed in memory and never stored. Reports expire after 24h.
        </p>
      </div>
    </div>
  )
}
