import { useT } from '../i18n'

const STYLES: Record<string, { color: string; bg: string }> = {
  content:   { color: 'var(--amber)',        bg: 'oklch(96% 0.04 75)'  },
  format:    { color: 'oklch(48% 0.14 280)', bg: 'oklch(95% 0.04 280)' },
  orphan:    { color: 'oklch(50% 0.14 310)', bg: 'oklch(96% 0.04 310)' },
  ambiguous: { color: 'oklch(52% 0.14 55)',  bg: 'oklch(96% 0.04 55)'  },
}

export default function CategoryTag({ category }: { category: string }) {
  const { t } = useT()
  const style = STYLES[category] ?? STYLES.content
  const labels: Record<string, string> = {
    content: t.catContent, format: t.catFormat, orphan: t.catOrphan, ambiguous: t.catAmbiguous,
  }
  return (
    <span
      style={{
        fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 99,
        background: style.bg, color: style.color,
        fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap',
      }}
    >
      {labels[category] ?? category}
    </span>
  )
}
