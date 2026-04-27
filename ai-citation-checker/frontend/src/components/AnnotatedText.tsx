import Citation from './Citation'

interface CitationData {
  id: string
  kind: string
  raw_text: string
  char_start: number
  char_end: number
  status: 'pass' | 'warning' | 'error'
  issues: Issue[]
}

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

interface Props {
  fullText: string
  citations: CitationData[]
  activeCitationId: string | null
  onCitationClick: (id: string) => void
}

export default function AnnotatedText({ fullText, citations, activeCitationId, onCitationClick }: Props) {
  const sorted = [...citations].sort((a, b) => a.char_start - b.char_start)
  const segments: React.ReactNode[] = []
  let cursor = 0

  for (const c of sorted) {
    if (c.char_start > cursor) {
      segments.push(
        <span key={`txt-${cursor}`}>{fullText.slice(cursor, c.char_start)}</span>
      )
    }
    segments.push(
      <Citation
        key={c.id}
        id={c.id}
        status={c.status}
        text={fullText.slice(c.char_start, c.char_end) || c.raw_text}
        issues={c.issues}
        onClick={onCitationClick}
        highlighted={activeCitationId === c.id}
      />
    )
    cursor = c.char_end
  }

  if (cursor < fullText.length) {
    segments.push(<span key="txt-end">{fullText.slice(cursor)}</span>)
  }

  return (
    <div className="whitespace-pre-wrap leading-7 text-sm text-gray-800 font-serif">
      {segments}
    </div>
  )
}
