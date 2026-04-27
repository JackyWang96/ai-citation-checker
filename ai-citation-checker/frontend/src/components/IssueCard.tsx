import StatusBadge from './StatusBadge'
import CategoryTag from './CategoryTag'

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
  verified?: { source: string; doi?: string }
}

interface Props {
  cit: CitationData
  isActive: boolean
  isHovered: boolean
  onClick: () => void
  onHover: (id: string | null) => void
}

const STATUS_COLOR: Record<string, string> = {
  pass:    'var(--green)',
  warning: 'var(--amber)',
  error:   'var(--red)',
}
const STATUS_BG: Record<string, string> = {
  pass:    'var(--green-bg)',
  warning: 'var(--amber-bg)',
  error:   'var(--red-bg)',
}
const STATUS_BORDER: Record<string, string> = {
  pass:    'var(--green-border)',
  warning: 'var(--amber-border)',
  error:   'var(--red-border)',
}
const STATUS_ICON: Record<string, string> = {
  pass: '✓', warning: '!', error: '✕',
}

export default function IssueCard({ cit, isActive, onClick, onHover }: Props) {
  const color = STATUS_COLOR[cit.status]
  const bg = STATUS_BG[cit.status]
  const border = STATUS_BORDER[cit.status]

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => onHover(cit.id)}
      onMouseLeave={() => onHover(null)}
      style={{
        padding: '14px 16px',
        borderRadius: 10,
        border: `1.5px solid ${isActive ? border : 'var(--border)'}`,
        background: isActive ? bg : 'var(--surface)',
        cursor: 'pointer',
        transition: 'all 0.15s ease',
        boxShadow: isActive ? `0 0 0 3px ${color}22` : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        {/* Status icon */}
        <div
          style={{
            width: 22,
            height: 22,
            borderRadius: '50%',
            flexShrink: 0,
            background: isActive ? color : bg,
            border: `1.5px solid ${color}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 11,
            fontWeight: 700,
            color: isActive ? 'white' : color,
          }}
        >
          {STATUS_ICON[cit.status]}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Header row */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              marginBottom: 4,
              flexWrap: 'wrap',
            }}
          >
            <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-3)', fontWeight: 500 }}>
              {cit.id}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>·</span>
            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
              {cit.kind === 'intext' ? '正文引用' : '参考文献'}
            </span>
            <StatusBadge status={cit.status} />
          </div>

          {/* Raw text preview */}
          <div
            style={{
              fontSize: 13,
              color: 'var(--text-2)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              marginBottom: cit.issues.length > 0 ? 10 : 0,
            }}
          >
            {cit.raw_text}
          </div>

          {/* Pass state */}
          {cit.issues.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--green)', marginTop: 6 }}>
              ✓ 引用已验证 · 作者、年份、标题及期刊均匹配 · APA 格式正确
            </div>
          )}

          {/* Issue blocks */}
          {cit.issues.map((iss, i) => (
            <div key={i} style={{ marginTop: 8, fontSize: 12, lineHeight: 1.6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <CategoryTag category={iss.category} />
                {iss.rule_id && (
                  <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-3)' }}>
                    {iss.rule_id}
                  </span>
                )}
              </div>
              <div style={{ color: 'var(--text-2)' }}>{iss.reason}</div>
              {iss.expected && (
                <div
                  style={{
                    marginTop: 4,
                    display: 'grid',
                    gridTemplateColumns: 'auto 1fr',
                    gap: '2px 8px',
                    fontFamily: 'var(--mono)',
                    fontSize: 11,
                  }}
                >
                  <span style={{ color: 'var(--text-3)' }}>expected</span>
                  <span style={{ color: 'var(--green)' }}>{iss.expected}</span>
                  <span style={{ color: 'var(--text-3)' }}>actual</span>
                  <span style={{ color: 'var(--red)' }}>{iss.actual}</span>
                </div>
              )}
              {iss.detail && (
                <div
                  style={{
                    marginTop: 6,
                    padding: '8px 10px',
                    background: 'var(--bg)',
                    borderRadius: 6,
                    fontSize: 11,
                    color: 'var(--text-3)',
                    fontFamily: 'var(--mono)',
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.6,
                  }}
                >
                  {iss.detail}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
