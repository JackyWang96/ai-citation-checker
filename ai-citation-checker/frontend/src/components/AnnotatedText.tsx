import { useT } from '../i18n'
import Citation from './Citation'

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
  kind: 'intext' | 'reference'
  raw_text: string
  char_start: number
  char_end: number
  status: 'pass' | 'warning' | 'error'
  issues: Issue[]
}

interface Props {
  fullText: string
  citations: CitationData[]
  activeCitId: string | null
  hoveredCitId: string | null
  onCitClick: (id: string) => void
  onCitHover: (id: string | null) => void
}

const BORDER_COLOR: Record<string, string> = {
  pass: 'var(--green-border)', warning: 'var(--amber-border)', error: 'var(--red-border)',
}
const BG_COLOR: Record<string, string> = {
  pass: 'var(--green-bg)', warning: 'var(--amber-bg)', error: 'var(--red-bg)',
}

export default function AnnotatedText({ fullText, citations, activeCitId, hoveredCitId, onCitClick, onCitHover }: Props) {
  const { t } = useT()

  const refCitations = citations.filter((c) => c.kind === 'reference')
  const intextCitations = citations.filter((c) => c.kind === 'intext')

  // Filter out references that couldn't be located in full_text (char_start=0
  // means "not found" — using them would truncate the body to empty).
  const locatableRefs = refCitations.filter((c) => c.char_start > 0)
  const firstRefChar =
    locatableRefs.length > 0
      ? Math.min(...locatableRefs.map((c) => c.char_start))
      : fullText.length

  const bodyText = fullText.slice(0, firstRefChar).trimEnd()

  const sortedIntext = [...intextCitations].sort((a, b) => a.char_start - b.char_start)
  const segments: React.ReactNode[] = []
  let cursor = 0

  for (const c of sortedIntext) {
    if (c.char_start >= firstRefChar) break
    if (c.char_start > cursor) {
      segments.push(<TextBlock key={`txt-${cursor}`} text={bodyText.slice(cursor, c.char_start)} />)
    }
    segments.push(
      <Citation
        key={c.id}
        id={c.id}
        status={c.status}
        text={fullText.slice(c.char_start, c.char_end) || c.raw_text}
        issues={c.issues}
        isActive={activeCitId === c.id}
        isHovered={hoveredCitId === c.id}
        onClick={onCitClick}
        onHover={onCitHover}
      />,
    )
    cursor = c.char_end
  }

  if (cursor < bodyText.length) {
    segments.push(<TextBlock key="txt-end" text={bodyText.slice(cursor)} />)
  }

  return (
    <div style={{ padding: '40px 48px', maxWidth: 760, lineHeight: 1.85, fontSize: 15, color: 'var(--text)' }}>
      <h2 style={headingStyle}>{t.essayHeading}</h2>
      <div style={{ textAlign: 'justify' }}>{segments}</div>

      {refCitations.length > 0 && (
        <div style={{ marginTop: 48, paddingTop: 32, borderTop: '1px solid var(--border)' }}>
          <h2 style={headingStyle}>{t.referencesHeading}</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {refCitations.map((cit) => {
              const isActive = activeCitId === cit.id
              const isHovered = hoveredCitId === cit.id
              const highlighted = isActive || isHovered
              return (
                <div
                  key={cit.id}
                  data-cit-id={cit.id}
                  onClick={() => onCitClick(cit.id)}
                  onMouseEnter={() => onCitHover(cit.id)}
                  onMouseLeave={() => onCitHover(null)}
                  style={{
                    padding: '10px 16px',
                    borderRadius: 6,
                    borderLeft: `3px solid ${highlighted ? BORDER_COLOR[cit.status] : 'var(--border)'}`,
                    background: highlighted ? BG_COLOR[cit.status] : 'transparent',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                    fontSize: 14,
                    lineHeight: 1.7,
                    outline: isActive ? `1.5px solid ${BORDER_COLOR[cit.status]}` : 'none',
                  }}
                >
                  {cit.raw_text}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function TextBlock({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <>
      {lines.map((line, i) => (
        <span key={i}>
          {line}
          {i < lines.length - 1 && <>{'\n'}{line.trim() === '' && <br />}</>}
        </span>
      ))}
    </>
  )
}

const headingStyle: React.CSSProperties = {
  fontSize: 13, fontWeight: 600, color: 'var(--text-3)',
  letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 24,
}
