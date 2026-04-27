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

function formatExpiry(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (ms <= 0) return '已过期'
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  return `${h}h ${m}m 后过期`
}

export default function IssuePanel({
  citations,
  summary,
  activeCitId,
  hoveredCitId,
  filterStatus,
  expiresAt,
  onCitClick,
  onCitHover,
  onFilterChange,
  issueRefs,
}: Props) {
  const filtered =
    filterStatus === 'all' ? citations : citations.filter((c) => c.status === filterStatus)

  const tabs: { key: FilterStatus; label: string; count: number }[] = [
    { key: 'all',     label: '全部',   count: summary.total   },
    { key: 'error',   label: '错误',   count: summary.error   },
    { key: 'warning', label: '警告',   count: summary.warning },
    { key: 'pass',    label: '通过',   count: summary.pass    },
  ]

  return (
    <div
      style={{
        width: 380,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg)',
        overflow: 'hidden',
      }}
    >
      {/* Filter tabs */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--surface)',
          display: 'flex',
          gap: 6,
          flexShrink: 0,
        }}
      >
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => onFilterChange(t.key)}
            style={{
              padding: '4px 10px',
              borderRadius: 99,
              fontSize: 12,
              fontWeight: 600,
              background: filterStatus === t.key ? 'var(--accent)' : 'transparent',
              color: filterStatus === t.key ? 'white' : 'var(--text-3)',
              transition: 'all 0.15s',
            }}
          >
            {t.label}({t.count})
          </button>
        ))}
      </div>

      {/* Issue list */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {filtered.length === 0 ? (
          <div
            style={{
              padding: '40px 16px',
              textAlign: 'center',
              fontSize: 13,
              color: 'var(--text-3)',
            }}
          >
            🎉 没有问题！
          </div>
        ) : (
          filtered.map((cit) => (
            <div
              key={cit.id}
              ref={(el) => {
                issueRefs.current[cit.id] = el
              }}
            >
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
      <div
        style={{
          padding: '10px 16px',
          borderTop: '1px solid var(--border)',
          background: 'var(--surface)',
          fontSize: 11,
          color: 'var(--text-3)',
          display: 'flex',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}
      >
        <span>{formatExpiry(expiresAt)}</span>
        <button
          style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600 }}
          onClick={() => navigator.clipboard.writeText(window.location.href)}
        >
          复制分享链接
        </button>
      </div>
    </div>
  )
}
