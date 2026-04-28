import { useT } from '../i18n'

type Status = 'pass' | 'warning' | 'error'

const COLORS: Record<Status, { color: string; bg: string }> = {
  pass:    { color: 'var(--green)', bg: 'var(--green-bg)' },
  warning: { color: 'var(--amber)', bg: 'var(--amber-bg)' },
  error:   { color: 'var(--red)',   bg: 'var(--red-bg)'   },
}

export default function StatusBadge({ status }: { status: Status }) {
  const { t } = useT()
  const { color, bg } = COLORS[status]
  const label = { pass: t.pass, warning: t.warning, error: t.error }[status]
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', borderRadius: 99,
        background: bg, color,
        fontSize: 11, fontWeight: 600, fontFamily: 'var(--mono)', whiteSpace: 'nowrap',
      }}
    >
      ● {label}
    </span>
  )
}
