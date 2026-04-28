import { useT } from '../i18n'
import IssueCard from './IssueCard'

interface Issue {
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

interface CitationData {
  id: string
  kind: 'intext' | 'reference'
  raw_text: string
  status: 'pass' | 'warning' | 'error'
  issues: Issue[]
}

interface Summary {
  total: number
  pass: number
  warning: number
  error: number
}

type FilterStatus = 'all' | 'pass' | 'warning' | 'error'

interface Props {
  citations: CitationData[]
  summary: Summary
  activeCitId: string | null
  hoveredCitId: string | null
  filterStatus: FilterStatus
  expiresAt: string
  onCitClick: (id: string) => void
  onCitHover: (id: string | null) => void
  onFilterChange: (status: FilterStatus) => void
  issueRefs: React.MutableRefObject<Record<string, HTMLDivElement | null>>
}

function formatExpiry(expiresAt: string, expiresInFn: (h: number, m: number) => string, expiredLabel: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (ms <= 0) return expiredLabel
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  return expiresInFn(h, m)
}

export default function IssuePanel({
  citations, summary, activeCitId, hoveredCitId,
  filterStatus, expiresAt, onCitClick, onCitHover, onFilterChange, issueRefs,
}: Props) {
  const { t } = useT()

  const filtered = filterStatus === 'all' ? citations : citations.filter((c) => c.status === filterStatus)

  const tabs: { key: FilterStatus; label: string; count: number }[] = [
    { key: 'all',     label: t.filterAll,     count: summary.total   },
    { key: 'error',   label: t.filterError,   count: summary.error   },
    { key: 'warning', label: t.filterWarning, count: summary.warning },
    { key: 'pass',    label: t.filterPass,    count: summary.pass    },
  ]

  return (
    <div style={{ width: 380, flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg)', overflow: 'hidden' }}>
      {/* Filter tabs */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', gap: 6, flexShrink: 0 }}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => onFilterChange(tab.key)}
            style={{
              padding: '4px 10px', borderRadius: 99, fontSize: 12, fontWeight: 600,
              background: filterStatus === tab.key ? 'var(--accent)' : 'transparent',
              color: filterStatus === tab.key ? 'white' : 'var(--text-3)',
              transition: 'all 0.15s',
            }}
          >
            {tab.label}({tab.count})
          </button>
        ))}
      </div>

      {/* Issue list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {filtered.length === 0 ? (
          <div style={{ padding: '40px 16px', textAlign: 'center', fontSize: 13, color: 'var(--text-3)' }}>
            {t.noIssues}
          </div>
        ) : (
          filtered.map((cit) => (
            <div key={cit.id} ref={(el) => { issueRefs.current[cit.id] = el }}>
              <IssueCard
                cit={cit}
                isActive={activeCitId === cit.id}
                isHovered={hoveredCitId === cit.id}
                onClick={() => onCitClick(cit.id)}
                onHover={onCitHover}
              />
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', background: 'var(--surface)', fontSize: 11, color: 'var(--text-3)', display: 'flex', justifyContent: 'space-between', flexShrink: 0 }}>
        <span>{formatExpiry(expiresAt, t.expiresIn, t.expired)}</span>
        <button
          style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600 }}
          onClick={() => navigator.clipboard.writeText(window.location.href)}
        >
          {t.copyLink}
        </button>
      </div>
    </div>
  )
}
