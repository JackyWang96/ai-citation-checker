/**
 * Render reference text with italics preserved from the source document.
 * Falls back to the plain `text` string when no run-level info is available
 * (e.g. in-text citations, or older reports that predate the `runs` field).
 */
export interface TextRun {
  text: string
  italic: boolean
}

interface Props {
  text: string
  runs?: TextRun[] | null
}

export default function RichText({ text, runs }: Props) {
  if (!runs || runs.length === 0) return <>{text}</>
  return (
    <>
      {runs.map((r, i) =>
        r.italic ? (
          <em key={i} style={{ fontStyle: 'italic' }}>{r.text}</em>
        ) : (
          <span key={i}>{r.text}</span>
        ),
      )}
    </>
  )
}
