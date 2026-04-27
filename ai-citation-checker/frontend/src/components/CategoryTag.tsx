type Category = 'content' | 'format' | 'orphan' | 'ambiguous'

const MAP: Record<Category, { label: string; color: string; bg: string }> = {
  content:   { label: '内容',   color: 'var(--amber)',              bg: 'oklch(96% 0.04 75)'  },
  format:    { label: '格式',   color: 'oklch(48% 0.14 280)',       bg: 'oklch(95% 0.04 280)' },
  orphan:    { label: '孤立引用', color: 'oklch(50% 0.14 310)',     bg: 'oklch(96% 0.04 310)' },
  ambiguous: { label: '模糊引用', color: 'oklch(52% 0.14 55)',      bg: 'oklch(96% 0.04 55)'  },
}

export default function CategoryTag({ category }: { category: string }) {
  const t = MAP[category as Category] ?? MAP.content
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 600,
        padding: '1px 6px',
        borderRadius: 99,
        background: t.bg,
        color: t.color,
        fontFamily: 'var(--mono)',
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
        whiteSpace: 'nowrap',
      }}
    >
      {t.label}
    </span>
  )
}
