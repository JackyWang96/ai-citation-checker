import { useT } from '../i18n'
import StatusBadge from './StatusBadge'
import CategoryTag from './CategoryTag'
import RichText, { type TextRun } from './RichText'

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
  runs?: TextRun[] | null
  suggestion?: string
  suggestion_explanation?: string
  suggestion_verified?: boolean
  suggestion_validation?: string[]
}

interface Props {
  cit: CitationData
  isActive: boolean
  isHovered: boolean
  onClick: () => void
  onHover: (id: string | null) => void
}

const STATUS_COLOR: Record<string, string> = {
  pass: 'var(--green)', warning: 'var(--amber)', error: 'var(--red)',
}
const STATUS_BG: Record<string, string> = {
  pass: 'var(--green-bg)', warning: 'var(--amber-bg)', error: 'var(--red-bg)',
}
const STATUS_BORDER: Record<string, string> = {
  pass: 'var(--green-border)', warning: 'var(--amber-border)', error: 'var(--red-border)',
}
const STATUS_ICON: Record<string, string> = {
  pass: '✓', warning: '!', error: '✕',
}

export default function IssueCard({ cit, isActive, onClick, onHover }: Props) {
  const { t } = useT()
  const color = STATUS_COLOR[cit.status]
  const bg = STATUS_BG[cit.status]
  const border = STATUS_BORDER[cit.status]

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => onHover(cit.id)}
      onMouseLeave={() => onHover(null)}
      style={{
        padding: '14px 16px', borderRadius: 10,
        border: `1.5px solid ${isActive ? border : 'var(--border)'}`,
        background: isActive ? bg : 'var(--surface)',
        cursor: 'pointer', transition: 'all 0.15s ease',
        boxShadow: isActive ? `0 0 0 3px ${color}22` : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        {/* Status icon */}
        <div
          style={{
            width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
            background: isActive ? color : bg,
            border: `1.5px solid ${color}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, fontWeight: 700, color: isActive ? 'white' : color,
          }}
        >
          {STATUS_ICON[cit.status]}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Header row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-3)', fontWeight: 500 }}>
              {cit.id}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>·</span>
            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
              {cit.kind === 'intext' ? t.kindIntext : t.kindReference}
            </span>
            <StatusBadge status={cit.status} />
          </div>

          {/* Raw text preview */}
          <div
            style={{
              fontSize: 13, color: 'var(--text-2)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              marginBottom: cit.issues.length > 0 ? 10 : 0,
            }}
          >
            <RichText text={cit.raw_text} runs={cit.runs} />
          </div>

          {/* Pass state */}
          {cit.issues.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--green)', marginTop: 6 }}>
              ✓ {t.verifiedMsg}
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
                <div style={{ marginTop: 4, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 8px', fontFamily: 'var(--mono)', fontSize: 11 }}>
                  <span style={{ color: 'var(--text-3)' }}>expected</span>
                  <span style={{ color: 'var(--green)' }}>{iss.expected}</span>
                  <span style={{ color: 'var(--text-3)' }}>actual</span>
                  <span style={{ color: 'var(--red)' }}>{iss.actual}</span>
                </div>
              )}
              {iss.detail && (
                <div style={{ marginTop: 6, padding: '8px 10px', background: 'var(--bg)', borderRadius: 6, fontSize: 11, color: 'var(--text-3)', fontFamily: 'var(--mono)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                  {iss.detail}
                </div>
              )}
            </div>
          ))}

          {/* AI fix suggestion. A rewrite that failed the checker's own rules
              must never be shown in the verified style — it is a draft, and
              saying otherwise is the one claim this tool cannot afford to get
              wrong. */}
          {cit.suggestion && (() => {
            const verified = cit.suggestion_verified === true
            const accent = verified ? 'var(--accent)' : 'var(--warn, #b45309)'
            return (
              <div style={{ marginTop: 10, padding: '8px 10px', background: verified ? 'var(--accent-bg, var(--bg))' : 'var(--bg)', border: `1px solid ${accent}`, borderRadius: 6, fontSize: 12, lineHeight: 1.6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, color: accent, fontWeight: 600, fontSize: 11 }}>
                  {verified ? `✨ ${t.suggestedFix}` : `⚠️ ${t.unverifiedDraft}`}
                </div>
                <div style={{ color: 'var(--text)', fontFamily: 'var(--mono)', fontSize: 11.5, whiteSpace: 'pre-wrap' }}>
                  {cit.suggestion}
                </div>
                {cit.suggestion_explanation && (
                  <div style={{ marginTop: 4, color: 'var(--text-3)', fontSize: 11 }}>
                    {cit.suggestion_explanation}
                  </div>
                )}
                {!verified && (
                  <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 11 }}>
                    <div style={{ fontWeight: 600 }}>{t.stillFailing}</div>
                    <ul style={{ margin: '2px 0 0', paddingLeft: 16 }}>
                      {(cit.suggestion_validation ?? []).map((v, i) => (
                        <li key={i}>{v}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
