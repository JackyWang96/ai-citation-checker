import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { fetchReport } from '../lib/api'
import AnnotatedText from '../components/AnnotatedText'
import IssuePanel from '../components/IssuePanel'

interface Issue {
  reason: string
  detail?: string
  field?: string
  expected?: string
  actual?: string
  category: string
  severity: string
  rule_id?: string
}

interface CitationData {
  id: string
  kind: string
  raw_text: string
  char_start: number
  char_end: number
  status: 'pass' | 'warning' | 'error'
  issues: Issue[]
}

interface ReportData {
  id: string
  filename: string
  full_text: string
  citations: CitationData[]
  summary: Record<string, number>
}

export default function Report() {
  const { id } = useParams<{ id: string }>()
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState('')
  const [activeCitationId, setActiveCitationId] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    fetchReport(id)
      .then(setReport)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load report'))
  }, [id])

  const handleCitationClick = useCallback((citId: string) => {
    setActiveCitationId(citId)
    document.getElementById(citId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-red-500">{error}</p>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const { summary, citations, full_text, filename } = report

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div>
          <h1 className="font-semibold text-gray-800">{filename}</h1>
          <p className="text-xs text-gray-400">This report expires in 24h</p>
        </div>
        <div className="flex gap-4 text-sm">
          <span className="text-green-600">🟢 {summary.pass} pass</span>
          <span className="text-yellow-600">🟡 {summary.warning} warning</span>
          <span className="text-red-600">🔴 {summary.error} error</span>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-y-auto p-6">
          <AnnotatedText
            fullText={full_text}
            citations={citations}
            activeCitationId={activeCitationId}
            onCitationClick={handleCitationClick}
          />
        </div>

        <div className="w-96 border-l border-gray-200 bg-white overflow-y-auto">
          <div className="p-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-700 text-sm">Issues</h2>
          </div>
          <IssuePanel
            citations={citations}
            activeCitationId={activeCitationId}
            onIssueClick={handleCitationClick}
          />
        </div>
      </div>
    </div>
  )
}
