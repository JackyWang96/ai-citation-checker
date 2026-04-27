import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { fetchReport } from '../lib/api'
import AnnotatedText from '../components/AnnotatedText'
import IssuePanel from '../components/IssuePanel'
import { LogoIcon } from './Upload'

type CitationStatus = 'pass' | 'warning' | 'error'
type FilterStatus = 'all' | 'pass' | 'warning' | 'error'

interface CitationIssue {
  type?: string
  severity: string
  category: string
  reason: string
  detail?: string
  field?: string
  expected?: string
  actual?: string
  rule_id?: string
}

interface Citation {
  id: string
  kind: 'intext' | 'reference'
  raw_text: string
  char_start: number
  char_end: number
  status: CitationStatus
  issues: CitationIssue[]
  verified_reference_id?: string
}

interface ReportData {
  id: string
  filename: string
  created_at: string
  expires_at: string
  full_text: string
  citations: Citation[]
  summary: { total: number; pass: number; warning: number; error: number }
}

function ScoreGauge({ pass, total }: { pass: number; total: number }) {
  const pct = total > 0 ? Math.round((pass / total) * 100) : 100
  const color = pct === 100 ? 'var(--green)' : pct >= 70 ? 'var(--amber)' : 'var(--red)'
  const label = pct === 100 ? '全部通过' : pct >= 70 ? '基本良好' : '需要修改'
  const r = 14
  const circumference = 2 * Math.PI * r

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ position: 'relative', width: 36, height: 36 }}>
        <svg width="36" height="36" viewBox="0 0 36 36" style={{ transform: 'rotate(-90deg)' }}>
          <circle cx="18" cy="18" r={r} fill="none" stroke="var(--border)" strokeWidth="4" />
          <circle
            cx="18"
            cy="18"
            r={r}
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - pct / 100)}
            strokeLinecap="round"
          />
        </svg>
        <span
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 9,
            fontWeight: 700,
            fontFamily: 'var(--mono)',
            color,
          }}
        >
          {pct}%
        </span>
      </div>
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--text-3)' }}>引用得分</div>
      </div>
    </div>
  )
}

function Divider() {
  return <div style={{ width: 1, height: 24, background: 'var(--border)', flexShrink: 0 }} />
}

export default function Report() {
  const { id } = useParams<{ id: string }>()
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState('')
  const [activeCitId, setActiveCitId] = useState<string | null>(null)
  const [hoveredCitId, setHoveredCitId] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('all')
  const issueRefs = useRef<Record<string, HTMLDivElement | null>>({})

  useEffect(() => {
    if (!id) return
    fetchReport(id)
      .then(setReport)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '加载报告失败'))
  }, [id])

  const handleCitClick = useCallback((citId: string) => {
    setActiveCitId((prev) => (prev === citId ? null : citId))
    // Scroll issue card into view
    const card = issueRefs.current[citId]
    if (card) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    // Scroll reference entry in essay into view
    const el = document.querySelector(`[data-cit-id="${citId}"]`)
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [])

  const handleCitHover = useCallback((citId: string | null) => {
    setHoveredCitId(citId)
  }, [])

  if (error) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
        <p style={{ color: 'var(--red)', fontSize: 15 }}>{error}</p>
      </div>
    )
  }

  if (!report) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            border: '3px solid var(--border)',
            borderTopColor: 'var(--accent)',
            animation: 'spin 0.9s linear infinite',
          }}
        />
      </div>
    )
  }

  const { summary, citations, full_text, filename, expires_at } = report
  const hasAmbiguous = citations.some((c) => c.issues.some((iss) => iss.category === 'ambiguous'))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', fontFamily: 'var(--font)' }}>
      {/* Top bar */}
      <header
        style={{
          borderBottom: '1px solid var(--border)',
          background: 'var(--surface)',
          padding: '12px 24px',
          display: 'flex',
          alignItems: 'center',
          gap: 20,
          flexShrink: 0,
          boxShadow: '0 1px 0 var(--border)',
        }}
      >
        {/* Logo + filename */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 4 }}>
          <LogoIcon size={20} />
          <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: '-0.01em' }}>引文检查器</span>
        </div>
        <Divider />
        <div style={{ fontSize: 13, color: 'var(--text-3)' }}>
          <span style={{ fontWeight: 600, color: 'var(--text)' }}>{filename}</span>
          <span style={{ marginLeft: 8 }}>· 共发现 {summary.total} 条引用</span>
        </div>

        {/* Right side */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 20 }}>
          <ScoreGauge pass={summary.pass} total={summary.total} />
          <Divider />

          {/* Counts */}
          {[
            { label: '通过', count: summary.pass,    color: 'var(--green)' },
            { label: '警告', count: summary.warning, color: 'var(--amber)' },
            { label: '错误', count: summary.error,   color: 'var(--red)'   },
          ].map((s) => (
            <div key={s.label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--mono)', color: s.color, lineHeight: 1 }}>
                {s.count}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>{s.label}</div>
            </div>
          ))}

          {hasAmbiguous && (
            <>
              <Divider />
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '6px 12px',
                  background: 'var(--amber-bg)',
                  borderRadius: 8,
                  fontSize: 12,
                  color: 'var(--amber)',
                }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <path d="M8 2L14 13H2L8 2Z" stroke="var(--amber)" strokeWidth="1.5" fill="none" strokeLinejoin="round" />
                  <path d="M8 7v3M8 11.5v.5" stroke="var(--amber)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                添加 DOI 可消除引用歧义
              </div>
            </>
          )}
        </div>
      </header>

      {/* Body: split panels */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Left: annotated essay */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            background: 'var(--surface)',
            borderRight: '1px solid var(--border)',
          }}
        >
          <AnnotatedText
            fullText={full_text}
            citations={citations}
            activeCitId={activeCitId}
            hoveredCitId={hoveredCitId}
            onCitClick={handleCitClick}
            onCitHover={handleCitHover}
          />
        </div>

        {/* Right: issue panel */}
        <IssuePanel
          citations={citations}
          summary={summary}
          activeCitId={activeCitId}
          hoveredCitId={hoveredCitId}
          filterStatus={filterStatus}
          expiresAt={expires_at}
          onCitClick={handleCitClick}
          onCitHover={handleCitHover}
          onFilterChange={setFilterStatus}
          issueRefs={issueRefs}
        />
      </div>
    </div>
  )
}
